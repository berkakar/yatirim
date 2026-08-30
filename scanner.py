import yfinance as yf
import pandas as pd
import numpy as np
import streamlit as st
from scipy.signal import argrelextrema

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


def detect_cup_and_handle(df, order=10, symmetry_threshold=0.05, depth_threshold=0.2, handle_window_days=30):
    """
    Klasik fincan-kulp formasyonu: A (sol tepe) -> B (fincan dibi) -> C (sağ tepe,
    A'ya yakın seviyede) -> D (C'den sonraki ~30 gün içindeki kulp dibi, fincanın
    alt yarısını aşmayan sığ bir geri çekilme). Aralıktaki en güncel geçerli
    formasyonu {'A','B','C','D'} dict olarak döner, bulunamazsa None.
    """
    highs, lows = _find_pivots(df, order)

    if len(highs) < 2 or lows.empty:
        return None

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
        if mid_depth < price_D < price_C:
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
@st.cache_data(ttl=1800)  # Verileri 30 dakika hafızada tutar, Yahoo engeline takılmaz
def get_scanner_data(ticker_symbol):
    """
    app.py tarafından çağrılan ana fonksiyon.
    Veriyi çeker, temizler ve formasyon analizlerini yapar.
    """
    try:
        # BIST hisseleri için otomatik .IS kontrolü
        formatted_ticker = ticker_symbol
        
        # yfinance ile veriyi çek (history kullanımı download'a göre çok daha kararlıdır)
        ticker_obj = yf.Ticker(formatted_ticker)
        df = ticker_obj.history(period="1y", interval="1d")
        
        # Eğer veri gelmediyse BIST hissesi olma ihtimaline karşı .IS ekleyip tekrar dene
        if df is None or df.empty or len(df) < 60:
            if not formatted_ticker.endswith(".IS"):
                ticker_obj = yf.Ticker(f"{formatted_ticker}.IS")
                df = ticker_obj.history(period="1y", interval="1d")

        # Veri hala boşsa veya yetersizse None dön
        if df is None or df.empty or len(df) < 60:
            return None, None, None, None

        # Indeks olan 'Date' sütununu normal sütun yap
        df = df.reset_index()
        
        # Sütun isimlerini standartlaştır (Date, Open, High, Low, Close, Volume)
        df.columns = [str(col).capitalize() for col in df.columns]

        required_cols = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
        if not all(col in df.columns for col in required_cols):
            return None, None, None, None

        # Tarih formatını düzelt ve saat dilimini temizle
        df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize(None)
        
        # Kapanış fiyatı eksik olan satırları sil
        df = df.dropna(subset=['Close'])

        if len(df) < 60:
            return None, None, None, None

        # --- FORMASYON ANALİZLERİ ---
        cup_pattern = detect_cup_and_handle(df)
        obo_pattern = detect_obo(df)
        tobo_pattern = detect_tobo(df)

        return df, cup_pattern, obo_pattern, tobo_pattern

    except Exception as e:
        return None, None, None, None