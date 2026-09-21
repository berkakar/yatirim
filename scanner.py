import yfinance as yf
import pandas as pd
import numpy as np
import streamlit as st
from scipy.signal import argrelextrema

from structure import Bar
from yf_data_quality import is_ohlc_consistent, is_fresh, has_implausible_daily_move, has_flat_prices


def bars_from_df(df: pd.DataFrame) -> list[Bar]:
    """get_scanner_data'nın döndürdüğü OHLCV DataFrame'i, buy_algorithms.py'deki
    (Alpaca/BackTest ile paylaşılan) algoritmaların beklediği Bar listesine
    çevirir - böylece aynı algoritmalar yfinance verisi üzerinde de çalışabilir."""
    return [
        Bar(t=row.Date.isoformat(), o=float(row.Open), h=float(row.High), l=float(row.Low),
            c=float(row.Close), v=float(row.Volume))
        for row in df.itertuples(index=False)
    ]

# ------------------------------------------------------------------------------
# FORMASYON TESPİT FONKSİYONLARI (Kendi mevcut algoritmalarınızı buraya koyun)
# ------------------------------------------------------------------------------
def _find_pivots(df, order=10):
    """Yerel tepe (High pivot) ve dip (Low pivot) noktalarını kronolojik sırada döner."""
    prices = df['Close'].values
    high_idx = argrelextrema(prices, np.greater_equal, order=order)[0]
    low_idx = argrelextrema(prices, np.less_equal, order=order)[0]
    highs = df.iloc[high_idx].reset_index(drop=True)
    lows = df.iloc[low_idx].reset_index(drop=True)
    return highs, lows


def detect_cup_and_handle(df, order=10, symmetry_threshold=0.05, depth_threshold=0.2, handle_window_days=30, max_age_days=5):
    """
    Klasik fincan-kulp formasyonu: A (sol tepe) -> B (fincan dibi) -> C (sağ tepe,
    A'ya yakın seviyede) -> D (C'den sonraki ~30 gün içindeki kulp dibi, fincanın
    alt yarısını aşmayan sığ bir geri çekilme). D (formasyonun en güncel noktası)
    veri setindeki son günden en fazla max_age_days gün önce oluşmuş olmalı - aksi
    halde formasyon güncel sayılmaz. Aralıktaki en güncel geçerli formasyonu
    {'A','B','C','D'} dict olarak döner, bulunamazsa None.
    """
    highs, lows = _find_pivots(df, order)

    if len(highs) < 2 or lows.empty:
        return None

    latest_date = pd.Timestamp(df['Date'].iloc[-1])
    found = None
    for i in range(len(highs) - 1):
        peak_A, peak_C = highs.iloc[i], highs.iloc[i + 1]
        date_A, date_C = pd.Timestamp(peak_A['Date']), pd.Timestamp(peak_C['Date'])
        price_A, price_C = float(peak_A['Close']), float(peak_C['Close'])
        if price_A <= 0:
            continue

        cup_lows = lows[(lows['Date'] > date_A) & (lows['Date'] < date_C)]
        if cup_lows.empty:
            continue
        dip_B = cup_lows.loc[cup_lows['Close'].idxmin()]
        price_B = float(dip_B['Close'])

        if abs(price_A - price_C) / price_A > symmetry_threshold:
            continue
        if (price_A - price_B) / price_A < depth_threshold:
            continue

        handle_lows = lows[(lows['Date'] > date_C) & (lows['Date'] <= date_C + pd.Timedelta(days=handle_window_days))]
        if handle_lows.empty:
            continue
        dip_D = handle_lows.loc[handle_lows['Close'].idxmin()]
        price_D = float(dip_D['Close'])

        mid_depth = price_A - (price_A - price_B) * 0.5
        if not (mid_depth < price_D < price_C):
            continue

        # Kulp dibi (D) güncel olmalı - eski (tamamlanmış) bir formasyonu
        # tarayıcıda göstermenin bir anlamı yok
        age_days = (latest_date - pd.Timestamp(dip_D['Date'])).days
        if age_days > max_age_days:
            continue

        found = {'A': peak_A, 'B': dip_B, 'C': peak_C, 'D': dip_D}

    return found

