# dtw_analysis.py
import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed

CACHE_FILE = "nasdaq_5m_cache.json"

def compute_dtw_similarity(s1, s2, max_warping_window=12, time_penalty=0.10):
    """
    Zamansal kısıtlamalı ve zamansal gecikme cezalı sıkılaştırılmış DTW hesaplaması.
    
    :param s1: 1. Fiyat zaman serisi (list veya array)
    :param s2: 2. Fiyat zaman serisi (list veya array)
    :param max_warping_window: Maksimum izin verilen zaman kayması adım sayısı (Örn: 12 adım = 60 dk).
    :param time_penalty: Zamansal uzaklık arttıkça eklenen ceza katsayısı (0.05 - 0.20 arası idealdir).
    :return: (similarity_score, dtw_dist)
    """
    s1 = np.array(s1, dtype=float)
    s2 = np.array(s2, dtype=float)
    
    if len(s1) == 0 or len(s2) == 0:
        return 0.0, float('inf')
        
    # Min-Max Normalizasyonu (Fiyat ölçeğini eşitleme)
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
            # Sakoe-Chiba Band Kısıtlaması: Belirlenen zaman penceresinden daha uzak eşleşmeleri engelle
            if abs(i - j) > max_warping_window:
                continue

            # 1. Fiyat Şekil Farkı
            price_cost = (s1_norm[i-1] - s2_norm[j-1]) ** 2
            
            # 2. Zamansal Gecikme Cezası (Eşleşen noktalar birbirinden uzaklaştıkça maliyet artar)
            lag_cost = time_penalty * (abs(i - j) / max_len)
            
            total_cost = price_cost + lag_cost
            
            dtw_matrix[i, j] = total_cost + min(
                dtw_matrix[i-1, j],    # İlerleme
                dtw_matrix[i, j-1],    # Gecikme
                dtw_matrix[i-1, j-1]  # Birebir Eşleşme
            )

    dtw_dist = np.sqrt(dtw_matrix[n, m]) / max_len
    
    if np.isinf(dtw_dist) or np.isnan(dtw_dist):
        return 0.0, float('inf')
        
    similarity_score = max(0.0, round((1.0 / (1.0 + dtw_dist * 10)) * 100, 2))
    return similarity_score, round(float(dtw_dist), 4)


def _fetch_single_ticker_5m(ticker, today_str):
    """Tek bir hissenin 5 dakikalık verisini çeken iş parçacığı (worker) fonksiyonu."""
    try:
        t = yf.Ticker(ticker)
        df = t.history(period="5d", interval="5m", prepost=True)
        
        if df.empty:
            return ticker, None

        df['Date_Str'] = df.index.strftime('%Y-%m-%d')
        df['Time_Str'] = df.index.strftime('%H:%M')
        df['Full_Time'] = df.index.strftime('%Y-%m-%d %H:%M')

        unique_dates = [d for d in sorted(df['Date_Str'].unique()) if d < today_str]
        
        if len(unique_dates) < 2:
            return ticker, None
            
        day1_date, day2_date = unique_dates[-2], unique_dates[-1]

        d1_df = df[df['Date_Str'] == day1_date]
        d2_df = df[df['Date_Str'] == day2_date]

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
    """
    Multithreading kullanarak tüm hisselerin verilerini eşzamanlı çeker ve önbelleğe alır.
    """
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


def _compute_pair_similarity(args):
    """İki hisse arasındaki DTW benzerliğini paralel hesaplayan yardımcı fonksiyon."""
    t1, t2, p1, p2, date_str, min_similarity, max_warping_window, time_penalty = args
    sim, dist = compute_dtw_similarity(p1, p2, max_warping_window=max_warping_window, time_penalty=time_penalty)
    if sim >= min_similarity:
        return {
            "Hisse 1": t1,
            "Hisse 2": t2,
            "İşlem Günü": date_str,
            "DTW Benzerlik Skoru %": sim,
            "DTW Mesafesi": dist
        }
    return None


def compute_cross_similarity_parallel(stocks_dict, min_similarity=75, max_warping_window=12, time_penalty=0.10, max_workers=8):
    """
    Tüm hisse çiftleri arasındaki DTW matrisini paralel olarak hesaplar.
    """
    stock_keys = list(stocks_dict.keys())
    tasks = []
    
    for i in range(len(stock_keys)):
        for j in range(i + 1, len(stock_keys)):
            t1, t2 = stock_keys[i], stock_keys[j]
            p1 = stocks_dict[t1]["day2"]["prices"]
            p2 = stocks_dict[t2]["day2"]["prices"]
            date_str = stocks_dict[t1]["day2"]["date"]
            tasks.append((t1, t2, p1, p2, date_str, min_similarity, max_warping_window, time_penalty))

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for res in executor.map(_compute_pair_similarity, tasks):
            if res is not None:
                results.append(res)
                
    return results


def find_local_extremes(times, prices, window=3):
    """Lokal tepe ve dip noktalarını tespit eder."""
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
