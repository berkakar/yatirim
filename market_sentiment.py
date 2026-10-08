"""
Piyasa Duyarlılığı (Korku / Açgözlülük) servisi - NASDAQ 100, NYSE ve BIST 100.

Yahoo Finance'in hazır bir duyarlılık skoru yok; bu modül piyasa verisinden,
CNN Fear & Greed endeksine benzer bir mantıkla 0-100 arası bir skor üretir
(0 = aşırı korku, 100 = aşırı açgözlülük). Her piyasa için bileşenler:

1. Momentum         - endeks / 125 günlük ortalama sapmasının son 1 yıldaki yüzdelik sırası
2. Oynaklık         - oynaklık endeksi (VXN / VIX) / 50 günlük ortalaması; yüksekse korku
                      (yüzdelik sıra ters çevrilir)
3. Genişlik         - piyasa listesindeki hisselerin 50 günlük ortalamanın üstündeki yüzdesi
4. Yeni zirve/dip   - son 5 günde 52 haftalık zirve yapanların, zirve+dip yapanlara oranı
5. Güvenli liman    - endeksin 20 günlük getirisi - TLT (uzun vadeli tahvil) 20 günlük
                      getirisi; yüzdelik sıra

BIST 100 farkları (Yahoo'da BIST için oynaklık endeksi ve opsiyon verisi yok):
- Oynaklık: XU100'ün 20 günlük gerçekleşen (yıllıklandırılmış) oynaklığı.
- Güvenli liman: TLT yerine dolar/TL (TRY=X) - Türk yatırımcının kaçtığı yer.
- Momentum ve yeni zirve/dip dolar bazında (fiyat / USDTRY) hesaplanır; TL
  bazında enflasyon yüzünden yapısal olarak açgözlülük tarafına kayarlardı.
  Genişlik (ortalamanın üstündeki hisse yüzdesi) TL bazında kalır.
- Yahoo'nun hatalı BIST mumları (günlük %10,5 marjını aşan sıçrama, donmuş
  fiyat - bkz. yf_data_quality.py) olan hisseler genişlik ölçülerinden çıkarılır.
- Put/call oranı yok.

Bileşik skor bu beşinin ortalamasıdır. Hepsi geçmiş fiyatlardan her gün için
yeniden hesaplanabildiği için grafikteki geçmiş de aynı formülle üretilir (ilk
çalıştırmadan itibaren dolu). Opsiyon put/call oranı (QQQ / SPY) yalnızca o günün
anlık verisi olduğundan skora katılmaz, bilgi olarak gösterilir.

Sonuç `market_sentiment_cache` kaydına (storage, ortak) yazılır; arayüz Giriş
Sayfası'nda buradan okur (bkz. market_sentiment_ui.py).

Kullanım (Droplet'te yatirim-market-sentiment.timer hafta içi kapanıştan sonra çağırır):
    python market_sentiment.py                 # tüm piyasalar
    python market_sentiment.py --market nyse
"""

import argparse
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import storage

STORAGE_NAME = "market_sentiment_cache"
HISTORY_DAYS = 180         # kaydedilen bileşik skor geçmişi (işlem günü)
PERCENTILE_WINDOW = 252    # yüzdelik sıranın hesaplandığı pencere (~1 yıl)
DOWNLOAD_PERIOD = "3y"     # 125 günlük ortalama + 252 günlük pencere + geçmiş için yeterli
MARKET_PAUSE_S = 20        # piyasalar arasında Yahoo'ya nefes aldırmak için

REALIZED_VOL_WINDOW = 20  # oynaklık endeksi olmayan piyasada gerçekleşen oynaklık penceresi
# Yeni zirve/dip (252) + 50 günlük ortalama penceresi: bu kadar son barda hatalı mum
# olan hisse bugünkü genişlik ölçülerini bozar, çıkarılır.
QUALITY_LOOKBACK = 300

