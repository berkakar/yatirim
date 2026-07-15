import yfinance as yf
import pandas as pd
import numpy as np
from scipy.signal import argrelextrema
import matplotlib.pyplot as plt
import warnings

# Uyarıları kapat (yfinance gereksiz çıktılarını engellemek için)
warnings.filterwarnings('ignore')

# --- HİSSE LİSTELERİ ---
nasdaq_100 = ["AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "AVGO", "PEP", "COST", "ADBE", "AMD", "CSCO", "INTC", "CMCSA", "AMGN", "NFLX", "INTU", "TXN", "QCOM"]
bist_100 = ["THYAO.IS", "GARAN.IS", "EREGL.IS", "ASELS.IS", "KCHOL.IS", "AKBNK.IS", "SISE.IS", "ISCTR.IS", "TUPRS.IS", "BIMAS.IS", "PETKM.IS", "YKBNK.IS", "PGSUS.IS", "SAHOL.IS", "FROTO.IS", "KOZAL.IS", "HEKTS.IS", "SASA.IS"]
tickers = nasdaq_100 + bist_100

def get_data(ticker):
    try:
        df = yf.download(ticker, period="1y", progress=False)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index()
        if 'Date' not in df.columns:
            df = df.rename(columns={'index': 'Date'})
        df['Date'] = pd.to_datetime(df['Date'])
        return df
    except Exception:
        return None

def find_cup_and_handle(df, order=10, symmetry_threshold=0.05, depth_threshold=0.2):
    prices = df['Close'].values
    high_indices = argrelextrema(prices, np.greater_equal, order=order)[0]
    low_indices = argrelextrema(prices, np.less_equal, order=order)[0]
    
    highs = df.iloc[high_indices].reset_index(drop=True)
    lows = df.iloc[low_indices].reset_index(drop=True)
    
    found_patterns = []
    
    for i in range(len(highs) - 1):
        peak_A = highs.iloc[i]
        peak_C = highs.iloc[i+1]
        
        date_A = pd.Timestamp(peak_A['Date'])
        date_C = pd.Timestamp(peak_C['Date'])
        
        search_lows = lows[(lows['Date'] > date_A) & (lows['Date'] < date_C)]
        if search_lows.empty: continue
        
        dip_B = search_lows.loc[search_lows['Close'].idxmin()]
        
        price_A = float(peak_A['Close'])
        price_C = float(peak_C['Close'])
        price_B = float(dip_B['Close'])
        
        if abs(price_A - price_C) / price_A > symmetry_threshold: continue
        if (price_A - price_B) / price_A < depth_threshold: continue
            
        date_C_plus_30 = date_C + pd.Timedelta(days=30)
        search_handle = lows[(lows['Date'] > date_C) & (lows['Date'] < date_C_plus_30)]
        if search_handle.empty: continue
        
        dip_D = search_handle.loc[search_handle['Close'].idxmin()]
        price_D = float(dip_D['Close'])
        
        # Kulp derinlik kontrolü
        mid_depth = price_A - ((price_A - price_B) * 0.5)
        if price_D > mid_depth and price_D < price_C:
            found_patterns.append({'A': peak_A, 'B': dip_B, 'C': peak_C, 'D': dip_D})
    return found_patterns

def plot_pattern(ticker, df, pattern):
    plt.figure(figsize=(10, 5))
    plt.plot(df['Date'], df['Close'], label='Fiyat', alpha=0.3, color='gray')
    
    # Fincan ve Kulp noktalarını çiz
    dates = [pattern['A']['Date'], pattern['B']['Date'], pattern['C']['Date'], pattern['D']['Date']]
    prices = [float(pattern['A']['Close']), float(pattern['B']['Close']), float(pattern['C']['Close']), float(pattern['D']['Close'])]
    
    plt.plot([pattern['A']['Date'], pattern['B']['Date'], pattern['C']['Date']], 
             [pattern['A']['Close'], pattern['B']['Close'], pattern['C']['Close']], color='blue', label='Fincan')
    plt.plot([pattern['C']['Date'], pattern['D']['Date']], 
             [pattern['C']['Close'], pattern['D']['Close']], color='red', linestyle='--', label='Kulp')
    
    plt.scatter(dates, prices, color='black', zorder=5)
    plt.title(f"{ticker} Formasyon Tespit Edildi!")
    plt.legend()
    plt.show()

# --- ANA DÖNGÜ ---
print(f"Toplam {len(tickers)} hisse analiz edilecek. Lütfen bekleyin...")

for i, ticker in enumerate(tickers):
    print(f"[{i+1}/{len(tickers)}] İşleniyor: {ticker:<10}", end=" -> ")
    
    data = get_data(ticker)
    
    if data is None:
        print("HATA: Veri alınamadı.")
        continue
        
    patterns = find_cup_and_handle(data)
    
    if patterns:
        print(f"BULUNDU! ({len(patterns)} adet)")
        plot_pattern(ticker, data, patterns[-1])
    else:
        print("Uygun formasyon yok.")

print("\nTarama tamamlandı.")