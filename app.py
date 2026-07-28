import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import json
import os

from config import load_ticker_lists, save_ticker_lists, DEFAULT_NASDAQ_100, DEFAULT_NYSE, DEFAULT_BIST_100
from scanner import get_scanner_data
from stoploss import get_stoploss_data
from valuation import get_valuation_data, style_valuation_df

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
# DİNAMİK LİSTE YÜKLEME VE SESSION STATE
# ------------------------------------------------------------------------------
if 'ticker_lists' not in st.session_state:
    st.session_state.ticker_lists = load_ticker_lists()

# ------------------------------------------------------------------------------
# YAN MENÜ (SIDEBAR) AYARLARI
# ------------------------------------------------------------------------------
st.sidebar.header("Ayarlar")
market = st.sidebar.radio("Piyasa Seçimi", ["NASDAQ 100", "BIST 100", "NYSE"])

module = st.sidebar.radio(
    "Modül Seçimi", 
    [
        "Fincan-Kulp Tarayıcı", 
        "OBO & TOBO Tarayıcı", 
        "Stop Loss Hesaplayıcı",
        "💎 Değerleme & Ucuzluk Skoru",
        "📊 Bağımsız Hisse Grafiği",
        "⚙️ Hisse Listelerini Yönet"
    ]
)

target_list = st.session_state.ticker_lists[market]

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

    if 'cup_signals' in st.session_state and st.session_state.cup_signals:
        st.subheader("🎯 Bulunan Fincan-Kulp Formasyonları")
        cols = st.columns(min(len(st.session_state.cup_signals), 5))
        for idx, t_sig in enumerate(st.session_state.cup_signals):
            col_idx = idx % 5
            if cols[col_idx].button(f"📊 {t_sig}", key=f"btn_cup_{idx}_{t_sig}"):
                render_chart_for(t_sig)
    elif 'cup_signals' in st.session_state:
        st.warning("Tarama sonucunda uygun formasyon bulunamadı.")

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

    if 'obo_signals' in st.session_state and st.session_state.obo_signals:
        st.subheader("📉 Bulunan OBO / TOBO Formasyonları")
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
# 3. MODÜL: STOP LOSS HESAPLAYICI
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

        if results:
            df_res = pd.DataFrame(results)
            st.subheader("📊 Detaylı Stop Loss & EMA Analizi")
            st.dataframe(df_res, use_container_width=True, hide_index=True)


