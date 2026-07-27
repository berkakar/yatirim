import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import json
import os

from config import NASDAQ_100, BIST_100, NYSE
from scanner import get_scanner_data
from stoploss import get_stoploss_data

SAVE_FILE = "selected_tickers.json"

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

# Sayfa Yapılandırması
st.set_page_config(layout="wide", page_title="Yatırım Terminali")
st.title("📈 Profesyonel Yatırım Terminali")

# ------------------------------------------------------------------------------
# YAN MENÜ (SIDEBAR) AYARLARI
# ------------------------------------------------------------------------------
st.sidebar.header("Ayarlar")
market = st.sidebar.radio("Piyasa Seçimi", ["NASDAQ 100", "BIST 100", "NYSE"])

# Modül Seçimi (Bağımsız Grafik Modülü en altta)
module = st.sidebar.radio(
    "Modül Seçimi", 
    [
        "Fincan-Kulp Tarayıcı", 
        "OBO & TOBO Tarayıcı", 
        "Stop Loss Hesaplayıcı",
        "📊 Bağımsız Hisse Grafiği"
    ]
)

if market == "NASDAQ 100":
    target_list = NASDAQ_100
elif market == "BIST 100":
    target_list = BIST_100
else:
    target_list = NYSE

# --- OTURUM DURUMU (SESSION STATE) KONTROLLERİ ---
if 'current_module' not in st.session_state or st.session_state.current_module != module:
    st.session_state.current_module = module
    st.session_state.show_chart = False
    st.session_state.selected_ticker = None

if 'current_market' not in st.session_state or st.session_state.current_market != market:
    st.session_state.current_market = market
    st.session_state.show_chart = False
    st.session_state.selected_ticker = None

def render_chart_for(ticker):
    st.session_state.selected_ticker = ticker
    st.session_state.show_chart = True


# ==============================================================================
# 1. MODÜL: FİNCAN-KULP TARAYICI
# ==============================================================================
if module == "Fincan-Kulp Tarayıcı":
    st.header("🔍 Fincan-Kulp Tarayıcı")
    st.caption("Aşağıdaki butona basarak seçili piyasadaki formasyonları taratabilirsiniz.")
    
    if st.button("🚀 Fincan-Kulp Listesini Tara", type="primary"):
        with st.spinner(f'{market} listesi taranıyor...'):
            signals = []
            for t in target_list:
                df_temp, cup, _, _ = get_scanner_data(t)
                if df_temp is not None and not df_temp.empty and cup is not None:
                    if isinstance(cup, dict) and all(k in cup for k in ['A', 'B', 'C', 'D']):
                        if t not in signals:
                            signals.append(t)
            st.session_state.cup_signals = signals

    # Tarama Sonuçları Listesi
    if 'cup_signals' in st.session_state and st.session_state.cup_signals:
        st.subheader("🎯 Bulunan Fincan-Kulp Formasyonları")
        st.info("Grafiğini incelemek istediğiniz hissenin butonuna tıklayın:")
        cols = st.columns(min(len(st.session_state.cup_signals), 5))
        for idx, t_sig in enumerate(st.session_state.cup_signals):
            col_idx = idx % 5
            if cols[col_idx].button(f"📊 {t_sig}", key=f"btn_cup_{idx}_{t_sig}"):
                render_chart_for(t_sig)
                
    elif 'cup_signals' in st.session_state:
        st.warning("Tarama sonucunda uygun formasyon bulunamadı.")

    # Taranan hisselerden birine tıklandıysa altına grafiği çiz
    if st.session_state.show_chart and st.session_state.selected_ticker:
        active_t = st.session_state.selected_ticker
        st.write("---")
        st.markdown(f"### 📊 Formasyon Analiz Grafiği: **{active_t}**")
        df, cup_pat, _, _ = get_scanner_data(active_t)
        if df is not None and not df.empty:
            df_viz = df.iloc[-126:]
            fig = go.Figure(data=[go.Candlestick(
                x=df_viz['Date'], open=df_viz['Open'], high=df_viz['High'],
                low=df_viz['Low'], close=df_viz['Close'], name='Fiyat'
            )])
            if cup_pat and isinstance(cup_pat, dict) and all(k in cup_pat for k in ['A', 'B', 'C', 'D']):
                fig.add_trace(go.Scatter(
                    x=[cup_pat['A']['Date'], cup_pat['B']['Date'], cup_pat['C']['Date']],
                    y=[float(cup_pat['A']['Close']), float(cup_pat['B']['Close']), float(cup_pat['C']['Close'])],
                    mode='lines+markers+text', name='Fincan',
                    line=dict(color='#00d2ff', width=3), text=['A', 'B', 'C'], textposition="top center"
                ))
                fig.add_trace(go.Scatter(
                    x=[cup_pat['C']['Date'], cup_pat['D']['Date']],
                    y=[float(cup_pat['C']['Close']), float(cup_pat['D']['Close'])],
                    mode='lines+markers+text', name='Kulp',
                    line=dict(color='#ff5e62', width=3, dash='dash'), text=['', 'D'], textposition="bottom center"
                ))
            fig.update_layout(title=f"{active_t} - Fincan Kulp Grafiği", template="plotly_dark", height=500, xaxis_rangeslider_visible=False)
            st.plotly_chart(fig, use_container_width=True)