def _find_shoulder_head_pattern(pivots, want_max, symmetry_threshold, min_head_prominence,
                                 latest_date, max_age_days):
    """
    Ardışık 3 pivottan (sol omuz, baş, sağ omuz) oluşan formasyonu arar.
    want_max=True  -> baş, omuzlardan yüksek olmalı (OBO / tepe dönüş formasyonu).
    want_max=False -> baş, omuzlardan düşük olmalı (TOBO / dip dönüş formasyonu).
    Sağ omuz, latest_date'ten en fazla max_age_days gün önce oluşmuş olmalı - aksi
    halde formasyon güncel sayılmaz. Aralıktaki en güncel geçerli formasyonu döner,
    yoksa None.
    """
    if len(pivots) < 3:
        return None

    found = None
    for i in range(len(pivots) - 2):
        ls, head, rs = pivots.iloc[i], pivots.iloc[i + 1], pivots.iloc[i + 2]
        p_ls, p_head, p_rs = float(ls['Close']), float(head['Close']), float(rs['Close'])
        if p_ls <= 0:
            continue

        if want_max:
            if not (p_head > p_ls and p_head > p_rs):
                continue
        else:
            if not (p_head < p_ls and p_head < p_rs):
                continue

        # Omuzlar birbirine yakın seviyede olmalı
        if abs(p_ls - p_rs) / p_ls > symmetry_threshold:
            continue

        # Baş, omuzların ortalamasından belirgin şekilde ayrışmalı (aksi halde üçü de
        # aynı seviyede "üçlü tepe/dip" olur, omuz-baş-omuz değil)
        avg_shoulder = (p_ls + p_rs) / 2
        if avg_shoulder <= 0 or abs(p_head - avg_shoulder) / avg_shoulder < min_head_prominence:
            continue

        # Sağ omuz güncel olmalı - eski (tamamlanmış, artık aksiyon alınamayacak) bir
        # formasyonu tarayıcıda göstermenin bir anlamı yok
        age_days = (latest_date - pd.Timestamp(rs['Date'])).days
        if age_days > max_age_days:
            continue

        found = {'left_shoulder': ls, 'head': head, 'right_shoulder': rs}

    return found


def detect_obo(df, order=10, symmetry_threshold=0.1, min_head_prominence=0.15, max_age_days=5):
    """
    Omuz-Baş-Omuz (Head & Shoulders) - tepe/dönüş formasyonu. Üç ardışık tepe pivotu;
    ortadaki (baş) diğer ikisinden (omuzlar) belirgin şekilde yüksek, omuzlar ise
    birbirine yakın seviyede olmalı; sağ omuz en fazla max_age_days gün önce oluşmuş
    olmalı. Bulunursa {'left_shoulder','head','right_shoulder'} dict döner, aksi
    halde None.
    """
    highs, _ = _find_pivots(df, order)
    latest_date = pd.Timestamp(df['Date'].iloc[-1])
    return _find_shoulder_head_pattern(highs, True, symmetry_threshold, min_head_prominence,
                                        latest_date, max_age_days)


def detect_tobo(df, order=10, symmetry_threshold=0.1, min_head_prominence=0.15, max_age_days=5):
    """
    Ters Omuz-Baş-Omuz (Inverse Head & Shoulders) - dip/dönüş formasyonu. Üç ardışık
    dip pivotu; ortadaki (baş) diğer ikisinden belirgin şekilde düşük, omuzlar ise
    birbirine yakın seviyede olmalı; sağ omuz en fazla max_age_days gün önce oluşmuş
    olmalı. Bulunursa {'left_shoulder','head','right_shoulder'} dict döner, aksi
    halde None.
    """
    _, lows = _find_pivots(df, order)
    latest_date = pd.Timestamp(df['Date'].iloc[-1])
    return _find_shoulder_head_pattern(lows, False, symmetry_threshold, min_head_prominence,
                                        latest_date, max_age_days)


# ------------------------------------------------------------------------------
# APP.PY'NİN BEKLEDİĞİ ANA FONKSİYON (ÖNBELLEKLİ VE GÜVENLİ)
# ------------------------------------------------------------------------------
# BackTest modülündeki (backtest.py) TIMEFRAME_LABELS ile aynı etiketler -
# uygulama genelinde tutarlılık için. Burada ayrıca tanımlanır (import
# edilmez) çünkü backtest.py çok daha ağır bağımlılıklar (Alpaca client vb.)
# sürükler ve scanner.py'nin bunlara ihtiyacı yok.
SCAN_TIMEFRAMES = ["15Min", "30Min", "1Hour", "1Day"]
SCAN_TIMEFRAME_LABELS = {"15Min": "15 Dakika", "30Min": "30 Dakika", "1Hour": "1 Saat", "1Day": "1 Gün"}

