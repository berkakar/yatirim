"""
Yapay Zeka Analiz Modülü - NASDAQ 100'den seçilen bir hisse için transformer
(zaman serisi) eğitiminde kullanılacak günlük veri setini hazırlar ve
uygulama veritabanına (storage.db_path(); YATIRIM_DB_PATH) kaydeder.

Adımlar (build_dataset):
    1. Yahoo'dan en az 3 yıllık günlük açılış / yüksek / düşük / kapanış / hacim.
       VWAP: Alpaca günlük barlarının `vw` alanı (kullanıcının anahtarı varsa);
       yoksa / eksik günlerde tipik fiyat (Y+D+K)/3 yaklaşığı - `vwap_is_proxy`=1.
    2. Hisse duyarlılığı (0-100): Piyasa Duyarlılığı ile aynı yöntemle hisse
       bazında - 50 günlük ortalamaya göre momentum ve oynaklığın yüzdelik
       sırası + RSI(14). Geçmiş haber duyarlılığı ücretsiz kaynaklarda olmadığı
       için fiyat temellidir; NASDAQ 100 piyasa duyarlılığı 6. adımda eklenir.
    3. EMA20 / EMA50 / EMA200 ve kapanışın bunlara % uzaklığı.
    4. Gün başına 1 / 2 / 3 aylık (21 / 42 / 63 işlem günü geriye) direnç:
       penceredeki onaylanmış tepe noktaları (pivot) ve pencerenin en yükseği
       içinden kapanışın üstündeki en yakını; üstte seviye yoksa (kırılım)
       pencerenin en yükseği. Üç seviyeden fiyata en yakını ve % uzaklığı.
       Yalnızca o güne kadar bilinen barlar kullanılır (pivot, sağında
       PIVOT_K bar oluştuktan sonra görülür) - geleceğe sızıntı yok.
    5. 3 yıllık hesaplamadan son 2 yıl alınır (ilk yıl EMA200 / yüzdelik sıra
       ısınması içindir). Değerleme & Ucuzluk Skoru ve oranları eklenir: her
       güne, o gün veya öncesindeki son günlük skor (valuation_scores_daily).
       Servisin yazmadığı geçmiş günler önce bilanço tablolarından yeniden
       hesaplanır (valuation_history.py; `valuation_is_reconstructed`=1);
       geçmişin başlangıcından önceki günlere en eski bilinen skor.
    6. Günlük arşivle birleştirme (market_archive): NASDAQ 100 duyarlılık
       parametreleri (nasdaq_100__*), 11 sektör ETF'si + SPY (<etf>__*) ve
       hissenin kendi sektör ETF'si (sector_etf__*).
    7. Temporal embedding için takvim özellikleri (ham indeks + sin/cos).
    8. Boş hücreler zamana göre doğrusal interpolasyonla doldurulur; serinin
       sonundaki boşluk ileri taşınır (geleceği bilemeyiz), başındaki geri.
       Her satırda kaç hücrenin doldurulduğu `interpolated_cells` sütunundadır.

Veritabanı:
    ai_datasets        (ticker)        parametreler, sütun sırası, son durum
    ai_dataset_daily   (ticker, date)  günün özellikleri (JSON - yeni özellik
                                       eklenince tablo değişmez), kaynak,
                                       kullanıcının elle eklediği `extra` alanlar

Satır kaynakları: `backfill` (ilk hazırlama / yeniden oluşturma), `daily`
(update ile sonradan eklenen gün). Yeniden oluşturma `extra` alanlara
dokunmaz; set_extra() ile güne istenen değer eklenir.

Eğitimde dikkat:
- Değerleme skorunun günlük geçmişi servis çalıştıkça birikir (Yahoo geçmiş
  temel veriyi vermez). Geçmişin başlangıcından önceki günlere en eski bilinen
  skor yazılır ve `valuation_is_snapshot`=1 ile işaretlenir; bu günlerde skor
  gerçekte o gün bilinmiyordu (sızıntı) - eğitimde ayıklanabilir.
- İç boşluklarda interpolasyon sonraki bilinen değeri kullanır; arşiv boşluğu
  çoksa `interpolated_cells` ile o satırları ayıklayın.
- Değerler gün kapanışıyla hesaplanır; ertesi günü tahmin ederken hedef bir gün
  ileri kaydırılmalıdır.

Kullanım:
    python ai_dataset.py build --ticker AAPL
    python ai_dataset.py update --all            # kayıtlı setlere yeni günler
    python ai_dataset.py export --ticker AAPL --out data/ml
    python ai_dataset.py status
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import storage

MARKET = "NASDAQ 100"
MARKET_PREFIX = "nasdaq_100"
DATASET_VERSION = 1
DEFAULT_FETCH_YEARS = 3
DEFAULT_KEEP_YEARS = 2

SOURCE_BACKFILL = "backfill"
SOURCE_DAILY = "daily"

EMA_PERIODS = (20, 50, 200)
# Ay = 21 işlem günü.
RESISTANCE_WINDOWS = {1: 21, 2: 42, 3: 63}
PIVOT_K = 2                  # tepe: iki yanındaki PIVOT_K barın yükseğinden düşük değil
SENTIMENT_SMA = 50
SENTIMENT_PERCENTILE_WINDOW = 126
RSI_PERIOD = 14

# Yahoo `sector` -> SPDR sektör ETF'si (market_sentiment.SECTOR_ETFS).
YAHOO_SECTOR_TO_ETF = {
    "Technology": "XLK",
    "Communication Services": "XLC",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Financial Services": "XLF",
    "Healthcare": "XLV",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}

VALUATION_EXCLUDED_KEY = "_excluded"  # valuation.EXCLUDED_KEY (streamlit'e bağımlı modülü yüklememek için)

# Arşivden alınmayan sütunlar: put/call yalnızca canlı satırlarda var (geçmişi
# yok, interpolasyonla uydurulmamalı), universe_size özellik değil.
_MARKET_SKIP = ("put_call_ratio", "put_call_score")

# Interpolasyona girmeyen sütunlar (bayrak / takvim / sayaç).
_NO_FILL = ("vwap_is_proxy", "valuation_is_snapshot", "valuation_is_reconstructed", "resistance_nearest_window",
            "interpolated_cells")

DAILY_TABLE = "ai_dataset_daily"
META_TABLE = "ai_datasets"

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {META_TABLE} (
    ticker          TEXT PRIMARY KEY,
    market          TEXT NOT NULL,
    params          TEXT NOT NULL,
    meta            TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS {DAILY_TABLE} (
    ticker          TEXT NOT NULL,
    date            TEXT NOT NULL,
    data            TEXT NOT NULL,
    extra           TEXT,
    source          TEXT NOT NULL,
    dataset_version INTEGER NOT NULL,
    recorded_at     TEXT NOT NULL,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS {DAILY_TABLE}_date ON {DAILY_TABLE} (date);
"""