# volatility None ise endeksin gerçekleşen oynaklığı kullanılır; usd_fx verilirse
# momentum ve yeni zirve/dip o kura bölünerek (dolar bazında) hesaplanır.
MARKETS = {
    "nasdaq100": {
        "market": "NASDAQ 100", "index": "^NDX", "index_label": "Nasdaq-100",
        "volatility": "^VXN", "volatility_label": "VXN", "options": "QQQ",
        "safe_haven": "TLT", "safe_haven_label": "TLT", "usd_fx": None,
    },
    "nyse": {
        "market": "NYSE", "index": "^NYA", "index_label": "NYSE Composite",
        "volatility": "^VIX", "volatility_label": "VIX", "options": "SPY",
        "safe_haven": "TLT", "safe_haven_label": "TLT", "usd_fx": None,
    },
    "bist100": {
        "market": "BIST 100", "index": "XU100.IS", "index_label": "BIST 100",
        "volatility": None, "volatility_label": "XU100 20 günlük oynaklık", "options": None,
        "safe_haven": "TRY=X", "safe_haven_label": "USD/TRY", "usd_fx": "TRY=X",
    },
}

COMPONENTS = {
    "momentum": "Momentum (125 gün)",
    "volatility": "Oynaklık",
    "breadth": "Genişlik (50 gün)",
    "highs_lows": "Yeni Zirve / Dip",
    "safe_haven": "Güvenli Liman Talebi",
}

# (üst sınır, etiket, ikon) - skor üst sınırın altındaysa o etiket.
LABELS = (
    (25, "Aşırı Korku", "😱"),
    (45, "Korku", "😟"),
    (55, "Nötr", "😐"),
    (75, "Açgözlülük", "🙂"),
    (101, "Aşırı Açgözlülük", "🤑"),
)


def log(msg):
    print(f"[sentiment] {msg}", flush=True)


def label_for(score):
    if score is None:
        return "Veri yok", "❔"
    for upper, label, icon in LABELS:
        if score < upper:
            return label, icon
    return LABELS[-1][1], LABELS[-1][2]


# ------------------------------------------------------------------------------
# Hesaplama (saf fonksiyonlar - testler sahte seriyle çağırır)
# ------------------------------------------------------------------------------

def rolling_percentile(series: pd.Series, window: int = PERCENTILE_WINDOW) -> pd.Series:
    """Her gün için değerin son `window` gün içindeki yüzdelik sırası (0-100)."""
    return series.rolling(window, min_periods=min(60, window)).rank(pct=True) * 100


def pct_above_sma(closes: pd.DataFrame, window: int) -> pd.Series:
    """Ortalaması hesaplanabilen hisseler içinde kapanışı `window` günlük
    ortalamanın üstünde olanların yüzdesi."""
    sma = closes.rolling(window, min_periods=window).mean()
    valid = sma.notna() & closes.notna()
    count = valid.sum(axis=1)
    above = ((closes > sma) & valid).sum(axis=1)
    return (above / count.replace(0, np.nan)) * 100


def highs_lows_score(closes: pd.DataFrame, window: int = 252, smooth: int = 5) -> pd.Series:
    """Son `smooth` günde 52 haftalık zirve yapan hisse sayısının, zirve + dip
    yapanlara oranı (0-100). Hiçbiri yoksa 50 (nötr)."""
    roll_max = closes.rolling(window, min_periods=window).max()
    roll_min = closes.rolling(window, min_periods=window).min()
    highs = (closes >= roll_max) & roll_max.notna()
    lows = (closes <= roll_min) & roll_min.notna()
    h = highs.sum(axis=1).rolling(smooth, min_periods=1).sum()
    lo = lows.sum(axis=1).rolling(smooth, min_periods=1).sum()
    total = h + lo
    score = (h / total.replace(0, np.nan)) * 100
    has_data = roll_max.notna().any(axis=1)
    return score.where(total > 0, 50.0).where(has_data)


def realized_volatility(close: pd.Series, window: int = REALIZED_VOL_WINDOW) -> pd.Series:
    """Günlük getirilerin `window` günlük standart sapması, yıllıklandırılmış (%)."""
    return close.pct_change().rolling(window, min_periods=window).std() * np.sqrt(252) * 100


