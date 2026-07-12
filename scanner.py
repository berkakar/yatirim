import streamlit as st
import yfinance as yf
import pandas_ta as ta
import pandas as pd

@st.cache_data
def get_scanner_data(ticker):
    df = yf.download(ticker, period="1y", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.empty or 'Volume' not in df.columns:
        return pd.DataFrame()
    
    df.ta.sma(length=50, append=True)
    df.ta.mfi(length=14, append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    
    if 'MFI_14' not in df.columns: df['MFI_14'] = 0.0
    
    vol_ma20 = df['Volume'].rolling(window=20).mean()
    macd_cols = [c for c in df.columns if 'MACD_' in c and 'MACDs_' not in c]
    macds_cols = [c for c in df.columns if 'MACDs_' in c]
    
    macd_bullish = df[macd_cols[0]] > df[macds_cols[0]] if (macd_cols and macds_cols) else False
    df['smart_signal'] = (df['Volume'] > (vol_ma20 * 1.5)) & (df['MFI_14'] > 50) & (macd_bullish)
    return df