_initialized_paths = set()


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_ticker(ticker) -> str:
    return str(ticker or "").strip().upper()


# ------------------------------------------------------------------------------
# Veri çekme
# ------------------------------------------------------------------------------

def download_ohlcv(ticker: str, start: str, end: str | None = None) -> pd.DataFrame:
    """Yahoo günlük barları (bölünme / temettü düzeltmeli): open, high, low,
    close, volume - indeks saat dilimsiz gün."""
    import yfinance as yf

    data = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=True)
    if data is None or data.empty:
        raise RuntimeError(f"{ticker}: Yahoo'dan günlük veri alınamadı")
    data = data.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    data.index = pd.to_datetime(data.index).tz_localize(None).normalize()
    data = data[~data.index.duplicated(keep="last")].sort_index()
    return data.dropna(subset=["close"])


def alpaca_vwap_fetcher(client):
    """Alpaca istemcisinden (ticker, start) -> günlük VWAP serisi üreten
    fonksiyon. Fiyatlar Yahoo ile aynı ölçekte olsun diye bölünme + temettü
    düzeltmeli (adjustment=all) istenir."""
    def fetch(ticker: str, start: str) -> pd.Series:
        bars = client.get_raw_bars_multi([ticker], "1Day", f"{start}T00:00:00Z", adjustment="all").get(ticker) or []
        if not bars:
            return pd.Series(dtype=float)
        idx = (pd.to_datetime([b["t"] for b in bars], utc=True).tz_convert("America/New_York")
               .tz_localize(None).normalize())
        s = pd.Series([b.get("vw") for b in bars], index=idx, dtype=float)
        return s[~s.index.duplicated(keep="last")]
    return fetch


# ------------------------------------------------------------------------------
# Hesaplama (saf fonksiyonlar)
# ------------------------------------------------------------------------------

def add_vwap(df: pd.DataFrame, vwap: pd.Series | None = None) -> pd.DataFrame:
    """vwap sütunu: verilen seri, eksik günlerde tipik fiyat (Y+D+K)/3."""
    out = df.copy()
    typical = (out["high"] + out["low"] + out["close"]) / 3
    real = vwap.reindex(out.index) if vwap is not None and len(vwap) else pd.Series(np.nan, index=out.index)
    real = real.where(real > 0)
    out["vwap"] = real.fillna(typical)
    out["vwap_is_proxy"] = real.isna().astype(int)
    return out


def add_emas(df: pd.DataFrame, periods=EMA_PERIODS) -> pd.DataFrame:
    out = df.copy()
    for p in periods:
        ema = out["close"].ewm(span=p, adjust=False, min_periods=p).mean()
        out[f"ema{p}"] = ema
        out[f"dist_ema{p}_pct"] = (out["close"] / ema - 1) * 100
    out["ret_1d_pct"] = out["close"].pct_change() * 100
    return out


def rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Wilder RSI (0-100)."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).where(loss > 0, 100.0).where(gain.notna())


