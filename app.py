import streamlit as st
import pandas as pd
import numpy as np
from scipy.signal import argrelextrema
import plotly.graph_objects as go
from plotly.subplots import make_subplots


import json
import os

# Kendi oluşturduğumuz modülleri çağırıyoruz
from config import NASDAQ_100, BIST_100, NYSE
from scanner import get_scanner_data
from stoploss import get_stoploss_data

# Seçimleri kaydetmek için dosya yolu
SAVE_FILE = "selected_tickers.json"

print("APP dosyası yüklendi!")

def save_selections(tickers):
    with open(SAVE_FILE, 'w') as f:
        json.dump(list(tickers), f)

def load_selections():
    if os.path.exists(SAVE_FILE):
        try:
            with open(SAVE_FILE, 'r') as f:
                return set(json.load(f))
        except:
            return set()
    return set()

st.set_page_config(layout="wide", page_title="Yatırım Terminali")
st.title("📈 Profesyonel Yatırım Terminali")

# Yan Menü Ayarları
st.sidebar.header("Ayarlar")
market = st.sidebar.radio("Piyasa Seçimi", ["NASDAQ 100", "BIST 100", "NYSE"])
module = st.sidebar.radio("Modül Seçimi", ["Fincan-Kulp Tarayıcı", "Stop Loss Hesaplayıcı"])

# Seçilen piyasaya göre listeyi belirle
if market == "NASDAQ 100":
    target_list = NASDAQ_100
elif market == "BIST 100":
    target_list = BIST_100
else:
    target_list = NYSE

# ----------------- 1. MODÜL: FİNCAN-KULP TARAYICI -----------------
if module == "Fincan-Kulp Tarayıcı":
    st.header("🔍 Fincan-Kulp & Teknik Analiz")
    selected_ticker = st.sidebar.selectbox("Hisse Seçiniz", target_list)
    
    # Butonlar
    col_a, col_b = st.sidebar.columns(2)
    
    if col_a.button("Analiz Et"):
        df = get_scanner_data(selected_ticker)
        if df.empty:
            st.error(f"{selected_ticker} için veri alınamadı.")
        else:
            df_viz = df.iloc[-126:] # Son 6 ay
            smooth_price = df_viz['Close'].rolling(window=10).mean().bfill()
            n = 15 
            min_indices = argrelextrema(smooth_price.values, np.less_equal, order=n)[0]
            max_indices = argrelextrema(smooth_price.values, np.greater_equal, order=n)[0]
            signals = df_viz[df_viz['smart_signal'] == True]
            
            # Grafik Oluşturma
            fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                                row_heights=[0.7, 0.3], specs=[[{"secondary_y": False}], [{"secondary_y": True}]])
            
            fig.add_trace(go.Candlestick(x=df_viz.index, open=df_viz['Open'], high=df_viz['High'], low=df_viz['Low'], close=df_viz['Close'], name='Fiyat'), row=1, col=1)
            
            # Patern İşaretleri
            fig.add_trace(go.Scatter(x=df_viz.index[min_indices], y=df_viz['Low'].iloc[min_indices], mode='markers', name='Fincan Dibi', marker=dict(color='orange', size=10, symbol='diamond')), row=1, col=1)
            fig.add_trace(go.Scatter(x=df_viz.index[max_indices], y=df_viz['High'].iloc[max_indices], mode='markers', name='Kulp/Tepe', marker=dict(color='dodgerblue', size=10, symbol='circle')), row=1, col=1)
            
            if not signals.empty:
                fig.add_trace(go.Scatter(x=signals.index, y=signals['Close'], mode='markers', marker=dict(size=14, color='lime', symbol='triangle-up'), name='AKILLI SİNYAL'), row=1, col=1)
            
            fig.add_trace(go.Bar(x=df_viz.index, y=df_viz['Volume'], name='Hacim', marker_color='#26a69a'), row=2, col=1, secondary_y=False)
            fig.add_trace(go.Scatter(x=df_viz.index, y=df_viz['MFI_14'], name='MFI', line=dict(color='cyan')), row=2, col=1, secondary_y=True)
            
            fig.update_layout(height=600, template='plotly_dark', xaxis_rangeslider_visible=False)
            st.plotly_chart(fig, use_container_width=True)

    if col_b.button("Tüm Listeyi Tara"):
        with st.spinner(f'{market} taranıyor...'):
            signals = []
            for t in target_list:
                df = get_scanner_data(t)
                if not df.empty and 'smart_signal' in df.columns and df['smart_signal'].iloc[-1]:
                    signals.append(t)
            if signals:
                st.success(f"Sinyal verenler: {', '.join(signals)}")
            else:
                st.warning("Şu an kriterlere uyan hisse yok.")