def compute_components(index_close: pd.Series, vol_close, haven_close: pd.Series,
                       closes: pd.DataFrame, usd_fx=None) -> pd.DataFrame:
    """Bileşenlerin günlük serileri (0-100) ve ham değerleri. Satırlar endeksin
    işlem günleri; diğer seriler bu günlere hizalanır (eksik gün en fazla 3 gün
    ileri taşınır - tatil farkları için).

    vol_close None ise oynaklık endeksin gerçekleşen oynaklığından hesaplanır.
    usd_fx (yerel para / USD kuru) verilirse momentum ve yeni zirve/dip dolar
    bazında hesaplanır."""
    idx = index_close.dropna()
    dates = idx.index
    vol = realized_volatility(idx) if vol_close is None else vol_close.reindex(dates).ffill(limit=3)
    haven = haven_close.reindex(dates).ffill(limit=3)
    closes = closes.reindex(dates).ffill(limit=3)
    if usd_fx is not None:
        fx = usd_fx.reindex(dates).ffill(limit=3)
        trend_idx, trend_closes = idx / fx, closes.div(fx, axis=0)
    else:
        trend_idx, trend_closes = idx, closes

    out = pd.DataFrame(index=dates)
    sma125 = trend_idx.rolling(125, min_periods=125).mean()
    out["momentum_raw"] = (trend_idx / sma125 - 1) * 100
    out["momentum"] = rolling_percentile(out["momentum_raw"])

    vol_sma50 = vol.rolling(50, min_periods=50).mean()
    out["volatility_raw"] = vol
    out["volatility_vs_avg"] = (vol / vol_sma50 - 1) * 100
    out["volatility"] = 100 - rolling_percentile(out["volatility_vs_avg"])

    out["breadth"] = pct_above_sma(closes, 50)
    out["breadth200"] = pct_above_sma(closes, 200)
    out["highs_lows"] = highs_lows_score(trend_closes)

    out["safe_haven_raw"] = (idx.pct_change(20) - haven.pct_change(20)) * 100
    out["safe_haven"] = rolling_percentile(out["safe_haven_raw"])

    scores = out[list(COMPONENTS)]
    # En az 3 bileşen varsa ortalama alınır; aksi halde o gün için skor yok.
    out["score"] = scores.mean(axis=1).where(scores.notna().sum(axis=1) >= 3)
    return out


def put_call_score(ratio):
    """Put/call hacim oranını 0-100'e çevirir: 0,5 ve altı açgözlülük (100),
    1,2 ve üstü korku (0), arası doğrusal."""
    if ratio is None or not np.isfinite(ratio):
        return None
    return float(np.clip((1.2 - ratio) / 0.7 * 100, 0, 100))


def _round(value, digits=1):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, digits) if np.isfinite(value) else None


def build_snapshot(market_cfg: dict, comp: pd.DataFrame, universe_size: int, put_call=None) -> dict:
    """Hesaplanan serilerden kaydedilecek sözlüğü oluşturur."""
    valid = comp.dropna(subset=["score"])
    if valid.empty:
        raise ValueError("bileşik skor hesaplanamadı (yeterli geçmiş veri yok)")
    last = valid.iloc[-1]
    as_of = valid.index[-1]
    score = _round(last["score"])
    label, icon = label_for(score)
    prev = _round(valid["score"].iloc[-2]) if len(valid) > 1 else None
    week_ago = _round(valid["score"].iloc[-6]) if len(valid) > 5 else None

    vol_label = market_cfg["volatility_label"]
    usd = " (dolar bazında)" if market_cfg.get("usd_fx") else ""
    details = {
        "momentum": f"{market_cfg['index_label']}{usd} 125 günlük ortalamanın %{abs(_round(last['momentum_raw'], 2) or 0)} "
                    f"{'üstünde' if (last['momentum_raw'] or 0) >= 0 else 'altında'}",
        "volatility": f"{vol_label} {_round(last['volatility_raw'], 2)} "
                      f"(50 günlük ortalamaya göre %{_round(last['volatility_vs_avg'], 1)})",
        "breadth": f"Hisselerin %{_round(last['breadth'])}'i 50 günlük, "
                   f"%{_round(last['breadth200'])}'i 200 günlük ortalamanın üstünde",
        "highs_lows": f"Son 5 günde 52 haftalık zirve yapanların, zirve + dip yapanlara oranı{usd}",
        "safe_haven": f"Endeks 20 günlük getirisi - {market_cfg['safe_haven_label']} 20 günlük getirisi: "
                      f"%{_round(last['safe_haven_raw'], 2)}",
    }
    components = {
        key: {"label": COMPONENTS[key], "score": _round(last[key]), "detail": details[key]}
        for key in COMPONENTS
    }
    history = [
        {"date": d.strftime("%Y-%m-%d"), "score": _round(s)}
        for d, s in valid["score"].tail(HISTORY_DAYS).items()
    ]
    return {
        "market": market_cfg["market"],
        "as_of": as_of.strftime("%Y-%m-%d"),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "score": score,
        "label": label,
        "icon": icon,
        "previous": prev,
        "week_ago": week_ago,
        "components": components,
        "put_call": put_call,
        "universe_size": universe_size,
        "sources": {
            "index": market_cfg["index"], "volatility": market_cfg["volatility"],
            "safe_haven": market_cfg["safe_haven"], "options": market_cfg["options"],
        },
        "history": history,
    }