def add_stock_sentiment(df: pd.DataFrame) -> pd.DataFrame:
    """Hisse duyarlılığı (0-100): momentum ve oynaklık bileşenleri Piyasa
    Duyarlılığı'ndaki gibi yüzdelik sıradır (oynaklık ters çevrilir) + RSI.
    En az iki bileşen varsa ortalama."""
    import market_sentiment as ms

    out = df.copy()
    close = out["close"]
    sma = close.rolling(SENTIMENT_SMA, min_periods=SENTIMENT_SMA).mean()
    out["sent_momentum_raw"] = (close / sma - 1) * 100
    out["sent_momentum"] = ms.rolling_percentile(out["sent_momentum_raw"], SENTIMENT_PERCENTILE_WINDOW)
    vol = ms.realized_volatility(close)
    out["sent_volatility_raw"] = vol
    vol_vs_avg = vol / vol.rolling(50, min_periods=50).mean() - 1
    out["sent_volatility"] = 100 - ms.rolling_percentile(vol_vs_avg, SENTIMENT_PERCENTILE_WINDOW)
    out["rsi14"] = rsi(close)
    parts = out[["sent_momentum", "sent_volatility", "rsi14"]]
    out["stock_sentiment"] = parts.mean(axis=1).where(parts.notna().sum(axis=1) >= 2)
    return out


def pivot_high_mask(high: np.ndarray, k: int = PIVOT_K) -> np.ndarray:
    """i. bar, [i-k, i+k] aralığının en yükseği ise tepe (kenarlardaki k bar hariç)."""
    n = len(high)
    mask = np.zeros(n, dtype=bool)
    for i in range(k, n - k):
        window = high[i - k:i + k + 1]
        if np.isfinite(high[i]) and high[i] >= np.nanmax(window):
            mask[i] = True
    return mask


def resistance_levels(high: pd.Series, close: pd.Series, windows=None, k: int = PIVOT_K) -> pd.DataFrame:
    """Her gün t ve her pencere (ay -> işlem günü) için direnç seviyesi.

    Adaylar: [t-N, t-k] aralığındaki onaylanmış tepeler + [t-N, t-1]
    aralığının en yükseği. Kapanışın üstündeki en düşük aday seçilir; hiçbiri
    üstte değilse pencerenin en yükseği (fiyat direnci kırmış, uzaklık ≤ 0).
    Geçmişi pencereden kısa günlerde NaN. Ardından üç seviyeden kapanışa mutlak
    olarak en yakını."""
    windows = windows or RESISTANCE_WINDOWS
    h = high.to_numpy(dtype=float)
    c = close.to_numpy(dtype=float)
    pivots = pivot_high_mask(h, k)
    n = len(h)
    out = pd.DataFrame(index=close.index)
    for months, length in windows.items():
        levels = np.full(n, np.nan)
        for t in range(length, n):
            lo = t - length
            window_max = np.nanmax(h[lo:t])
            cands = h[lo:t - k + 1][pivots[lo:t - k + 1]] if t - k >= lo else np.array([])
            cands = np.append(cands, window_max)
            above = cands[cands > c[t]]
            levels[t] = above.min() if above.size else window_max
        out[f"resistance_{months}m"] = levels
        out[f"resistance_{months}m_dist_pct"] = (levels / c - 1) * 100

    dist = out[[f"resistance_{m}m_dist_pct" for m in windows]].to_numpy()
    lvl = out[[f"resistance_{m}m" for m in windows]].to_numpy()
    nearest, nearest_dist, nearest_win = (np.full(n, np.nan) for _ in range(3))
    months = list(windows)
    for t in range(n):
        row = np.abs(dist[t])
        if np.all(np.isnan(row)):
            continue
        j = int(np.nanargmin(row))
        nearest[t], nearest_dist[t], nearest_win[t] = lvl[t, j], dist[t, j], months[j]
    out["resistance_nearest"] = nearest
    out["resistance_nearest_dist_pct"] = nearest_dist
    out["resistance_nearest_window"] = nearest_win
    return out


def trim_years(df: pd.DataFrame, years: int) -> pd.DataFrame:
    """Son `years` yıl (son tarihten geriye takvim yılı)."""
    if df.empty:
        return df
    start = df.index.max() - pd.DateOffset(years=years)
    return df[df.index > start]


# Veri setindeki değerleme sütunu -> skor tablosu alanı (valuation_scores_daily.scored).
# PEG bilerek yok: Yahoo'nun analist beklentisine dayalı PEG'inin geçmişi yok
# (yeniden hesapta bugünkü değerden türetiliyor) - özellik olarak kullanılmaz.
VALUATION_FIELDS = {
    "valuation_score": "Nihai Skor",
    "valuation_sector_discount_pct": "Alt Sektör İskontosu %",
    "valuation_pe": "F/K",
    "valuation_sector_pe": "Alt Sektör Ort. F/K",
    "valuation_eps_growth_pct": "EPS Büyümesi %",
    "valuation_revenue_growth_pct": "Gelir Büyümesi %",
    "valuation_roe_pct": "Öz Sermaye Getirisi (ROE) %",
    "valuation_roa_pct": "Varlık Getirisi (ROA) %",
    "valuation_net_margin_pct": "Net Kar Marjı %",
    "valuation_gross_margin_pct": "Brüt Kar Marjı %",
    "valuation_interest_coverage": "Faiz Karşılama Oranı",
    "valuation_debt_equity": "Borç / Özsermaye",
    "valuation_debt_assets_pct": "Borç / Varlık %",
    "valuation_current_ratio": "Cari Oran",
    "valuation_quick_ratio": "Likidite Oranı",
    "valuation_asset_turnover": "Varlık Devir Hızı",
}
VALUATION_COLS = tuple(VALUATION_FIELDS)