# ==============================================================================
# 4. MODÜL: DEĞERLEME & UCUZLUK SKORU (YENİ EKLENEN MODÜL)
# ==============================================================================
elif module == "💎 Değerleme & Ucuzluk Skoru":
    st.header("💎 Temel Analiz: Değerleme & Ucuzluk Skor Kartı")
    st.caption("F/K, PD/DD, FD/FAVÖK, PEG, ROE ve Borçluluk kriterlerini birleştirerek hissenin ucuzluğunu 0-100 arasında puanlar.")

    if 'selected_tickers' not in st.session_state:
        st.session_state.selected_tickers = load_selections()

    col_mode, col_sec = st.columns([2, 2])
    
    with col_mode:
        scan_mode = st.radio(
            "Tarama Kapsamı:", 
            ["Sadece Kaydedilmiş/Seçili Hisseler", f"Tüm {market} Endeksini Tara ({len(target_list)} Hisse)"],
            horizontal=True
        )

    scan_list = list(st.session_state.selected_tickers) if "Sadece" in scan_mode else target_list

    if st.button("🚀 Değerleme Analizini Başlat", type="primary"):
        if not scan_list:
            st.warning("⚠️ Lütfen analiz etmek için en az bir hisse seçin.")
        else:
            val_results = []
            progress_bar = st.progress(0)
            status_text = st.empty()

            for i, ticker in enumerate(scan_list):
                status_text.text(f"Analiz ediliyor ({i+1}/{len(scan_list)}): {ticker}")
                res = get_valuation_data(ticker)
                if res:
                    val_results.append(res)
                progress_bar.progress((i + 1) / len(scan_list))

            status_text.empty()
            progress_bar.empty()
            st.session_state.val_results = val_results

    if 'val_results' in st.session_state and st.session_state.val_results:
        df_val = pd.DataFrame(st.session_state.val_results)

        # Sektöre göre alfabetik, skora göre büyükten küçüğe sırala
        df_val = df_val.sort_values(by=["Sektör", "Nihai Skor"], ascending=[True, False])

        # Sektör Filtresi
        all_sectors = ["Tüm Sektörler"] + list(df_val["Sektör"].unique())
        selected_sector = st.selectbox("🎯 Sektör Filtresi:", all_sectors)

        if selected_sector != "Tüm Sektörler":
            df_val = df_val[df_val["Sektör"] == selected_sector]

        st.subheader(f"📊 Değerleme Sonuçları ({len(df_val)} Hisse)")

        # Kolon Başlığı İpuçları (Hint / Tooltip Yapılandırması)
        column_config = {
            "Hisse": st.column_config.TextColumn("Hisse", help="Hisse Senedi Kodu"),
            "Sektör": st.column_config.TextColumn("Sektör", help="Şirketin Faaliyet Sektörü"),
            "Nihai Skor": st.column_config.NumberColumn("Nihai Skor (0-100)", help="💡 70+ Yeşil: Ucuz ve Kaliteli Kalmış Potential\n💡 40 Altı Kırmızı: Pahalı/Riskli"),
            "F/K": st.column_config.NumberColumn("F/K", help="💡 Fiyat/Kazanç: 10 altı UCUZ, 20 üzeri PAHALI (kırmızı) kabul edilir."),
            "PD/DD": st.column_config.NumberColumn("PD/DD", help="💡 Piyasa/Defter Değeri: 1.5 altı UCUZ, 4.0 üzeri PAHALI (kırmızı) kabul edilir."),
            "FD/FAVÖK": st.column_config.NumberColumn("FD/FAVÖK", help="💡 Firma Değeri/FAVÖK: 7 altı UCUZ, 15 üzeri PAHALI (kırmızı) kabul edilir."),
            "PEG": st.column_config.NumberColumn("PEG", help="💡 F/K ÷ Büyüme: 1.0 altı UCUZ (Büyümesine kıyasla cazip), 1.8 üzeri PAHALI."),
            "Özkaynak Karlılığı %": st.column_config.NumberColumn("Özkaynak Karlılığı %", help="💡 ROE: %25+ Yüksek Karlılık. %10 altı DÜŞÜK/VERİMSİZ (kırmızı)."),
            "Net Borç / FAVÖK": st.column_config.NumberColumn("Net Borç / FAVÖK", help="💡 Borçluluk: 1.5 altı ÇOK SAĞLIKLI, 3.5 üzeri YÜKSEK BORÇ RİSKİ (kırmızı)."),
            "FCF Verimi %": st.column_config.NumberColumn("FCF Verimi %", help="💡 Serbest Nakit Akışı Verimi: %8 üzeri güçlü nakit makinesi demektir.")
        }

        # Renklendirilmiş Tabloyu Çizdir
        styled_df = style_valuation_df(df_val)
        st.dataframe(styled_df, column_config=column_config, use_container_width=True, hide_index=True)