# ------------------------------------------------------------------------------
# Yahoo Finance
# ------------------------------------------------------------------------------

def market_universe(market: str) -> list:
    """Tüm kullanıcıların `market` piyasa listesi (tekrarsız); kayıtlı kullanıcı
    yoksa varsayılan liste. Gruplar dahil edilmez - genişlik ölçüsü piyasanın
    kendisini temsil etsin."""
    import config
    import valuation_service

    users = valuation_service.known_users()
    tickers = []
    if not users:
        tickers = config._defaults().get(market, [])
    for user in users:
        tickers += config.load_ticker_lists(user).get(market, [])
    return valuation_service.dedupe(tickers)


def download_closes(tickers, period=DOWNLOAD_PERIOD, retries=2, sleep=time.sleep) -> pd.DataFrame:
    """Günlük kapanışları tek istekte indirir (sütunlar hisse). Boş gelirse
    artan beklemeyle tekrar dener."""
    import yfinance as yf

    last_error = None
    for attempt in range(retries + 1):
        if attempt:
            sleep(30 * attempt)
        try:
            data = yf.download(list(tickers), period=period, interval="1d", auto_adjust=True,
                               progress=False, threads=True, group_by="column")
            if data is not None and not data.empty:
                closes = data["Close"]
                if isinstance(closes, pd.Series):  # tek hisse istendiyse
                    closes = closes.to_frame(list(tickers)[0])
                closes.index = pd.to_datetime(closes.index).tz_localize(None).normalize()
                return closes.dropna(how="all")
        except Exception as e:  # ağ / Yahoo hatası - tekrar dene
            last_error = e
    raise RuntimeError(f"Yahoo'dan kapanışlar alınamadı: {last_error or 'boş yanıt'}")


def fetch_put_call(symbol: str, expirations: int = 3):
    """En yakın `expirations` vadenin toplam put / call işlem hacmi oranı.
    Alınamazsa None (skor bu bileşen olmadan hesaplanır)."""
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        put_vol = call_vol = 0.0
        for exp in list(ticker.options)[:expirations]:
            chain = ticker.option_chain(exp)
            put_vol += float(chain.puts["volume"].fillna(0).sum())
            call_vol += float(chain.calls["volume"].fillna(0).sum())
        if call_vol <= 0:
            return None
        ratio = put_vol / call_vol
        return {"symbol": symbol, "ratio": _round(ratio, 2), "score": _round(put_call_score(ratio)),
                "put_volume": int(put_vol), "call_volume": int(call_vol)}
    except Exception as e:
        log(f"{symbol} put/call oranı alınamadı: {e}")
        return None


def drop_bad_bist_data(closes: pd.DataFrame, lookback: int = QUALITY_LOOKBACK) -> tuple:
    """Son `lookback` barda Yahoo'nun hatalı mumu (günlük marjı aşan sıçrama ya da
    donmuş fiyat - bkz. yf_data_quality) görülen BIST hisselerini çıkarır.
    (temiz kapanışlar, çıkarılanlar) döner; .IS olmayan sütunlara dokunmaz."""
    import yf_data_quality as q

    dropped = []
    for col in closes.columns:
        recent = closes[col].dropna().tail(lookback)
        if q.has_implausible_daily_move(recent, col) or (
                col.upper().endswith(".IS") and q.has_flat_prices(recent.tail(20))):
            dropped.append(col)
    return closes.drop(columns=dropped), dropped


