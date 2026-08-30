# dtw_analysis.py
import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed

CACHE_FILE = "nasdaq_5m_cache.json"
DTW_RESULTS_CACHE_FILE = "dtw_results_cache.json"

def compute_dtw_similarity(s1, s2, max_warping_window=12, time_penalty=0.10):
    """
    Zamansal kısıtlamalı ve zamansal gecikme cezalı sıkılaştırılmış DTW hesaplaması.
    """
    s1 = np.array(s1, dtype=float)
    s2 = np.array(s2, dtype=float)
    
    if len(s1) == 0 or len(s2) == 0:
        return 0.0, float('inf')
        
    min1, max1 = np.min(s1), np.max(s1)
    min2, max2 = np.min(s2), np.max(s2)
    
    s1_norm = (s1 - min1) / (max1 - min1 + 1e-8)
    s2_norm = (s2 - min2) / (max2 - min2 + 1e-8)

    n, m = len(s1_norm), len(s2_norm)
    dtw_matrix = np.full((n + 1, m + 1), np.inf)
    dtw_matrix[0, 0] = 0

    max_len = max(n, m)

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if abs(i - j) > max_warping_window:
                continue

            price_cost = (s1_norm[i-1] - s2_norm[j-1]) ** 2
            lag_cost = time_penalty * (abs(i - j) / max_len)
            total_cost = price_cost + lag_cost
            
            dtw_matrix[i, j] = total_cost + min(
                dtw_matrix[i-1, j],
                dtw_matrix[i, j-1],
                dtw_matrix[i-1, j-1]
            )

    dtw_dist = np.sqrt(dtw_matrix[n, m]) / max_len
    
    if np.isinf(dtw_dist) or np.isnan(dtw_dist):
        return 0.0, float('inf')
        
    similarity_score = max(0.0, round((1.0 / (1.0 + dtw_dist * 10)) * 100, 2))
    return similarity_score, round(float(dtw_dist), 4)

def _fetch_single_ticker_5m(ticker, today_str):
    """Tek bir hissenin 5 dakikalık verisini çeker, US/Eastern (New York) yerel saatinde tutar."""
    try:
        t = yf.Ticker(ticker)
        df = t.history(period="5d", interval="5m", prepost=True)
        
        if df.empty:
            return ticker, None

        # yfinance verisini America/New_York zaman dilimine sabitleyelim
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC').tz_convert('America/New_York')
        else:
            df.index = df.index.tz_convert('America/New_York')

        df['Date_Str'] = df.index.strftime('%Y-%m-%d')
        df['Time_Str'] = df.index.strftime('%H:%M')
        df['Full_Time'] = df.index.strftime('%Y-%m-%d %H:%M')

        # Geçmiş işlem günlerini filtrele
        unique_dates = [d for d in sorted(df['Date_Str'].unique()) if d < today_str]
        
        if len(unique_dates) < 2:
            return ticker, None
            
        day1_date, day2_date = unique_dates[-2], unique_dates[-1]

        d1_df = df[df['Date_Str'] == day1_date]
        d2_df = df[df['Date_Str'] == day2_date]

        if d1_df.empty or d2_df.empty:
            return ticker, None

        data = {
            "day1": {
                "date": day1_date,
                "times": d1_df['Full_Time'].tolist(),
                "times_short": d1_df['Time_Str'].tolist(),
                "prices": d1_df['Close'].round(2).tolist()
            },
            "day2": {
                "date": day2_date,
                "times": d2_df['Full_Time'].tolist(),
                "times_short": d2_df['Time_Str'].tolist(),
                "prices": d2_df['Close'].round(2).tolist()
            }
        }
        return ticker, data
    except Exception:
        return ticker, None
    
def fetch_and_cache_5m_data(ticker_list, max_workers=12):
    today_str = str(date.today())
    cache_data = {}

    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
        except Exception:
            cache_data = {}

    last_update = cache_data.get("_meta", {}).get("last_update_date", "")
    if last_update == today_str and len(cache_data.get("stocks", {})) > 0:
        return cache_data["stocks"]

    updated_stocks = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ticker = {
            executor.submit(_fetch_single_ticker_5m, ticker, today_str): ticker 
            for ticker in ticker_list
        }
        for future in as_completed(future_to_ticker):
            ticker, result = future.result()
            if result is not None:
                updated_stocks[ticker] = result

    full_cache = {
        "_meta": {"last_update_date": today_str},
        "stocks": updated_stocks
    }
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(full_cache, f, ensure_ascii=False, indent=2)

    return updated_stocks


def compute_two_day_trend(day1_prices, day2_prices):
    """
    1. günün açılışından 2. günün kapanışına net fiyat değişimini hesaplar ve
    bunu yükseliş/düşüş trendi olarak sınıflandırır.
    """
    if not day1_prices or not day2_prices:
        return None, "Bilinmiyor"

    start_price = float(day1_prices[0])
    end_price = float(day2_prices[-1])
    if start_price == 0:
        return None, "Bilinmiyor"

    change_pct = round((end_price - start_price) / start_price * 100, 2)
    trend = "📈 Yükseliş" if change_pct >= 0 else "📉 Düşüş"
    return change_pct, trend


def load_cached_dtw_results(max_warping_window, time_penalty):
    """Sadece zamansal parametrelere bağlı olarak önbellek sonuçlarını yükler."""
    today_str = str(date.today())
    if os.path.exists(DTW_RESULTS_CACHE_FILE):
        try:
            with open(DTW_RESULTS_CACHE_FILE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
                meta = cache.get("_meta", {})
                if (meta.get("last_update_date") == today_str and
                    meta.get("max_warping_window") == max_warping_window and
                    meta.get("time_penalty") == time_penalty):
                    return cache.get("self_similarity")
        except Exception:
            pass
    return None


def save_cached_dtw_results(max_warping_window, time_penalty, self_sim):
    """Hesaplanan DTW öz-benzerlik sonuçlarını diske kaydeder."""
    today_str = str(date.today())
    cache = {
        "_meta": {
            "last_update_date": today_str,
            "max_warping_window": max_warping_window,
            "time_penalty": time_penalty
        },
        "self_similarity": self_sim
    }
    with open(DTW_RESULTS_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def find_local_extremes(times, prices, window=3):
    prices = np.array(prices)
    peaks = []
    troughs = []
    
    n = len(prices)
    if n < window * 2 + 1:
        return peaks, troughs

    for i in range(window, n - window):
        sub = prices[i - window : i + window + 1]
        if prices[i] == np.max(sub) and prices[i] > prices[i-1]:
            peaks.append((i, times[i], float(prices[i])))
        elif prices[i] == np.min(sub) and prices[i] < prices[i-1]:
            troughs.append((i, times[i], float(prices[i])))
            
    peaks = sorted(peaks, key=lambda x: x[2], reverse=True)[:3]
    troughs = sorted(troughs, key=lambda x: x[2])[:3]
    
    return peaks, troughs