# ==============================================================================
# 5. MODÜL: BAĞIMSIZ HİSSE GRAFİĞİ
# ==============================================================================
elif module == "📊 Bağımsız Hisse Grafiği":
    st.header("📊 Bağımsız Hisse Senedi Grafiği İnceleme")
    
    col_select, col_btn = st.columns([3, 1])
    with col_select:
        chosen_ticker = st.selectbox("Grafiğini görmek istediğiniz hisseyi seçin:", target_list, key="standalone_selectbox")
        
    with col_btn:
        st.write("<br>", unsafe_allow_html=True)
        if st.button("📈 Grafiği Göster", use_container_width=True, type="primary"):
            render_chart_for(chosen_ticker)

    if st.session_state.show_chart and st.session_state.selected_ticker:
        active_t = st.session_state.selected_ticker
        st.write("---")
        st.markdown(f"### 📈 Fiyat Grafiği: **{active_t}**")
        
        with st.spinner(f"{active_t} verileri getiriliyor..."):
            df, cup_pat, obo_pat, tobo_pat = get_scanner_data(active_t)
            
            if df is None or df.empty or 'Close' not in df.columns:
                st.error(f"❌ {active_t} için geçerli piyasa verisi alınamadı.")
            else:
                df_viz = df.iloc[-126:]
                fig = go.Figure(data=[go.Candlestick(
                    x=df_viz['Date'], open=df_viz['Open'], high=df_viz['High'],
                    low=df_viz['Low'], close=df_viz['Close'], name='Fiyat'
                )])
                
                fig.update_layout(title=f"{active_t} - Mum Grafiği", template="plotly_dark", height=550, xaxis_rangeslider_visible=False)
                st.plotly_chart(fig, use_container_width=True)


# ==============================================================================
# 6. MODÜL: HİSSE LİSTELERİNİ YÖNET
# ==============================================================================
elif module == "⚙️ Hisse Listelerini Yönet":
    st.header("⚙️ Hisse Listelerini Düzenleme ve Kalıcı Kaydetme")

    selected_m = st.selectbox("Düzenlenecek Piyasayı Seçin:", ["NASDAQ 100", "BIST 100", "NYSE"])
    current_market_list = st.session_state.ticker_lists[selected_m]

    col_add, col_del = st.columns(2)

    with col_add:
        st.subheader("➕ Yeni Hisse Ekle")
        new_symbol = st.text_input("Hisse Sembolü (Örn: NVDA veya TUPRS):", "").upper().strip()
        
        if st.button("Listeye Ekle", type="primary"):
            if new_symbol:
                if selected_m == "BIST 100" and not new_symbol.endswith(".IS"):
                    new_symbol += ".IS"

                if new_symbol in current_market_list:
                    st.warning(f"⚠️ **{new_symbol}** zaten {selected_m} listesinde mevcut.")
                else:
                    st.session_state.ticker_lists[selected_m].append(new_symbol)
                    save_ticker_lists(st.session_state.ticker_lists)
                    st.success(f"✅ **{new_symbol}**, {selected_m} listesine eklendi ve kaydedildi!")
                    st.rerun()

    with col_del:
        st.subheader("🗑️ Hisse Çıkar")
        symbol_to_remove = st.selectbox("Listeden çıkarmak istediğiniz hisse:", current_market_list)
        
        if st.button("Listeden Çıkar", type="secondary"):
            if symbol_to_remove in current_market_list:
                st.session_state.ticker_lists[selected_m].remove(symbol_to_remove)
                save_ticker_lists(st.session_state.ticker_lists)
                st.success(f"🗑️ **{symbol_to_remove}**, {selected_m} listesinden çıkarıldı!")
                st.rerun()

    st.write("---")
    st.subheader(f"📋 Güncel {selected_m} Listesi ({len(current_market_list)} Hisse)")
    st.write(", ".join(current_market_list))

    st.write("<br>", unsafe_allow_html=True)
    if st.button("🔄 Orijinal Varsayılan Listelere Dön (Sıfırla)"):
        st.session_state.ticker_lists = {
            "NASDAQ 100": list(dict.fromkeys(DEFAULT_NASDAQ_100)),
            "NYSE": list(dict.fromkeys(DEFAULT_NYSE)),
            "BIST 100": list(dict.fromkeys(DEFAULT_BIST_100))
        }
        save_ticker_lists(st.session_state.ticker_lists)
        st.success("Tüm listeler varsayılan ayarlara sıfırlandı!")
        st.rerun()