# Her mum periyodu için yfinance interval kodu. Kaç gün geriye gidileceği
# artık sabit değil - kullanıcı app.py'deki iki text box'tan giriyor
# (gün-içi periyotlar - 15dk/30dk/1sa - için ortak bir değer, günlük için
# ayrı bir değer). Yahoo tarafındaki gerçek üst sınırlar: gün-içi ~60 gün,
# günlük pratikte ~730 gün - varsayılan/maksimumlar buna göre seçildi.
#
# "4Hour" bilinçli olarak SCAN_TIMEFRAMES'e eklenmedi - yfinance native
# olarak 4 saatlik interval desteklemiyor (izin verilen aralıklar: 1m/2m/
# 5m/15m/30m/60m/90m/1d/...), bu yüzden 1 saatlik barlar çekilip
# _resample_to_4h ile sentetik 4 saatlik bara dönüştürülüyor (bkz.
# get_scanner_data). SCAN_TIMEFRAMES tüm tarama modüllerinde (app.py'deki
# Alım Bölgesi Tarama dahil) paylaşıldığı için buraya eklenirse istenmeyen
# yerlerde de çıkar; ihtiyacı olan modül (bkz. bicak_kanali_test.py)
# "4Hour" değerini get_scanner_data'ya doğrudan geçerek kullanır.
_YF_INTERVALS = {"15Min": "15m", "30Min": "30m", "1Hour": "1h", "4Hour": "1h", "1Day": "1d"}
INTRADAY_DEFAULT_DAYS = 15
INTRADAY_MAX_DAYS = 60
DAILY_DEFAULT_DAYS = 365
DAILY_MAX_DAYS = 730
FOUR_HOUR_DEFAULT_DAYS = 180
FOUR_HOUR_MAX_DAYS = 730  # 1 saatlik barların yfinance'de izin verilen üst sınırıyla aynı (resample kaynağı bu)


def _fetch_yf_ohlcv(ticker_symbol, period, interval, min_rows=60):
    """get_scanner_data ve fetch_daily_pairs'in ortak veri çekme/temizleme
    mantığı: BIST .IS fallback'i, sütun normalizasyonu, saat dilimi
    temizliği. Temiz bir OHLCV DataFrame döner, yetersiz/boş/bayat/anlamsız
    veride None (bkz. yf_data_quality - güncellik ve iç tutarlılık
    kontrolleri)."""
    formatted_ticker = ticker_symbol
    resolved_ticker = formatted_ticker

    ticker_obj = yf.Ticker(formatted_ticker)
    df = ticker_obj.history(period=period, interval=interval)

    # Eğer veri gelmediyse BIST hissesi olma ihtimaline karşı .IS ekleyip tekrar dene
    if df is None or df.empty or len(df) < min_rows:
        if not formatted_ticker.endswith(".IS"):
            resolved_ticker = f"{formatted_ticker}.IS"
            ticker_obj = yf.Ticker(resolved_ticker)
            df = ticker_obj.history(period=period, interval=interval)

    if df is None or df.empty or len(df) < min_rows:
        return None

    # Yahoo'nun döndürdüğü barların iç tutarlılığını (High/Low/Open/Close),
    # BIST sembollerinde günlük taban/tavan marjını aşan sıçramaları (bozuk
    # tick) ve işlem görmeyen/durdurulmuş bir sembol için bayat/tekrarlanan
    # fiyat döndürülüp döndürülmediğini doğrula - anlamsız veriyle
    # tarama/sinyal üretmemek için.
    if (
        not is_ohlc_consistent(df)
        or has_implausible_daily_move(df['Close'], resolved_ticker)
        or has_flat_prices(df['Close'])
    ):
        return None

    # Indeks olan 'Date'/'Datetime' sütununu normal bir 'Date' sütununa çevir
    # (günlük periyotta index adı 'Date', gün-içi periyotlarda 'Datetime' olur)
    df = df.reset_index()
    df = df.rename(columns={df.columns[0]: "Date"})

    # Sütun isimlerini standartlaştır (Date, Open, High, Low, Close, Volume)
    df.columns = [str(col).capitalize() for col in df.columns]

    required_cols = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
    if not all(col in df.columns for col in required_cols):
        return None

    # Tarih formatını düzelt ve saat dilimini temizle
    df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize(None)

    # Kapanış fiyatı eksik olan satırları sil
    df = df.dropna(subset=['Close'])

    if len(df) < min_rows:
        return None

    # Son bar beklenenden fazla eskiyse (Yahoo'dan bayat/önbelleklenmiş bir
    # yanıt geldiyse) veriyi güncel kabul etme.
    if not is_fresh(df['Date'].iloc[-1].date()):
        return None

    return df


