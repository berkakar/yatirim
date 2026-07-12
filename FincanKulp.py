import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
from scipy.signal import argrelextrema

# Sayfa Ayarları
st.set_page_config(layout="wide", page_title="Global Analiz Terminali")
st.title("🚀 Global Fincan Kulp & Akıllı Sinyal Tarayıcısı")

# Ticker Listeleri
nasdaq_100 = ["AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "AVGO", "PEP", "COST", "ADBE", "AMD", "CSCO", "INTC", "CMCSA", "AMGN", "NFLX", "INTU", "TXN", "QCOM"]
# BIST 100 (Örnek liste, tam listeyi buraya ekleyebilirsin)
bist_100 = ["THYAO.IS", "GARAN.IS", "EREGL.IS", "ASELS.IS", "KCHOL.IS", "AKBNK.IS", "SISE.IS", "ISCTR.IS", "TUPRS.IS", "BIMAS.IS", "PETKM.IS", "YKBNK.IS", "PGSUS.IS", "SAHOL.IS", "FROTO.IS", "KOZAL.IS", "HEKTS.IS", "SASA.IS"]

@st.cache_data
def get_data(ticker):
    df = yf.download(ticker, period="1y")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    
    # HİSSE VERİSİ YOKSA VEYA HATA VARSA BOŞ BİR TABLO DÖNDÜR
    if df.empty or 'Volume' not in df.columns:
        return pd.DataFrame(columns=['smart_signal']) 

    # İndikatörler
    df.ta.sma(length=50, append=True)
    df.ta.mfi(length=14, append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    
    # Sütunları güvenli bir şekilde oluştur
    if 'MFI_14' not in df.columns: df['MFI_14'] = 0.0
    
    macd_cols = [c for c in df.columns if 'MACD_' in c and 'MACDs_' not in c]
    macds_cols = [c for c in df.columns if 'MACDs_' in c]
    macd_bullish = df[macd_cols[0]] > df[macds_cols[0]] if (macd_cols and macds_cols) else False

    vol_ma20 = df['Volume'].rolling(window=20).mean()
    df['smart_signal'] = (df['Volume'] > (vol_ma20 * 1.5)) & (df['MFI_14'] > 50) & (macd_bullish)
    
    return df

# --- Yan Menü ---
st.sidebar.header("Piyasa Ayarları")
market = st.sidebar.radio("Piyasa Seçiniz", ["NASDAQ 100", "BIST 100"])

# Seçime göre liste atama
target_list = nasdaq_100 if market == "NASDAQ 100" else bist_100
selected_ticker = st.sidebar.selectbox("Hisse Seçiniz", target_list)

if st.sidebar.button("Seçili Hisseyi Analiz Et"):
    df = get_data(selected_ticker)
    df_viz = df.iloc[-126:] # Son 6 ay
    
    # Patern Tespiti
    smooth_price = df_viz['Close'].rolling(window=10).mean().bfill()
    n = 15 
    min_indices = argrelextrema(smooth_price.values, np.less_equal, order=n)[0]
    max_indices = argrelextrema(smooth_price.values, np.greater_equal, order=n)[0]
    
    signals = df_viz[df_viz['smart_signal'] == True]
    
    st.subheader(f"{selected_ticker} - {market} Analiz Paneli")
    
    # Grafik
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                        row_heights=[0.7, 0.3], specs=[[{"secondary_y": False}], [{"secondary_y": True}]])
    
    fig.add_trace(go.Candlestick(x=df_viz.index, open=df_viz['Open'], high=df_viz['High'], low=df_viz['Low'], close=df_viz['Close'], name='Fiyat'), row=1, col=1)
    
    # Patern İşaretleri
    fig.add_trace(go.Scatter(x=df_viz.index[min_indices], y=df_viz['Low'].iloc[min_indices], 
                             mode='markers', name='Fincan Dibi', marker=dict(color='orange', size=10, symbol='diamond')), row=1, col=1)
    # Mavi Tepe Noktaları
    fig.add_trace(go.Scatter(x=df_viz.index[max_indices], y=df_viz['High'].iloc[max_indices], 
                             mode='markers', name='Kulp/Tepe', marker=dict(color='dodgerblue', size=10, symbol='circle')), row=1, col=1)
    
    # Akıllı Sinyal
    if not signals.empty:
        fig.add_trace(go.Scatter(x=signals.index, y=signals['Close'], mode='markers', 
                                 marker=dict(size=14, color='lime', symbol='triangle-up'), name='AKILLI SİNYAL'), row=1, col=1)
        
    fig.add_trace(go.Bar(x=df_viz.index, y=df_viz['Volume'], name='Hacim', marker_color='#26a69a'), row=2, col=1, secondary_y=False)
    fig.add_trace(go.Scatter(x=df_viz.index, y=df_viz['MFI_14'], name='MFI', line=dict(color='cyan')), row=2, col=1, secondary_y=True)
    
    fig.update_layout(height=600, template='plotly_dark', xaxis_rangeslider_visible=False)
    st.plotly_chart(fig, use_container_width=True)

# Tarayıcı
st.sidebar.markdown("---")
if st.sidebar.button(f"{market} Taramasını Başlat"):
    with st.spinner('Piyasa taranıyor...'):
        signal_list = []
        for t in target_list:
            df = get_data(t)
            
            # BURAYA DİKKAT: Sadece df doluysa ve içinde kolon varsa kontrol et
            if not df.empty and 'smart_signal' in df.columns:
                if df['smart_signal'].iloc[-1]:
                    signal_list.append(t)
        
        if signal_list:
            st.success(f"### Sinyal Veren Hisseler ({market}): {', '.join(signal_list)}")
        else:
            st.warning("Şu an kriterlere uyan hisse bulunamadı.")