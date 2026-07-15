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
    
    col_a, col_b = st.sidebar.columns(2)
    
    if col_a.button("Analiz Et"):
        df = get_scanner_data(selected_ticker)
        
        # DÜZELTME: df'in None olup olmadığını ve boş olup olmadığını önce kontrol et
        if df is None or df.empty:
            st.error(f"{selected_ticker} için veri alınamadı veya liste boş.")
        else:
            df_viz = df.iloc[-126:] 
            # ... geri kalan kodlar aynı ...

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

# 6. Analizi Başlat
    if st.button("🚀 Seçilen Hisseleri Analiz Et"):
        results = []
        with st.spinner('Analiz yapılıyor...'):
            for t in st.session_state.selected_tickers:
                data = get_stoploss_data(t)
                
                # DÜZELTME: Verinin None olmadığını VE sözlük/objenin beklenen anahtarlara sahip olduğunu kontrol et
                if data is not None and isinstance(data, dict):
                    try:
                        results.append({
                            "Hisse": t, 
                            "Fiyat": round(float(data.get('Close', 0)), 2),
                            "Yıllık Vol %": f"%{data.get('Volatility', 0)}",
                            "Maks. Günlük Düşüş": f"%{data.get('Max_Daily_Drop', 0)}",
                            "Tipik Günlük Düşüş": f"%{data.get('Typical_Drop', 0)}",
                            "Fiyat Değişim Histogramı": data.get('Histogram', []), 
                            "1.5x ATR": f"{data.get('SL_1.5', '-')} (%{data.get('Risk_1.5', '-')})",
                            "2.0x ATR": f"{data.get('SL_2.0', '-')} (%{data.get('Risk_2.0', '-')})",
                            "3.0x ATR": f"{data.get('SL_3.0', '-')} (%{data.get('Risk_3.0', '-')})"
                        })
                    except Exception as e:
                        st.warning(f"{t} verisi işlenirken hata oluştu: {e}")
                else:
                    st.warning(f"{t} için geçerli veri alınamadı.")

        if results:
            df_res = pd.DataFrame(results)
            st.subheader("📊 Detaylı Stop Loss Analizi")
            st.dataframe(df_res, use_container_width=True, hide_index=True)