def _resample_to_4h(df: pd.DataFrame) -> pd.DataFrame | None:
    """1 saatlik barları takvim saatine göre 4 saatlik gruplara toplayarak
    sentetik 4 saatlik bar üretir (bkz. get_scanner_data - yfinance native
    olarak 4h interval desteklemediği için 1h barlar buradan türetiliyor).
    Gruplar takvim saatine hizalanır (00:00, 04:00, 08:00, ...), gerçek
    borsa seans açılış saatine göre değil."""
    d = df.set_index("Date")
    agg = d.resample("4h").agg({
        "Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum",
    })
    agg = agg.dropna(subset=["Open", "High", "Low", "Close"])
    if agg.empty:
        return None
    return agg.reset_index()


@st.cache_data(ttl=1800)  # Verileri 30 dakika hafızada tutar, Yahoo engeline takılmaz
def get_scanner_data(ticker_symbol, timeframe="1Day", period_days=None):
    """
    app.py tarafından çağrılan ana fonksiyon.
    Veriyi çeker, temizler ve formasyon analizlerini yapar.
    timeframe: SCAN_TIMEFRAMES içindeki değerlerden biri ("15Min", "30Min",
    "1Hour", "1Day") veya (SCAN_TIMEFRAMES'e dahil olmayan, sadece Bıçak
    Kanalı modülünün kullandığı) "4Hour" - bu durumda 1 saatlik barlar
    çekilip _resample_to_4h ile 4 saatliğe dönüştürülür.
    period_days: Kaç gün geriye gidileceği. None ise timeframe'e göre varsayılan
    kullanılır; her durumda ilgili maksimuma (gün-içi 60, 4 saatlik 730,
    günlük 730) sıkıştırılır.
    """
    try:
        interval = _YF_INTERVALS.get(timeframe, _YF_INTERVALS["1Day"])
        if timeframe == "1Day":
            days = DAILY_DEFAULT_DAYS if period_days is None else int(period_days)
            days = max(1, min(days, DAILY_MAX_DAYS))
        elif timeframe == "4Hour":
            days = FOUR_HOUR_DEFAULT_DAYS if period_days is None else int(period_days)
            days = max(1, min(days, FOUR_HOUR_MAX_DAYS))
        else:
            days = INTRADAY_DEFAULT_DAYS if period_days is None else int(period_days)
            days = max(1, min(days, INTRADAY_MAX_DAYS))

        df = _fetch_yf_ohlcv(ticker_symbol, f"{days}d", interval)
        if df is None:
            return None, None, None, None

        if timeframe == "4Hour":
            df = _resample_to_4h(df)
            if df is None or len(df) < 60:
                return None, None, None, None

        # --- FORMASYON ANALİZLERİ ---
        cup_pattern = detect_cup_and_handle(df)
        obo_pattern = detect_obo(df)
        tobo_pattern = detect_tobo(df)

        return df, cup_pattern, obo_pattern, tobo_pattern

    except Exception as e:
        return None, None, None, None


@st.cache_data(ttl=1800)
def fetch_daily_pairs(ticker_symbol):
    """Trend/SMA200 filtreleri (buy_algorithms.trend_pullback, backtest_engine'deki
    EMA trend filtresi) için gereken [(tarih, kapanış), ...] listesini döner - bu
    filtreler yüzlerce günlük bağlam istediğinden (bkz. backtest.py'deki
    DAILY_TREND_LOOKBACK_DAYS), tarama/backtest gün sayısı üst sınırlarından
    (DAILY_MAX_DAYS) bağımsız olarak elde olan tüm günlük geçmiş ("max") çekilir.
    Tarihe göre artan sırada döner, veri yoksa boş liste."""
    df = _fetch_yf_ohlcv(ticker_symbol, "max", "1d", min_rows=1)
    if df is None:
        return []
    pairs = [(row.Date.date(), float(row.Close)) for row in df.itertuples(index=False)]
    pairs.sort(key=lambda p: p[0])
    return pairs