"""Hisse Patern Modülü - veri ve DTW hesaplama katmanı.

Kullanıcı tarafından seçilen hisselerin günlük kapanış fiyatlarını kullanarak
üç farklı periyot tipinde (yıllık, 3 aylık, aylık) tekrarlayan patern
benzerliğini ölçer: her periyot tipi için hissenin tamamlanmış son N periyodu
(örn. yıllık için son 3 tam takvim yılı) ikili (pairwise) olarak DTW (Dynamic
Time Warping - bkz. dtw_analysis.py, "🔄 DTW Zaman Serisi & Benzerlik
Analizi" modülüyle ortak algoritma) ile karşılaştırılır; ortalamaları o
periyot tipi için hissenin "patern benzerlik skoru" olur.

Ham günlük fiyat verisi, dtw_analysis.py'deki 5 dakikalık önbellek deseniyle
aynı şekilde diske (CACHE_FILE) günde 1 kez önbelleklenir.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import numpy as np
import pandas as pd
import yfinance as yf

from dtw_analysis import compute_dtw_similarity

CACHE_FILE = "hisse_patern_cache.json"
FETCH_PERIOD = "4y"  # yıllık (son 3 tam takvim yılı) karşılaştırması için en az 4 yıllık veri gerekir
TIME_PENALTY = 0.10  # dtw_analysis.py'deki varsayılanla aynı

# key -> periyot ayarları: etiket (tablo/grafik başlığı), pandas periyot
# frekansı (to_period için), karşılaştırılacak son N tamamlanmış periyot,
# detay grafiği için yeniden örnekleme kuralı (None = ham günlük veri).
PERIOD_CONFIGS = {
    "yillik": {"label": "Yıllık (3 Yıllık)", "freq": "Y", "n_periods": 3, "chart_rule": "ME"},
    "3_aylik": {"label": "3 Aylık (Son 2 Yıl)", "freq": "Q", "n_periods": 8, "chart_rule": "W"},
    "aylik": {"label": "Aylık (Son 12 Ay)", "freq": "M", "n_periods": 12, "chart_rule": None},
}


def _fetch_single_ticker_daily(ticker):
    try:
        t = yf.Ticker(ticker)
        df = t.history(period=FETCH_PERIOD, interval="1d")
        if df is None or df.empty:
            return ticker, None

        df = df.reset_index()
        df.columns = [str(c).capitalize() for c in df.columns]
        if not {"Date", "Close"}.issubset(df.columns):
            return ticker, None

        df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
        df = df.dropna(subset=["Close"])
        if df.empty:
            return ticker, None

        return ticker, {
            "dates": df["Date"].dt.strftime("%Y-%m-%d").tolist(),
            "closes": df["Close"].round(4).tolist(),
        }
    except Exception:
        return ticker, None


def fetch_and_cache_daily_data(ticker_list, max_workers=12):
    """Günlük kapanış verisini diskteki önbellekten okur, eksik/güncel olmayan
    hisseleri paralel olarak Yahoo Finance'den çeker ve önbelleğe yazar."""
    today_str = str(date.today())
    cache_data = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
        except Exception:
            cache_data = {}

    stocks = cache_data.get("stocks", {}) if cache_data.get("_meta", {}).get("last_update_date") == today_str else {}
    missing = [t for t in ticker_list if t not in stocks]

    if missing:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_fetch_single_ticker_daily, t): t for t in missing}
            for future in as_completed(futures):
                ticker, result = future.result()
                if result is not None:
                    stocks[ticker] = result

        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"_meta": {"last_update_date": today_str}, "stocks": stocks}, f, ensure_ascii=False, indent=2)

    return {t: stocks[t] for t in ticker_list if t in stocks}


def _segment_by_period(dates, closes, freq, n_periods):
    """Fiyat serisini takvim periyotlarına (yıl/çeyrek/ay) böler, sadece
    tamamlanmış (içinde bulunulan periyottan önceki) periyotları döner ve
    en güncel n_periods tanesini alır. Her segment en az 3 fiyat noktası
    içermeli, aksi halde DTW için anlamsız kabul edilip atlanır."""
    s = pd.Series(closes, index=pd.to_datetime(dates)).sort_index()
    if s.empty:
        return []

    current_period = pd.Timestamp.today().to_period(freq)
    period_index = s.index.to_period(freq)
    s = s[period_index < current_period]
    if s.empty:
        return []

    segments = []
    for period_key, group in s.groupby(s.index.to_period(freq)):
        if len(group) < 3:
            continue
        segments.append({
            "label": str(period_key),
            "dates": group.index.strftime("%Y-%m-%d").tolist(),
            "closes": group.tolist(),
        })

    return segments[-n_periods:]


def _pairwise_avg_similarity(segments, max_warping_window):
    """Aynı periyot tipindeki tüm segment çiftleri arasındaki DTW benzerlik
    skorlarının ortalamasını döner. Anlamlı bir karşılaştırma için en az 2
    segment gerekir, aksi halde None döner."""
    if len(segments) < 2:
        return None

    sims = []
    for i in range(len(segments)):
        for j in range(i + 1, len(segments)):
            sim, _ = compute_dtw_similarity(
                segments[i]["closes"], segments[j]["closes"], max_warping_window, TIME_PENALTY
            )
            sims.append(sim)

    return round(float(np.mean(sims)), 2) if sims else None


def compute_pattern_table(ticker_list, max_warping_window):
    """Her hisse için üç periyot tipinin patern benzerlik skorlarını hesaplar.

    Döner: (rows, segments_by_ticker)
      rows: tabloda gösterilecek satırların listesi (dict)
      segments_by_ticker: {ticker: {period_key: [segment, ...]}} - detay
        grafiği çizmek için ham segment verisi
    """
    daily_data = fetch_and_cache_daily_data(ticker_list)
    rows = []
    segments_by_ticker = {}

    for ticker in ticker_list:
        data = daily_data.get(ticker)
        if not data or not data.get("closes"):
            continue

        row = {"Hisse": ticker, "Son Fiyat": round(float(data["closes"][-1]), 2)}
        segments_by_ticker[ticker] = {}

        for key, cfg in PERIOD_CONFIGS.items():
            segments = _segment_by_period(data["dates"], data["closes"], cfg["freq"], cfg["n_periods"])
            row[f"{cfg['label']} Benzerlik %"] = _pairwise_avg_similarity(segments, max_warping_window)
            segments_by_ticker[ticker][key] = segments

        rows.append(row)

    return rows, segments_by_ticker


def build_detail_chart_series(segments, chart_rule):
    """Bir periyot tipine ait segmentleri (örn. bir hissenin son 3 yılı)
    grafikte okunabilir olacak şekilde yeniden örnekler (aylık/haftalık
    kapanış) ya da günlük veriyi olduğu gibi bırakır (chart_rule=None).

    Her segment için {label, x, dates, prices} döner - x ekseni periyot içi
    göreli konum (1, 2, 3, ...) olduğundan farklı yıllar/çeyrekler/aylar aynı
    eksende çakışacak şekilde çizilebilir."""
    series = []
    for seg in segments:
        if chart_rule is None:
            x_dates, prices = seg["dates"], seg["closes"]
        else:
            s = pd.Series(seg["closes"], index=pd.to_datetime(seg["dates"]))
            resampled = s.resample(chart_rule).last().dropna()
            x_dates = resampled.index.strftime("%Y-%m-%d").tolist()
            prices = [round(float(v), 2) for v in resampled.tolist()]

        series.append({
            "label": seg["label"],
            "x": list(range(1, len(prices) + 1)),
            "dates": x_dates,
            "prices": prices,
        })
    return series
