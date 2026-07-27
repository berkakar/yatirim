import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import numpy as np

@st.cache_data
def get_stoploss_data(ticker):

    print(f"DEBUG: {ticker} için veri alınıyor...")
    
    # 1. Veri İndirme (1 yıllık periyot)
    df = yf.download(ticker, period="1y", progress=False)
    
    # yfinance sütun yapısını düzeltme (Eğer MultiIndex gelirse)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    
    # Yeterli veri yoksa işlemi durdur (En az 50 bar kontrolü)
    if df.empty or len(df) < 50: 
        return None
    
    close = df['Close'].iloc[-1]

    # 2. EMA Hesaplamaları (EMA20, EMA50, EMA200)
    ema20 = df.ta.ema(length=20).iloc[-1]
    ema50 = df.ta.ema(length=50).iloc[-1]
    
    # Hissenin 200 günlük verisi var mı kontrolü
    if len(df) >= 200:
        ema200 = df.ta.ema(length=200).iloc[-1]
    else:
        ema200 = np.nan

    # Yüzdesel Uzaklık Hesaplama: ((Fiyat - EMA) / EMA) * 100
    ema20_dist = ((close - ema20) / ema20) * 100 if pd.notna(ema20) else 0.0
    ema50_dist = ((close - ema50) / ema50) * 100 if pd.notna(ema50) else 0.0
    ema200_dist = ((close - ema200) / ema200) * 100 if pd.notna(ema200) else None

    # 3. Yıllık Volatilite Hesaplama
    df['Returns'] = np.log(df['Close'] / df['Close'].shift(1))
    volatility = df['Returns'].std() * np.sqrt(252) * 100 
    
    # 4. Maksimum Günlük Düşüş (Yüzdesel)
    max_daily_drop = (df['Close'].pct_change() * 100).min()
    
    # 5. Tipik Günlük Düşüş (0% ile -0.5% aralığındaki ortalama)
    daily_pct = df['Close'].pct_change()
    typical_drops = daily_pct[(daily_pct < 0) & (daily_pct > -0.005)]
    typical_drop_avg = typical_drops.mean() * 100 if not typical_drops.empty else 0
    
    # 6. Düşüş Histogramı (En sık görülen 5 düşüş seviyesi)
    pct_changes = df['Close'].pct_change() * 100
    drops = pct_changes[pct_changes < 0]
    rounded_drops = np.floor(drops + 0.5)
    in_range = rounded_drops[(rounded_drops <= -1) & (rounded_drops >= -25)]
    counts = in_range.value_counts().nlargest(5)
    histogram_str = ", ".join([f"{int(idx)}% ({int(val)}g)" for idx, val in counts.items()])
    
    # 7. ATR Hesaplama (14 günlük)
    atr = df.ta.atr(high=df['High'], low=df['Low'], close=df['Close'], length=14).iloc[-1]
    
    # 8. Stop Loss Hesaplama (Fiyat ve Yüzde)
    def calc_sl(multiplier):
        sl_price = close - (multiplier * atr)
        if sl_price <= 0: return 0, 0
        sl_percent = ((close - sl_price) / close) * 100
        return round(sl_price, 2), round(sl_percent, 2)
    
    sl15, risk15 = calc_sl(1.5)
    sl20, risk20 = calc_sl(2.0)
    sl30, risk30 = calc_sl(3.0)
    
    # Sonuçları paketle ve döndür
    return {
        "Close": close,
        "EMA20_Dist": round(ema20_dist, 2),
        "EMA50_Dist": round(ema50_dist, 2),
        "EMA200_Dist": round(ema200_dist, 2) if ema200_dist is not None else "-",
        "Volatility": round(volatility, 2),
        "Max_Daily_Drop": round(max_daily_drop, 2),
        "Typical_Drop": round(typical_drop_avg, 3),
        "Histogram": histogram_str,
        "SL_1.5": sl15, "Risk_1.5": risk15,
        "SL_2.0": sl20, "Risk_2.0": risk20,
        "SL_3.0": sl30, "Risk_3.0": risk30
    }