# ==============================================================================
# 2. MODÜL: OBO & TOBO TARAYICI
# ==============================================================================
elif module == "OBO & TOBO Tarayıcı":
    st.header("📉 Omuz Baş Omuz (OBO) & Ters OBO Tarayıcı")
    st.caption("Aşağıdaki butona basarak seçili piyasadaki formasyonları taratabilirsiniz.")
    
    if st.button("🚀 OBO & TOBO Listesini Tara", type="primary"):
        with st.spinner(f'{market} listesi taranıyor...'):
            signals = []
            for t in target_list:
                df_temp, _, obo, tobo = get_scanner_data(t)
                if df_temp is None or df_temp.empty or 'Close' not in df_temp.columns:
                    continue
                form_type = None
                if obo is not None and isinstance(obo, dict) and all(k in obo for k in ['left_shoulder', 'head', 'right_shoulder']):
                    form_type = "OBO"
                elif tobo is not None and isinstance(tobo, dict) and all(k in tobo for k in ['left_shoulder', 'head', 'right_shoulder']):
                    form_type = "TOBO"
                
                if form_type:
                    item = {"ticker": t, "type": form_type}
                    if item not in signals:
                        signals.append(item)
                        
            st.session_state.obo_signals = signals

    # Tarama Sonuçları Listesi
    if 'obo_signals' in st.session_state and st.session_state.obo_signals:
        st.subheader("📉 Bulunan OBO / TOBO Formasyonları")
        st.info("Grafiğini incelemek istediğiniz hissenin butonuna tıklayın:")
        cols = st.columns(min(len(st.session_state.obo_signals), 5))
        for idx, sig_item in enumerate(st.session_state.obo_signals):
            col_idx = idx % 5
            t_sig = sig_item["ticker"]
            f_type = sig_item["type"]
            label = f"{t_sig} ({'⚠️ OBO' if f_type == 'OBO' else '🚀 TOBO'})"

            if cols[col_idx].button(label, key=f"btn_obo_{idx}_{t_sig}"):
                render_chart_for(t_sig)
    elif 'obo_signals' in st.session_state:
        st.warning("Tarama sonucunda uygun formasyon bulunamadı.")

    # Taranan hisselerden birine tıklandıysa altına grafiği çiz
    if st.session_state.show_chart and st.session_state.selected_ticker:
        active_t = st.session_state.selected_ticker
        st.write("---")
        st.markdown(f"### 📊 Formasyon Analiz Grafiği: **{active_t}**")
        df, _, obo_pat, tobo_pat = get_scanner_data(active_t)
        if df is not None and not df.empty:
            df_viz = df.iloc[-126:]
            fig = go.Figure(data=[go.Candlestick(
                x=df_viz['Date'], open=df_viz['Open'], high=df_viz['High'],
                low=df_viz['Low'], close=df_viz['Close'], name='Fiyat'
            )])
            if obo_pat and isinstance(obo_pat, dict) and all(k in obo_pat for k in ['left_shoulder', 'head', 'right_shoulder']):
                ls, h, rs = obo_pat['left_shoulder'], obo_pat['head'], obo_pat['right_shoulder']
                fig.add_trace(go.Scatter(
                    x=[ls['Date'], h['Date'], rs['Date']],
                    y=[float(ls['Close']), float(h['Close']), float(rs['Close'])],
                    mode='lines+markers+text', name='OBO',
                    line=dict(color='#ff0055', width=3), text=['Sol', 'Baş', 'Sağ'], textposition="top center"
                ))
            elif tobo_pat and isinstance(tobo_pat, dict) and all(k in tobo_pat for k in ['left_shoulder', 'head', 'right_shoulder']):
                ls, h, rs = tobo_pat['left_shoulder'], tobo_pat['head'], tobo_pat['right_shoulder']
                fig.add_trace(go.Scatter(
                    x=[ls['Date'], h['Date'], rs['Date']],
                    y=[float(ls['Close']), float(h['Close']), float(rs['Close'])],
                    mode='lines+markers+text', name='TOBO',
                    line=dict(color='#00ff66', width=3), text=['Sol', 'Baş', 'Sağ'], textposition="bottom center"
                ))
            fig.update_layout(title=f"{active_t} - OBO/TOBO Grafiği", template="plotly_dark", height=500, xaxis_rangeslider_visible=False)
            st.plotly_chart(fig, use_container_width=True)


