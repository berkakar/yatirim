import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import numpy as np

@st.cache_data
def get_stoploss_data(ticker):
    # Veriyi yıllık alıyoruz (yıllık volatilite için daha sağlıklı)
    df = yf.download(ticker, period="1y", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    
    if df.empty or len(df) < 20: 
        return None
    
    # 1. Yıllık Volatilite
    df['Returns'] = np.log(df['Close'] / df['Close'].shift(1))
    volatility = df['Returns'].std() * np.sqrt(252) * 100 
    
    # 2. Maksimum Günlük Düşüş (Eklendi)
    max_daily_drop = (df['Close'].pct_change() * 100).min()
    
    # 3. Tipik Günlük Düşüş (0% ile -0.5% arası)
    daily_pct = df['Close'].pct_change()
    typical_drops = daily_pct[(daily_pct < 0) & (daily_pct > -0.005)]
    typical_drop_avg = typical_drops.mean() * 100 if not typical_drops.empty else 0
    
    # 4. ATR Hesaplama
    atr = df.ta.atr(length=14).iloc[-1]
    close = df['Close'].iloc[-1]
    
    # 5. Stop Loss Hesaplama
    def calc_sl(multiplier):
        sl_price = close - (multiplier * atr)
        sl_percent = ((close - sl_price) / close) * 100
        return round(sl_price, 2), round(sl_percent, 2)
    
    sl15, risk15 = calc_sl(1.5)
    sl20, risk20 = calc_sl(2.0)
    sl30, risk30 = calc_sl(3.0)
    
    return {
        "Close": close,
        "Volatility": round(volatility, 2),
        "Max_Daily_Drop": round(max_daily_drop, 2), # Geri geldi
        "Typical_Drop": round(typical_drop_avg, 3),
        "SL_1.5": sl15, "Risk_1.5": risk15,
        "SL_2.0": sl20, "Risk_2.0": risk20,
        "SL_3.0": sl30, "Risk_3.0": risk30
    }