# ----------------- 2. MODÜL: STOP LOSS HESAPLAYICI -----------------
elif module == "Stop Loss Hesaplayıcı":
    st.header("🛡️ Risk Yönetimi: Stop Loss Seçimi")
    
    # 1. Uygulama açıldığında dosyadan yükle (sadece bir kez)
    if 'selected_tickers' not in st.session_state:
        st.session_state.selected_tickers = load_selections()

    # 2. Hızlı Arama
    search_term = st.text_input("🔍 Hisseleri filtrelemek için yazın (örn: THY):", "").upper()

    # 3. Tabloyu hazırla
    df_selection = pd.DataFrame({'Hisse': target_list})
    df_selection['Seçili'] = df_selection['Hisse'].apply(lambda x: x in st.session_state.selected_tickers)

    if search_term:
        df_selection = df_selection[df_selection['Hisse'].str.contains(search_term)]

    # 4. İnteraktif Tablo
    edited_df = st.data_editor(
        df_selection, 
        column_config={"Seçili": st.column_config.CheckboxColumn(required=True)},
        hide_index=True,
        use_container_width=True
    )

    # 5. Değişiklikleri hem Session State'e hem de Dosyaya kaydet
    changed = False
    for index, row in edited_df.iterrows():
        if row['Seçili']:
            if row['Hisse'] not in st.session_state.selected_tickers:
                st.session_state.selected_tickers.add(row['Hisse'])
                changed = True
        else:
            if row['Hisse'] in st.session_state.selected_tickers:
                st.session_state.selected_tickers.discard(row['Hisse'])
                changed = True
    
    # Eğer bir değişiklik olduysa dosyayı güncelle
    if changed:
        save_selections(st.session_state.selected_tickers)

    st.write(f"Şu an **{len(st.session_state.selected_tickers)}** hisse seçili ve kaydedildi.")

# 6. Analizi Başlat (Analiz döngüsünün içini bu şekilde güncelle)
    if st.button("🚀 Seçilen Hisseleri Analiz Et"):
        results = []
        with st.spinner('Analiz yapılıyor...'):
            for t in st.session_state.selected_tickers:
                data = get_stoploss_data(t)
                if data is not None:
                    results.append({
                        "Hisse": t, 
                        "Fiyat": round(float(data['Close']), 2),
                        "Yıllık Vol %": f"%{data['Volatility']}",
                        "Maks. Günlük Düşüş": f"%{data['Max_Daily_Drop']}",
                        "Tipik Günlük Düşüş": f"%{data['Typical_Drop']}",
                        "Fiyat Değişim Histogramı": data['Histogram'], 
                        "1.5x ATR": f"{data['SL_1.5']} (%{data['Risk_1.5']})",
                        "2.0x ATR": f"{data['SL_2.0']} (%{data['Risk_2.0']})",
                        "3.0x ATR": f"{data['SL_3.0']} (%{data['Risk_3.0']})"
                    })
        
        if results:
            df_res = pd.DataFrame(results)
            st.subheader("📊 Detaylı Stop Loss Analizi")
            st.dataframe(df_res, use_container_width=True, hide_index=True)