def add_valuation(df: pd.DataFrame, valuation: dict | None, history: pd.DataFrame | None = None) -> pd.DataFrame:
    """Değerleme sütunları. Her güne, o gün veya öncesindeki son geçmiş satırı
    (as-of). Geçmişten önceki günlere en eski geçmiş satırı, geçmiş hiç yoksa
    `valuation` (bugünkü skor) yazılır; bu günler `valuation_is_snapshot`=1."""
    out = df.copy()
    if history is not None and not history.empty:
        hist = history.reindex(columns=list(VALUATION_COLS) + ["valuation_is_reconstructed"]).sort_index()
        asof = pd.merge_asof(pd.DataFrame(index=out.index), hist, left_index=True, right_index=True)
        has = pd.Series(hist.index.min() <= out.index, index=out.index)
        fallback = hist.iloc[0].to_dict()
    else:
        asof = pd.DataFrame(index=out.index, columns=list(VALUATION_COLS), dtype=float)
        has = pd.Series(False, index=out.index)
        fallback = valuation or {}
    for col in VALUATION_COLS:
        value = fallback.get(col)
        out[col] = asof[col].where(has, np.nan if value is None else value).astype(float)
    out["valuation_is_snapshot"] = (~has).astype(int)
    recon = asof["valuation_is_reconstructed"] if "valuation_is_reconstructed" in asof else 0
    out["valuation_is_reconstructed"] = pd.Series(recon, index=out.index).where(has, 0).fillna(0).astype(int)
    return out


def market_columns(frame: pd.DataFrame, sector_etf: str | None = None) -> pd.DataFrame:
    """market_archive.feature_frame çıktısından NASDAQ 100 ve sektör ETF
    sütunları + hissenin kendi sektör ETF'si `sector_etf__*` olarak."""
    import market_sentiment as ms

    if frame is None or frame.empty:
        return pd.DataFrame()
    etfs = [s.lower() for s in list(ms.SECTOR_ETFS) + [ms.SECTOR_BENCHMARK]]
    keep = []
    for col in frame.columns:
        prefix, _, feat = col.partition("__")
        if feat in _MARKET_SKIP:
            continue
        if prefix == MARKET_PREFIX or prefix in etfs:
            keep.append(col)
    out = frame[keep].apply(pd.to_numeric, errors="coerce")
    if sector_etf:
        own = sector_etf.lower()
        for col in keep:
            prefix, _, feat = col.partition("__")
            if prefix == own:
                out[f"sector_etf__{feat}"] = out[col]
    return out