def compute_market(slug: str, downloader=download_closes, put_call_fetcher=fetch_put_call,
                   universe=None) -> dict:
    cfg = MARKETS[slug]
    tickers = universe if universe is not None else market_universe(cfg["market"])
    specials = [s for s in (cfg["index"], cfg["volatility"], cfg["safe_haven"], cfg["usd_fx"]) if s]
    specials = list(dict.fromkeys(specials))
    log(f"{cfg['market']}: {len(tickers)} hisse + {', '.join(specials)} indiriliyor")
    closes = downloader(list(dict.fromkeys(specials + list(tickers))))
    if cfg["index"] not in closes or closes[cfg["index"]].dropna().empty:
        raise RuntimeError(f"{cfg['index']} verisi gelmedi")
    if cfg["usd_fx"] and (cfg["usd_fx"] not in closes or closes[cfg["usd_fx"]].dropna().empty):
        raise RuntimeError(f"{cfg['usd_fx']} verisi gelmedi (dolar bazı hesaplanamaz)")
    stock_cols = [t for t in tickers if t in closes.columns and t not in specials]
    missing = len(tickers) - len(stock_cols)
    if missing:
        log(f"{cfg['market']}: {missing} hisse için veri gelmedi")
    stocks, dropped = drop_bad_bist_data(closes[stock_cols])
    if dropped:
        log(f"{cfg['market']}: hatalı Yahoo verisi nedeniyle çıkarılan {len(dropped)} hisse: {', '.join(dropped)}")
    empty = pd.Series(dtype=float)

    def col(symbol):
        return closes[symbol] if symbol in closes else empty

    comp = compute_components(
        closes[cfg["index"]],
        col(cfg["volatility"]) if cfg["volatility"] else None,
        col(cfg["safe_haven"]),
        stocks,
        usd_fx=closes[cfg["usd_fx"]] if cfg["usd_fx"] else None,
    )
    put_call = put_call_fetcher(cfg["options"]) if (put_call_fetcher and cfg["options"]) else None
    return build_snapshot(cfg, comp, stocks.shape[1], put_call)


# ------------------------------------------------------------------------------
# Kayıt
# ------------------------------------------------------------------------------

def save_snapshot(snapshot: dict) -> None:
    def merge(current):
        current = current if isinstance(current, dict) else {}
        current[snapshot["market"]] = snapshot
        return current

    storage.update(STORAGE_NAME, storage.SHARED, merge, {})


def load_all() -> dict:
    """{piyasa: snapshot} - arayüz için."""
    data = storage.read(STORAGE_NAME, storage.SHARED, {})
    return data if isinstance(data, dict) else {}


def run(slugs, pause_s=MARKET_PAUSE_S, sleep=time.sleep, **kwargs) -> int:
    """Seçilen piyasaları sırayla hesaplayıp kaydeder. Başarısız piyasa sayısını
    döner (biri hata verirse diğeri yine çalışır, eski kaydı silinmez)."""
    failures = 0
    for i, slug in enumerate(slugs):
        if i and pause_s:
            sleep(pause_s)
        try:
            snapshot = compute_market(slug, **kwargs)
            save_snapshot(snapshot)
            log(f"{snapshot['market']}: {snapshot['score']} ({snapshot['label']}) - {snapshot['as_of']}")
        except Exception as e:
            failures += 1
            log(f"HATA {MARKETS[slug]['market']}: {e}")
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description="Piyasa Duyarlılığı (Korku/Açgözlülük) servisi")
    parser.add_argument("--market", choices=sorted(MARKETS), action="append",
                        help="yalnızca bu piyasa (tekrarlanabilir); verilmezse hepsi")
    args = parser.parse_args(argv)
    if not storage.enabled():
        log(f"UYARI: YATIRIM_DB_PATH tanımlı değil - sonuçlar yerel {storage.db_path()} dosyasına yazılacak.")
    slugs = args.market or list(MARKETS)
    return 1 if run(slugs) else 0


if __name__ == "__main__":
    sys.exit(main())