# ==============================================================================
# 3. MODÜL: STOP LOSS HESAPLAYICI (EMA SÜTUNLARI EKLENDİ)
# ==============================================================================
elif module == "Stop Loss Hesaplayıcı":
    st.header("🛡️ Risk Yönetimi: Stop Loss & EMA Analizi")
    
    if 'selected_tickers' not in st.session_state:
        st.session_state.selected_tickers = load_selections()

    search_term = st.text_input("🔍 Hisseleri filtrelemek için yazın (örn: THY):", "").upper()

    df_selection = pd.DataFrame({'Hisse': target_list})
    df_selection['Seçili'] = df_selection['Hisse'].apply(lambda x: x in st.session_state.selected_tickers)

    if search_term:
        df_selection = df_selection[df_selection['Hisse'].str.contains(search_term)]

    edited_df = st.data_editor(
        df_selection, 
        column_config={"Seçili": st.column_config.CheckboxColumn(required=True)},
        hide_index=True,
        use_container_width=True
    )

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
    
    if changed:
        save_selections(st.session_state.selected_tickers)

    st.write(f"Şu an **{len(st.session_state.selected_tickers)}** hisse seçili ve kaydedildi.")

    if st.button("🚀 Seçilen Hisseleri Analiz Et"):
        results = []
        with st.spinner('Stop loss ve hareketli ortalama analizleri yapılıyor...'):
            for t in st.session_state.selected_tickers:
                data = get_stoploss_data(t)
                if data is not None and isinstance(data, dict):
                    try:
                        # EMA200 eksi/artı işareti veya yetersiz veri kontrolü
                        ema200_val = data.get('EMA200_Dist', '-')
                        ema200_str = f"%{ema200_val:+.2f}" if isinstance(ema200_val, (int, float)) else "-"

                        results.append({
                            "Hisse": t, 
                            "Fiyat": round(float(data.get('Close', 0)), 2),
                            "EMA20 Fark": f"%{data.get('EMA20_Dist', 0):+.2f}",
                            "EMA50 Fark": f"%{data.get('EMA50_Dist', 0):+.2f}",
                            "EMA200 Fark": ema200_str,
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
            st.subheader("📊 Detaylı Stop Loss & EMA Analizi")
            st.dataframe(df_res, use_container_width=True, hide_index=True)


# ==============================================================================
# 4. MODÜL: BAĞIMSIZ HİSSE GRAFİĞİ (SOL MENÜNÜN EN ALTINDAKİ SEÇENEK)
# ==============================================================================
elif module == "📊 Bağımsız Hisse Grafiği":
    st.header("📊 Bağımsız Hisse Senedi Grafiği İnceleme")
    st.caption("Aşağıdaki listeden dilediğiniz hisseyi seçip butona basarak grafiğini inceleyebilirsiniz.")
    
    col_select, col_btn = st.columns([3, 1])
    
    with col_select:
        chosen_ticker = st.selectbox(
            "Grafiğini görmek istediğiniz hisseyi seçin:", 
            target_list, 
            key="standalone_selectbox"
        )
        
    with col_btn:
        st.write("<br>", unsafe_allow_html=True)
        if st.button("📈 Grafiği Göster", use_container_width=True, type="primary"):
            render_chart_for(chosen_ticker)

    # YALNIZCA BUTONA BASILDIĞINDA GRAFİK ÇİZİLİR
    if st.session_state.show_chart and st.session_state.selected_ticker:
        active_t = st.session_state.selected_ticker
        st.write("---")
        st.markdown(f"### 📈 Fiyat Grafiği: **{active_t}**")
        
        with st.spinner(f"{active_t} verileri getiriliyor..."):
            df, cup_pat, obo_pat, tobo_pat = get_scanner_data(active_t)
            
            if df is None or df.empty or 'Close' not in df.columns:
                st.error(f"❌ {active_t} için geçerli piyasa verisi alınamadı.")
            else:
                df_viz = df.iloc[-126:] # Son 6 aylık veri
                fig = go.Figure(data=[go.Candlestick(
                    x=df_viz['Date'], open=df_viz['Open'], high=df_viz['High'],
                    low=df_viz['Low'], close=df_viz['Close'], name='Fiyat'
                )])
                
                fig.update_layout(
                    title=f"{active_t} - Mum Grafiği", 
                    template="plotly_dark", 
                    height=550, 
                    xaxis_rangeslider_visible=False
                )
                st.plotly_chart(fig, use_container_width=True)