def temporal_features(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Temporal embedding için: ham takvim indeksleri (öğrenilen embedding
    tabloları için - Informer/Autoformer tarzı ay, gün, haftanın günü) ve
    döngüsel sin/cos kodlaması (Time2Vec / sürekli kodlama için)."""
    idx = pd.DatetimeIndex(index)
    out = pd.DataFrame(index=idx)
    out["time_idx"] = np.arange(len(idx))
    out["year"] = idx.year
    out["month"] = idx.month
    out["day_of_month"] = idx.day
    out["day_of_week"] = idx.dayofweek
    out["day_of_year"] = idx.dayofyear
    out["week_of_year"] = idx.isocalendar().week.to_numpy().astype(int)
    out["quarter"] = idx.quarter
    out["is_month_start"] = idx.is_month_start.astype(int)
    out["is_month_end"] = idx.is_month_end.astype(int)
    days_in_month = idx.days_in_month
    for name, value, period in (
        ("dow", idx.dayofweek, 5),
        ("dom", idx.day - 1, days_in_month),
        ("month", idx.month - 1, 12),
        ("doy", idx.dayofyear - 1, 365.25),
        ("woy", out["week_of_year"].to_numpy() - 1, 52.18),
    ):
        angle = 2 * np.pi * np.asarray(value, dtype=float) / np.asarray(period, dtype=float)
        out[f"{name}_sin"] = np.sin(angle)
        out[f"{name}_cos"] = np.cos(angle)
    return out


TEMPORAL_COLS = tuple(temporal_features(pd.DatetimeIndex([pd.Timestamp("2024-01-02")])).columns)


def fill_gaps(df: pd.DataFrame, skip=()) -> tuple[pd.DataFrame, dict]:
    """Sayısal sütunlardaki boşlukları doldurur: içeride zamana göre doğrusal
    interpolasyon, sonda ileri taşıma, başta geri taşıma. Döner: (çerçeve,
    {sütun: doldurulan hücre sayısı}); satır başına sayı `interpolated_cells`."""
    out = df.copy()
    cols = [c for c in out.columns if c not in skip and pd.api.types.is_numeric_dtype(out[c])]
    before = out[cols].isna()
    filled = out[cols].interpolate(method="time", limit_area="inside").ffill().bfill()
    out[cols] = filled
    after = out[cols].isna()
    changed = before & ~after
    out["interpolated_cells"] = changed.sum(axis=1).astype(int)
    report = {c: int(n) for c, n in changed.sum().items() if n}
    return out, report


# ------------------------------------------------------------------------------
# Değerleme ve arşiv bağlantıları
# ------------------------------------------------------------------------------

def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def valuation_from_scored(scored: dict, fetched_at=None) -> dict:
    return {
        **{col: _num(scored.get(key)) for col, key in VALUATION_FIELDS.items()},
        "sector": scored.get("Ana Sektör"),
        "sub_sector": scored.get("Alt Sektör (İş Modeli)"),
        "as_of": fetched_at,
    }


def load_valuation_history(ticker: str, market: str = MARKET) -> pd.DataFrame:
    """valuation_scores_daily'deki günlük skor geçmişi - indeks gün, VALUATION_COLS
    + valuation_is_reconstructed (bilançolardan yeniden hesaplanan gün)."""
    import valuation_db

    rows = valuation_db.get_daily_history(market, ticker)
    if not rows:
        return pd.DataFrame(columns=list(VALUATION_COLS) + ["valuation_is_reconstructed"])
    df = pd.DataFrame([{
        "date": r["date"],
        **{col: _num(r["scored"].get(key)) for col, key in VALUATION_FIELDS.items()},
        "valuation_is_reconstructed": int(r["source"] == valuation_db.SOURCE_RECONSTRUCTED),
    } for r in rows])
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").astype(float)


def load_valuation(ticker: str, market: str = MARKET, fetch_missing: bool = False) -> dict | None:
    """Değerleme & Ucuzluk Skoru: önce veritabanındaki satır; yoksa ve
    fetch_missing ise Değerleme modülündeki gibi anlık çekilip kaydedilir."""
    import valuation_db

    row = valuation_db.get_rows(market, [ticker]).get(ticker)
    if row:
        if row["scored"].get(VALUATION_EXCLUDED_KEY):  # ETF vb. - skorlanmaz
            return None
        return valuation_from_scored(row["scored"], row["fetched_at"])
    if not fetch_missing:
        return None
    import valuation_service

    rows, _ = valuation_service.get_scores_for_selection(market, [ticker])
    if not rows:
        return None
    return valuation_from_scored(rows[0], rows[0].get("_fetched_at"))


def load_market_frame(start: str, end: str) -> pd.DataFrame:
    import market_archive

    return market_archive.feature_frame(start, end)


# ------------------------------------------------------------------------------
# Veri seti oluşturma
# ------------------------------------------------------------------------------

def build_dataset(ticker: str, fetch_years: int = DEFAULT_FETCH_YEARS, keep_years: int = DEFAULT_KEEP_YEARS,
                  ohlcv_fetcher=download_ohlcv, vwap_fetcher=None, valuation=None, valuation_history=None,
                  reconstruct_valuation=None, history_loader=None,
                  market_loader=load_market_frame, end=None, progress=None) -> tuple[pd.DataFrame, dict]:
    """Bir hissenin eğitim tablosu. Döner: (çerçeve - indeks tarih, meta)."""
    ticker = normalize_ticker(ticker)
    fetch_years = max(int(fetch_years), DEFAULT_FETCH_YEARS)
    keep_years = min(max(int(keep_years), 1), fetch_years - 1)
    step = progress or (lambda msg: None)
    warnings = []
    end_ts = pd.Timestamp(end or datetime.now().date())
    # Hafta sonu / tatil ve 3 yıl tam dolsun diye birkaç gün fazlası.
    start = (end_ts - pd.DateOffset(years=fetch_years) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")

    step(f"{ticker}: {fetch_years} yıllık günlük fiyat ve hacim çekiliyor")
    raw = ohlcv_fetcher(ticker, start)
    raw = raw[raw.index <= end_ts]
    if raw.empty:
        raise RuntimeError(f"{ticker}: fiyat verisi boş")
    if raw.index.min() > end_ts - pd.DateOffset(years=fetch_years) + pd.Timedelta(days=30):
        warnings.append(f"Hisse geçmişi {fetch_years} yıldan kısa (ilk gün {raw.index.min():%Y-%m-%d}); "
                        "EMA200 ve direnç seviyelerinin ilk günleri interpolasyon/taşıma ile doldurulur.")

    vwap, vwap_source = None, "typical_price"
    if vwap_fetcher is not None:
        step(f"{ticker}: Alpaca'dan günlük VWAP çekiliyor")
        try:
            vwap = vwap_fetcher(ticker, start)
            if vwap is not None and len(vwap):
                vwap_source = "alpaca"
        except Exception as e:  # anahtar / ağ hatası - tipik fiyatla devam
            warnings.append(f"Alpaca VWAP alınamadı ({e}); tipik fiyat (Y+D+K)/3 kullanıldı.")
            vwap = None

    step("EMA, hisse duyarlılığı ve direnç seviyeleri hesaplanıyor")
    df = add_vwap(raw, vwap)
    df = add_emas(df)
    df = add_stock_sentiment(df)
    df = df.join(resistance_levels(df["high"], df["close"]))
    df = trim_years(df, keep_years)

    step("Değerleme & Ucuzluk Skoru ekleniyor")
    reconstructed = None
    if reconstruct_valuation is not None:
        step(f"{ticker}: Ucuzluk Skoru geçmişi bilanço tablolarından yeniden hesaplanıyor")
        try:
            reconstructed = reconstruct_valuation(ticker, df["close"])
            valuation_history = (history_loader or load_valuation_history)(ticker)
        except Exception as e:  # Yahoo / skor yok - eldeki geçmişle devam
            warnings.append(f"Ucuzluk Skoru geçmişi yeniden hesaplanamadı: {e}")
    has_history = valuation_history is not None and not valuation_history.empty
    if valuation is None and not has_history:
        warnings.append(f"{ticker} için {MARKET} değerleme skoru bulunamadı; değerleme sütunları boş.")
    df = add_valuation(df, valuation, valuation_history)
    # Hissenin alt sektörü (iş modeli grubu) - sabit kategorik özellik, ilk sütun.
    sub_sector = (valuation or {}).get("sub_sector")
    df.insert(0, "sub_sector", sub_sector)
    snapshot_days = int(df["valuation_is_snapshot"].sum())
    if snapshot_days and (valuation is not None or has_history):
        since = valuation_history.index.min().strftime("%Y-%m-%d") if has_history else None
        warnings.append(f"Ucuzluk skoru geçmişi {'yok' if since is None else since + ' tarihinden başlıyor'}; "
                        f"{snapshot_days} güne {'bugünkü' if since is None else 'en eski bilinen'} skor yazıldı "
                        "(valuation_is_snapshot=1).")
    sector_etf = YAHOO_SECTOR_TO_ETF.get((valuation or {}).get("sector") or "")

    step("NASDAQ 100 parametreleri ve sektör ETF'leri ile birleştiriliyor")
    first, last = df.index.min().strftime("%Y-%m-%d"), df.index.max().strftime("%Y-%m-%d")
    market = market_columns(market_loader(first, last), sector_etf)
    if market.empty:
        warnings.append("Günlük arşivde (sentiment_daily / sector_etf_daily) bu aralıkta veri yok; "
                        "piyasa ve sektör sütunları eklenemedi. Arşivi doldurup yeniden oluşturun.")
    else:
        market.index = pd.to_datetime(market.index)
        coverage = market.index.intersection(df.index)
        missing_days = len(df) - len(coverage)
        if missing_days:
            warnings.append(f"Arşivde {missing_days} işlem günü eksik; bu günler interpolasyonla dolduruldu.")
        df = df.join(market, how="left")
        if sector_etf is None and valuation is not None:
            warnings.append(f"'{valuation.get('sector')}' sektörü bir ETF ile eşleşmedi; sector_etf__* yok.")

    step("Boşluklar interpolasyonla dolduruluyor, temporal özellikler ekleniyor")
    df, fill_report = fill_gaps(df, skip=_NO_FILL)
    df = df.join(temporal_features(df.index))
    df.index.name = "date"

    meta = {
        "ticker": ticker,
        "market": MARKET,
        "dataset_version": DATASET_VERSION,
        "fetch_start": raw.index.min().strftime("%Y-%m-%d"),
        "start": first,
        "end": last,
        "rows": int(len(df)),
        "columns": list(df.columns),
        "vwap_source": vwap_source,
        "valuation": valuation,
        "valuation_history_days": int(len(valuation_history)) if has_history else 0,
        "valuation_reconstruction": reconstructed,
        "sector_etf": sector_etf,
        "sub_sector": sub_sector,
        "sector": (valuation or {}).get("sector"),
        "filled": fill_report,
        "warnings": warnings,
        "built_at": _now(),
    }
    return df, meta


# ------------------------------------------------------------------------------
# Veritabanı
# ------------------------------------------------------------------------------

def _connect():
    path = storage.db_path()
    if path not in _initialized_paths:
        with storage.connection() as conn:
            conn.executescript(_SCHEMA)
        _initialized_paths.add(path)
    return storage.connection()


def _clean(v):
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return float(v) if np.isfinite(v) else None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    return v


def _row_json(rec: dict) -> str:
    return json.dumps({k: _clean(v) for k, v in rec.items()}, ensure_ascii=False)


def save_dataset(df: pd.DataFrame, meta: dict, params: dict, source: str = SOURCE_BACKFILL,
                 only_after: str | None = None, replace: bool = False) -> int:
    """Satırları yazar. replace=True: hissenin önceki satırları silinir (extra
    alanlar korunur). only_after: yalnızca bu tarihten sonraki günler eklenir,
    var olan günler değişmez. Yazılan satır sayısını döner."""
    ticker = meta["ticker"]
    now = _now()
    rows = []
    for date, rec in df.iterrows():
        day = pd.Timestamp(date).strftime("%Y-%m-%d")
        if only_after and day <= only_after:
            continue
        rows.append((ticker, day, _row_json(rec.to_dict()), source, DATASET_VERSION, now))
    with _connect() as conn, storage.write_transaction(conn):
        extras = {}
        if replace:
            extras = dict(conn.execute(
                f"SELECT date, extra FROM {DAILY_TABLE} WHERE ticker = ? AND extra IS NOT NULL", (ticker,)))
            conn.execute(f"DELETE FROM {DAILY_TABLE} WHERE ticker = ?", (ticker,))
        conn.executemany(
            f"""INSERT INTO {DAILY_TABLE} (ticker, date, data, extra, source, dataset_version, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (ticker, date) DO UPDATE SET data = excluded.data, source = excluded.source,
                    dataset_version = excluded.dataset_version, recorded_at = excluded.recorded_at""",
            [(t, d, data, extras.get(d), s, v, r) for t, d, data, s, v, r in rows],
        )
        existing = conn.execute(f"SELECT created_at, meta FROM {META_TABLE} WHERE ticker = ?", (ticker,)).fetchone()
        created = existing[0] if existing and not replace else now
        stored_meta = dict(meta)
        if existing and not replace:
            old = json.loads(existing[1])
            stored_meta["start"] = old.get("start", meta["start"])
            stored_meta["columns"] = list(dict.fromkeys(old.get("columns", []) + meta["columns"]))
        n, first, last = conn.execute(
            f"SELECT COUNT(*), MIN(date), MAX(date) FROM {DAILY_TABLE} WHERE ticker = ?", (ticker,)).fetchone()
        stored_meta.update(rows=n, start=first, end=last)
        conn.execute(
            f"""INSERT INTO {META_TABLE} (ticker, market, params, meta, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (ticker) DO UPDATE SET market = excluded.market, params = excluded.params,
                    meta = excluded.meta, created_at = excluded.created_at, updated_at = excluded.updated_at""",
            (ticker, meta.get("market", MARKET), json.dumps(params, ensure_ascii=False),
             json.dumps(stored_meta, ensure_ascii=False, default=str), created, now),
        )
    return len(rows)


def load_dataset(ticker: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Kayıtlı veri seti: indeks tarih, sütunlar meta'daki sıra + `source` +
    elle eklenen `extra` alanlar."""
    ticker = normalize_ticker(ticker)
    where, params = ["ticker = ?"], [ticker]
    if start:
        where.append("date >= ?")
        params.append(start)
    if end:
        where.append("date <= ?")
        params.append(end)
    with _connect() as conn:
        rows = conn.execute(f"SELECT date, data, extra, source FROM {DAILY_TABLE} WHERE {' AND '.join(where)} "
                            "ORDER BY date", params).fetchall()
        meta_row = conn.execute(f"SELECT meta FROM {META_TABLE} WHERE ticker = ?", (ticker,)).fetchone()
    if not rows:
        return pd.DataFrame()
    records, extra_cols = [], []
    for date, data, extra, source in rows:
        rec = json.loads(data)
        if extra:
            ext = json.loads(extra)
            extra_cols += [k for k in ext if k not in extra_cols]
            rec.update(ext)
        rec["date"], rec["source"] = date, source
        records.append(rec)
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    order = json.loads(meta_row[0]).get("columns", []) if meta_row else []
    cols = [c for c in order if c in df.columns]
    cols += [c for c in df.columns if c not in cols and c not in extra_cols and c != "source"]
    return df[cols + [c for c in extra_cols if c not in cols] + ["source"]]


def set_extra(ticker: str, date: str, values: dict) -> None:
    """Bir güne elle özellik ekler / günceller (ör. haber skoru). Yeniden
    oluşturma bu alanları silmez. Değer None ise alan kaldırılır."""
    ticker = normalize_ticker(ticker)
    day = pd.Timestamp(date).strftime("%Y-%m-%d")
    with _connect() as conn, storage.write_transaction(conn):
        row = conn.execute(f"SELECT extra FROM {DAILY_TABLE} WHERE ticker = ? AND date = ?", (ticker, day)).fetchone()
        if row is None:
            raise KeyError(f"{ticker} {day}: veri setinde bu gün yok")
        extra = json.loads(row[0]) if row[0] else {}
        for k, v in values.items():
            if v is None:
                extra.pop(k, None)
            else:
                extra[k] = _clean(v)
        conn.execute(f"UPDATE {DAILY_TABLE} SET extra = ? WHERE ticker = ? AND date = ?",
                     (json.dumps(extra, ensure_ascii=False) if extra else None, ticker, day))


def list_datasets() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(f"SELECT ticker, market, params, meta, created_at, updated_at FROM {META_TABLE} "
                            "ORDER BY ticker").fetchall()
    out = []
    for ticker, market, params, meta, created, updated in rows:
        m = json.loads(meta)
        out.append({"ticker": ticker, "market": market, "params": json.loads(params), "meta": m,
                    "rows": m.get("rows"), "start": m.get("start"), "end": m.get("end"),
                    "created_at": created, "updated_at": updated})
    return out


def get_dataset_info(ticker: str) -> dict | None:
    ticker = normalize_ticker(ticker)
    return next((d for d in list_datasets() if d["ticker"] == ticker), None)


def delete_dataset(ticker: str) -> int:
    ticker = normalize_ticker(ticker)
    with _connect() as conn, storage.write_transaction(conn):
        n = conn.execute(f"DELETE FROM {DAILY_TABLE} WHERE ticker = ?", (ticker,)).rowcount
        conn.execute(f"DELETE FROM {META_TABLE} WHERE ticker = ?", (ticker,))
    return n


def create(ticker: str, fetch_years: int = DEFAULT_FETCH_YEARS, keep_years: int = DEFAULT_KEEP_YEARS,
           vwap_fetcher=None, fetch_valuation: bool = False, reconstruct_history: bool = False,
           **kwargs) -> tuple[pd.DataFrame, dict]:
    """Veri setini hazırlayıp kaydeder (var olanın yerine - extra alanlar korunur).
    reconstruct_history: veri setinin günleri için Ucuzluk Skoru geçmişini önce
    bilanço tablolarından yeniden hesaplayıp valuation_scores_daily'ye yazar
    (valuation_history.py)."""
    ticker = normalize_ticker(ticker)
    if reconstruct_history and "reconstruct_valuation" not in kwargs:
        import valuation_history

        kwargs["reconstruct_valuation"] = lambda t, closes: valuation_history.reconstruct(t, closes, MARKET)
    valuation = kwargs.pop("valuation", None) or load_valuation(ticker, fetch_missing=fetch_valuation)
    history = kwargs.pop("valuation_history", None)
    history = load_valuation_history(ticker) if history is None else history
    df, meta = build_dataset(ticker, fetch_years, keep_years, vwap_fetcher=vwap_fetcher,
                             valuation=valuation, valuation_history=history, **kwargs)
    params = {"fetch_years": fetch_years, "keep_years": keep_years, "vwap_source": meta["vwap_source"]}
    save_dataset(df, meta, params, SOURCE_BACKFILL, replace=True)
    return load_dataset(ticker), meta


def update(ticker: str, vwap_fetcher=None, **kwargs) -> tuple[int, dict]:
    """Kayıtlı veri setine son kayıtlı günden sonraki günleri ekler (`daily`).
    Var olan günler değişmez; yeni günlere o gün veya öncesindeki son günlük
    skor yazılır (valuation_scores_daily)."""
    info = get_dataset_info(ticker)
    if info is None:
        raise KeyError(f"{normalize_ticker(ticker)} için kayıtlı veri seti yok - önce oluşturun")
    params = info["params"]
    valuation = kwargs.pop("valuation", None) or load_valuation(info["ticker"])
    history = kwargs.pop("valuation_history", None)
    history = load_valuation_history(info["ticker"]) if history is None else history
    df, meta = build_dataset(info["ticker"], params.get("fetch_years", DEFAULT_FETCH_YEARS),
                             params.get("keep_years", DEFAULT_KEEP_YEARS), vwap_fetcher=vwap_fetcher,
                             valuation=valuation, valuation_history=history, **kwargs)
    added = save_dataset(df, meta, params, SOURCE_DAILY, only_after=info["end"])
    return added, meta


def export(ticker: str, out_dir: str, fmt: str = "csv") -> str:
    df = load_dataset(ticker)
    if df.empty:
        raise KeyError(f"{normalize_ticker(ticker)} için kayıtlı veri seti yok")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"ai_dataset_{normalize_ticker(ticker)}.{fmt}")
    if fmt == "parquet":
        df.reset_index().to_parquet(path, index=False)
    else:
        df.to_csv(path, date_format="%Y-%m-%d")
    return path


# ------------------------------------------------------------------------------
# Komut satırı
# ------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Yapay zeka eğitim veri seti (NASDAQ 100 hissesi, günlük)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="veri setini hazırlar ve kaydeder")
    b.add_argument("--ticker", required=True)
    b.add_argument("--fetch-years", type=int, default=DEFAULT_FETCH_YEARS)
    b.add_argument("--keep-years", type=int, default=DEFAULT_KEEP_YEARS)
    b.add_argument("--fetch-valuation", action="store_true", help="skor veritabanında yoksa Yahoo'dan çek")
    b.add_argument("--no-reconstruct", action="store_true",
                   help="Ucuzluk Skoru geçmişini bilançolardan yeniden hesaplama")
    u = sub.add_parser("update", help="kayıtlı veri setlerine yeni günleri ekler")
    g = u.add_mutually_exclusive_group(required=True)
    g.add_argument("--ticker")
    g.add_argument("--all", action="store_true")
    e = sub.add_parser("export", help="CSV / Parquet")
    e.add_argument("--ticker", required=True)
    e.add_argument("--out", default=os.path.join("data", "ml"))
    e.add_argument("--format", choices=("csv", "parquet"), default="csv")
    sub.add_parser("status", help="kayıtlı veri setleri")
    args = parser.parse_args(argv)

    if args.cmd == "build":
        df, meta = create(args.ticker, args.fetch_years, args.keep_years, fetch_valuation=args.fetch_valuation,
                          reconstruct_history=not args.no_reconstruct,
                          progress=log)
        log(f"{meta['ticker']}: {len(df)} gün, {len(df.columns)} sütun kaydedildi ({meta['start']} → {meta['end']})")
        for w in meta["warnings"]:
            log(f"UYARI: {w}")
        return 0
    if args.cmd == "update":
        tickers = [d["ticker"] for d in list_datasets()] if args.all else [args.ticker]
        failures = 0
        for t in tickers:
            try:
                added, _ = update(t)
                log(f"{t}: {added} yeni gün eklendi")
            except Exception as ex:
                failures += 1
                log(f"HATA {t}: {ex}")
        return 1 if failures else 0
    if args.cmd == "export":
        print(export(args.ticker, args.out, args.format))
        return 0
    rows = list_datasets()
    if not rows:
        print("Kayıtlı veri seti yok - önce: python ai_dataset.py build --ticker AAPL")
    for d in rows:
        print(f"{d['ticker']:<8} {d['rows']:>5} gün  {d['start']} → {d['end']}  (güncelleme: {d['updated_at']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
