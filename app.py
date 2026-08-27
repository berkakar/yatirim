import streamlit as st
import streamlit_authenticator as stauth
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import json
import os

from config import load_ticker_lists, save_ticker_lists, GITHUB_REPO, DEFAULT_NASDAQ_100, DEFAULT_NYSE, DEFAULT_BIST_100
from github_config import read_json_from_github, write_json_to_github
from scanner import get_scanner_data
from stoploss import get_stoploss_data
from valuation import fetch_single_ticker_raw, calculate_sector_relative_scores, style_valuation_df
from dtw_analysis import (
    fetch_and_cache_5m_data,
    compute_dtw_similarity,
    compute_cross_similarity_parallel,
    find_local_extremes,
    load_cached_dtw_results,
    save_cached_dtw_results
)
from alpaca_client import AlpacaClient
from alpaca_dashboard import render_alpaca_dashboard
from premium_buy_portfolio import render_premium_buy_portfolio

NAV_HOME = "🏠 Özet"
MODULE_GROUPS = {
    "🔍 Tarama": ["Fincan-Kulp Tarayıcı", "OBO & TOBO Tarayıcı"],
    "📊 Analiz": [
        "Stop Loss Hesaplayıcı",
        "💎 Değerleme & Ucuzluk Skoru",
        "🔄 DTW Zaman Serisi & Benzerlik Analizi",
        "📊 Bağımsız Hisse Grafiği",
    ],
    "💼 Portföy": ["🦙 Alpaca Canlı Pozisyonlar", "🎯 Premium Buy Point Portföyü"],
    "⚙️ Ayarlar": ["⚙️ Hisse Listelerini Yönet"],
}
# Modül düğmelerinde gösterilecek ikonlu etiketler (yönlendirme için kullanılan
# değerler MODULE_GROUPS'takiyle aynı kalır, sadece görünen metin değişir)
MODULE_DISPLAY = {
    "Fincan-Kulp Tarayıcı": "🔍 Fincan-Kulp Tarayıcı",
    "OBO & TOBO Tarayıcı": "📉 OBO & TOBO Tarayıcı",
    "Stop Loss Hesaplayıcı": "🛡️ Stop Loss Hesaplayıcı",
}

def _save_file(username):
    return f"selected_tickers_{username}.json"

def get_user_alpaca_creds(username):
    """Kullanıcıya özel Alpaca anahtarlarını secrets.toml'daki [alpaca.<username>]
    bölümünden okur. Tanımlı değilse (None, None) döner."""
    user_alpaca = st.secrets.get("alpaca", {}).get(username, {})
    return user_alpaca.get("key_id"), user_alpaca.get("secret_key")

def save_selections(tickers, username):
    """Seçili hisseleri kalıcı olması için GitHub'a commit'ler (mümkün olduğunda),
    ayrıca yerel dosyaya da yazar - bkz. config.save_ticker_lists için aynı gerekçe."""
    save_file = _save_file(username)
    token = st.secrets.get("GITHUB_TOKEN")
    if token:
        try:
            write_json_to_github(GITHUB_REPO, token, save_file, list(tickers), f"Update selected tickers ({username})")
        except Exception as e:
            st.warning(f"⚠️ Seçili hisseler GitHub'a kalıcı olarak kaydedilemedi (sadece bu oturumda geçerli olacak): {e}")

    with open(save_file, 'w') as f:
        json.dump(list(tickers), f)

def load_selections(username):
    save_file = _save_file(username)
    token = st.secrets.get("GITHUB_TOKEN")
    if token:
        try:
            data = read_json_from_github(GITHUB_REPO, token, save_file, None)
            if data is not None:
                return set(data)
        except Exception:
            pass

    if os.path.exists(save_file):
        try:
            with open(save_file, 'r') as f:
                return set(json.load(f))
        except:
            return set()
    return set()

# Sayfa Yapılandırması
st.set_page_config(layout="wide", page_title="Yatırım Terminali")

# ------------------------------------------------------------------------------
# GİRİŞ (AUTHENTICATION)
# ------------------------------------------------------------------------------
_credentials = {"usernames": {u: dict(v) for u, v in st.secrets["credentials"]["usernames"].items()}}
authenticator = stauth.Authenticate(
    _credentials,
    st.secrets["cookie"]["name"],
    st.secrets["cookie"]["key"],
    st.secrets["cookie"]["expiry_days"],
)
authenticator.login(location="main")

_auth_status = st.session_state.get("authentication_status")
if _auth_status is False:
    st.error("❌ Kullanıcı adı veya şifre hatalı.")
    st.stop()
elif _auth_status is None:
    st.warning("🔒 Devam etmek için giriş yapın.")
    st.stop()

username = st.session_state["username"]

st.title("📈 Profesyonel Yatırım Terminali")
authenticator.logout("🚪 Çıkış Yap", "sidebar")

# ------------------------------------------------------------------------------
# DİNAMİK LİSTE YÜKLEME VE SESSION STATE
# ------------------------------------------------------------------------------
if 'ticker_lists' not in st.session_state:
    st.session_state.ticker_lists = load_ticker_lists(username)

# ------------------------------------------------------------------------------
# YAN MENÜ (SIDEBAR) AYARLARI
# ------------------------------------------------------------------------------
market = st.sidebar.selectbox("Piyasa Seçimi", ["NASDAQ 100", "NYSE", "BIST 100"])

st.sidebar.divider()
st.markdown("""
<style>
[data-testid="stSidebar"] [data-testid="stButtonGroup"] > div {
    display: flex !important;
    flex-direction: column !important;
    align-items: flex-start !important;
    gap: 0.4rem;
}
[data-testid="stSidebar"] [data-testid="stButtonGroup"] button {
    writing-mode: vertical-rl !important;
    transform: rotate(180deg);
    white-space: nowrap;
    width: 2.4rem !important;
    min-width: 2.4rem !important;
    max-width: 2.4rem !important;
    height: auto !important;
    min-height: 7.5rem !important;
    padding: 0.5rem 0.3rem !important;
    font-size: 16.7px !important;
}
[data-testid="stSidebar"] [data-testid="stButtonGroup"] button * {
    writing-mode: inherit !important;
}
</style>
""", unsafe_allow_html=True)
if "nav_category" not in st.session_state:
    st.session_state["nav_category"] = NAV_HOME

nav_col_tabs, nav_col_modules = st.sidebar.columns([1, 4], gap="small")
with nav_col_tabs:
    category = st.segmented_control(
        "Kategori", [NAV_HOME] + list(MODULE_GROUPS.keys()),
        key="nav_category", required=True, label_visibility="collapsed",
    )

if category == NAV_HOME:
    module = NAV_HOME
else:
    modules_in_category = MODULE_GROUPS[category]
    module_state_key = f"active_module_{category}"
    if module_state_key not in st.session_state:
        st.session_state[module_state_key] = modules_in_category[0]
    module = st.session_state[module_state_key]

    def _select_module(state_key, mod_name):
        st.session_state[state_key] = mod_name

    with nav_col_modules:
        for mod_name in modules_in_category:
            st.button(
                MODULE_DISPLAY.get(mod_name, mod_name),
                key=f"navbtn_{category}_{mod_name}",
                use_container_width=True,
                type="primary" if mod_name == module else "secondary",
                on_click=_select_module,
                args=(module_state_key, mod_name),
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
# 0. MODÜL: ÖZET (ANA SAYFA)
# ==============================================================================
if module == NAV_HOME:
    st.header("🏠 Genel Bakış")
    st.caption("Hesap özeti, liste durumu ve bu oturumdaki son tarama sonuçları.")

    key_id, secret_key = get_user_alpaca_creds(username)

    if key_id and secret_key:
        try:
            client = AlpacaClient(key_id, secret_key)
            positions = client.get_all_positions()
            total_value = sum(float(p["market_value"]) for p in positions)
            total_pl = sum(float(p["unrealized_pl"]) for p in positions)
            total_cost = sum(float(p["cost_basis"]) for p in positions)
            total_pl_pct = (total_pl / total_cost * 100) if total_cost else 0.0

            c1, c2, c3 = st.columns(3)
            c1.metric("Açık Pozisyon", len(positions))
            c2.metric("Toplam Pozisyon Değeri", f"${total_value:,.2f}")
            c3.metric("Toplam Kâr/Zarar", f"${total_pl:,.2f}", f"{total_pl_pct:+.2f}%")
        except Exception as e:
            st.warning(f"⚠️ Alpaca hesap özeti alınamadı: {e}")
    else:
        st.info(f"'{username}' için Alpaca hesabı tanımlı değil (`.streamlit/secrets.toml` içinde `[alpaca.{username}]`).")

    st.divider()
    st.subheader("📋 Hisse Listeleri")
    lc1, lc2, lc3 = st.columns(3)
    lc1.metric("NASDAQ 100 Listesi", len(st.session_state.ticker_lists["NASDAQ 100"]))
    lc2.metric("NYSE Listesi", len(st.session_state.ticker_lists["NYSE"]))
    lc3.metric("BIST 100 Listesi", len(st.session_state.ticker_lists["BIST 100"]))

    st.divider()
    st.subheader("🎯 Bu Oturumdaki Son Tarama Sonuçları")
    sc1, sc2 = st.columns(2)
    with sc1:
        if 'cup_signals' in st.session_state:
            st.metric("Fincan-Kulp Sinyali", len(st.session_state.cup_signals))
        else:
            st.info("Fincan-Kulp Tarayıcı bu oturumda henüz çalıştırılmadı.")
    with sc2:
        if 'obo_signals' in st.session_state:
            st.metric("OBO / TOBO Sinyali", len(st.session_state.obo_signals))
        else:
            st.info("OBO & TOBO Tarayıcı bu oturumda henüz çalıştırılmadı.")

    st.divider()
    st.subheader("🚀 Hızlı Erişim")

    def _go_to_category(cat_name):
        st.session_state["nav_category"] = cat_name

    nav_cols = st.columns(len(MODULE_GROUPS))
    for col, cat_name in zip(nav_cols, MODULE_GROUPS.keys()):
        col.button(
            cat_name, use_container_width=True, key=f"quicknav_{cat_name}",
            on_click=_go_to_category, args=(cat_name,),
        )

# ==============================================================================
# 1. MODÜL: FİNCAN-KULP TARAYICI
# ==============================================================================
elif module == "Fincan-Kulp Tarayıcı":
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
        st.session_state.selected_tickers = load_selections(username)

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
        save_selections(st.session_state.selected_tickers, username)

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
# 4. MODÜL: DEĞERLEME & UCUZLUK SKORU (MİKRO İŞ MODELİ GRUPLAMALI)
# ==============================================================================
elif module == "💎 Değerleme & Ucuzluk Skoru":
    st.header("💎 Temel Analiz: Mikro İş Modeline Göre Değerleme")
    st.caption("Şirketler genel sektör yerine kendi özel iş modellerine (örn: GPU vs RAM vs Telekom) göre gruplanır ve iskontoları kıyaslanır.")

    if 'selected_tickers' not in st.session_state:
        st.session_state.selected_tickers = load_selections(username)

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
            raw_results = []
            progress_bar = st.progress(0)
            status_text = st.empty()

            for i, ticker in enumerate(scan_list):
                status_text.text(f"Finansal veriler çekiliyor ({i+1}/{len(scan_list)}): {ticker}")
                res = fetch_single_ticker_raw(ticker)
                if res:
                    raw_results.append(res)
                progress_bar.progress((i + 1) / len(scan_list))

            status_text.empty()
            progress_bar.empty()

            # İş modeli alt sektör ortalamalarına ve 100 puanlık matrise göre skorla
            st.session_state.val_results = calculate_sector_relative_scores(raw_results)

    if 'val_results' in st.session_state and st.session_state.val_results:
        df_val = pd.DataFrame(st.session_state.val_results)
        df_val = df_val.sort_values(by=["Alt Sektör (İş Modeli)", "Nihai Skor"], ascending=[True, False])

        all_sub_sectors = ["Tüm Alt Sektörler / İş Modelleri"] + list(df_val["Alt Sektör (İş Modeli)"].unique())
        selected_sub_sector = st.selectbox("🎯 İş Modeli / Alt Sektör Filtresi:", all_sub_sectors)

        if selected_sub_sector != "Tüm Alt Sektörler / İş Modelleri":
            df_val = df_val[df_val["Alt Sektör (İş Modeli)"] == selected_sub_sector]

        st.subheader(f"📊 Değerleme Sonuçları ({len(df_val)} Hisse)")

        # Kolon İpuçları (Hint / Tooltip Yapılandırması)
        column_config = {
            "Hisse": st.column_config.TextColumn("Hisse", help="Hisse Sembolü"),
            "Alt Sektör (İş Modeli)": st.column_config.TextColumn("İş Modeli Grubu", help="💡 sub_sectors.json dosyasından gelen mikro grup (örn: RAM vs GPU)"),
            "Ana Sektör": st.column_config.TextColumn("Ana Sektör", help="yfinance Makro Sektörü"),
            "Nihai Skor": st.column_config.NumberColumn("Nihai Skor (0-100)", help="💡 70+ Yeşil: Yüksek Kalite & Ucuz Hisse\n💡 40 Altı Kırmızı: Zayıf/Pahalı"),
            "Alt Sektör İskontosu %": st.column_config.NumberColumn("İş Modeli İskontosu % [15p]", help="💡 Özel İş Modeli F/K medyanına göre ucuzluk/pahalılık oranı. Eksi değer, hissenin akranlarına göre PRİMLİ (daha pahalı) işlem gördüğü anlamına gelir."),
            "Alt Sektör Ort. F/K": st.column_config.NumberColumn("Alt Sektör Ort. F/K", help="💡 Sadece o mikro gruptaki şirketlerin medyan F/K değeri."),
            "PEG": st.column_config.NumberColumn("PEG [10p]", help="💡 Optimum: < 1.0 (F/K ÷ EPS Büyümesi)."),
            "EPS Büyümesi %": st.column_config.NumberColumn("EPS Büyümesi % [10p]", help="💡 Optimum: > %10."),
            "Gelir Büyümesi %": st.column_config.NumberColumn("Gelir Büyümesi % [10p]", help="💡 Optimum: > %10."),
            "Öz Sermaye Getirisi (ROE) %": st.column_config.NumberColumn("Öz Sermaye Getirisi % [10p]", help="💡 Optimum: > %10."),
            "Net Kar Marjı %": st.column_config.NumberColumn("Net Kar Marjı % [8p]", help="💡 Optimum: > %15."),
            "Brüt Kar Marjı %": st.column_config.NumberColumn("Brüt Kar Marjı % [7p]", help="💡 Optimum: %30 - %60."),
            "Faiz Karşılama Oranı": st.column_config.NumberColumn("Faiz Karşılama [7p]", help="💡 Optimum: > 3.0."),
            "Varlık Getirisi (ROA) %": st.column_config.NumberColumn("Varlık Getirisi (ROA) % [6p]", help="💡 Optimum: %5 - %10."),
            "Borç / Özsermaye": st.column_config.NumberColumn("Borç / Özsermaye [5p]", help="💡 Optimum: 0 - 0.5. Eksi değer, şirketin özsermayesinin negatife düştüğü anlamına gelir; bu bir risk sinyalidir ve puan almaz."),
            "Borç / Varlık %": st.column_config.NumberColumn("Borç / Varlık % [4p]", help="💡 Optimum: < %50."),
            "Cari Oran": st.column_config.NumberColumn("Cari Oran [3p]", help="💡 Optimum: 1.0 - 2.0."),
            "Likidite Oranı": st.column_config.NumberColumn("Likidite (Asit-Test) [3p]", help="💡 Optimum: > 1.0."),
            "Varlık Devir Hızı": st.column_config.NumberColumn("Varlık Devir Hızı [2p]", help="💡 Optimum: 1.0 - 2.0.")
        }

        styled_df = style_valuation_df(df_val)
        st.dataframe(styled_df, column_config=column_config, use_container_width=True, hide_index=True)

        st.divider()
        with st.expander("ℹ️ Nihai Skor nasıl hesaplanıyor? Parametrelerin anlamı", expanded=True):
            st.markdown("""
**Nihai Skor**, aşağıdaki 14 kritere göre 0'dan başlayıp puan **eklenerek** hesaplanır (hiçbir kriterde puan düşülmez).
Maksimum toplam **100 puan**dır. Bir kritere ait veri yfinance'ten gelmiyorsa (None/boş), o kriterden puan alınmaz —
yani düşük skor her zaman "kötü şirket" anlamına gelmez, bazen sadece "eksik veri" anlamına gelir.

| # | Kriter | Ağırlık | Ne anlama gelir? | Puanlama |
|---|---|---|---|---|
| 1 | **İş Modeli İskontosu %** | 15p | Hissenin F/K'sı, aynı mikro iş modelindeki (alt sektör) şirketlerin medyan F/K'sına göre ne kadar ucuz/pahalı. **Eksi değer = akranlarına göre daha pahalı (prim)**, bir hata değildir. | ≥30: 15p · 15-30: 10p · 0-15: 5p · <0 (prim): 0p |
| 2 | **PEG** | 10p | F/K ÷ EPS büyüme oranı. 1'in altı, büyümesine göre ucuz demektir. | ≤1.0: 10p · 1.0-1.5: 5p |
| 3 | **EPS Büyümesi %** | 10p | Yıllık kâr büyümesi. Negatifse şirketin kârı küçülüyor demektir. | ≥10: 10p · 5-10: 5p |
| 4 | **Gelir Büyümesi %** | 10p | Yıllık ciro büyümesi. Negatifse ciro küçülüyor demektir. | ≥10: 10p · 5-10: 5p |
| 5 | **Öz Sermaye Getirisi (ROE) %** | 10p | Özsermayenin ne kadar verimli kullanıldığı. | ≥10: 10p · 5-10: 5p |
| 6 | **Net Kâr Marjı %** | 8p | Cironun ne kadarının net kâra dönüştüğü. | ≥15: 8p · 8-15: 4p |
| 7 | **Brüt Kâr Marjı %** | 7p | Maliyet sonrası kalan marj. Çok yüksek de (>60) ideal kabul edilmez, orta bant tercih edilir. | 30-60: 7p · >60: 5p |
| 8 | **Faiz Karşılama Oranı** | 7p | FAVÖK'ün faiz giderini kaç kat karşıladığı - borç ödeme gücü. | ≥3: 7p · 1.5-3: 3p |
| 9 | **Varlık Getirisi (ROA) %** | 6p | Toplam varlıkların ne kadar verimli kullanıldığı. | ≥5: 6p · 2-5: 3p |
| 10 | **Borç / Özsermaye** | 5p | Borcun özsermayeye oranı - kaldıraç seviyesi. **Eksi değer, özsermayenin negatife düştüğü anlamına gelir (ciddi risk sinyali) ve puan almaz.** | 0-0.5: 5p · 0.5-1.0: 3p |
| 11 | **Borç / Varlık %** | 4p | Varlıkların ne kadarının borçla finanse edildiği. | ≤50: 4p · 50-70: 2p |
| 12 | **Cari Oran** | 3p | Kısa vadeli varlık / kısa vadeli borç. 1'in altı likidite sıkıntısına işaret eder. | 1.0-2.0: 3p · >2.0: 2p |
| 13 | **Likidite Oranı (Asit-Test)** | 3p | Stoklar hariç kısa vadeli ödeme gücü. | ≥1.0: 3p |
| 14 | **Varlık Devir Hızı** | 2p | Varlıkların ciro üretme hızı. | 1.0-2.0: 2p · >2.0: 1p |

**Neden bazı yüzdeler eksi görünüyor?** İskonto, büyüme (EPS/Gelir) ve kârlılık (ROE/ROA/marj) gibi kalemler gerçek
piyasa/finansal verilerdir; şirket küçülüyorsa veya akranlarına göre pahalıysa bu değerler doğal olarak eksi çıkar -
bu bir hesaplama hatası değil, gerçek durumun yansımasıdır ve yukarıdaki tabloda bu kriterler zaten puan almaz.
Tek istisna **Borç/Özsermaye**'ydi: negatif özsermayeyi yanlışlıkla "düşük borç" sayıp tam puan veriyordu, bu düzeltildi.
""")

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
                    save_ticker_lists(st.session_state.ticker_lists, username)
                    st.success(f"✅ **{new_symbol}**, {selected_m} listesine eklendi ve kaydedildi!")
                    st.rerun()

    with col_del:
        st.subheader("🗑️ Hisse Çıkar")
        symbol_to_remove = st.selectbox("Listeden çıkarmak istediğiniz hisse:", current_market_list)
        
        if st.button("Listeden Çıkar", type="secondary"):
            if symbol_to_remove in current_market_list:
                st.session_state.ticker_lists[selected_m].remove(symbol_to_remove)
                save_ticker_lists(st.session_state.ticker_lists, username)
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
        save_ticker_lists(st.session_state.ticker_lists, username)
        st.success("Tüm listeler varsayılan ayarlara sıfırlandı!")
        st.rerun()

# ==============================================================================
# MODÜL: DTW ZAMAN SERİSİ & BENZERLİK ANALİZİ (GÖRÜNTÜLEME & TİP GÜVENCELİ)
# ==============================================================================
elif module == "🔄 DTW Zaman Serisi & Benzerlik Analizi":
    st.header("🔄 DTW (Dynamic Time Warping) Zaman Serisi & Benzerlik Analizi")
    st.caption("NASDAQ 100 hisselerinin son 2 gününün 5 dakikalık seans içi fiyat hareketlerini kıyaslar.")

    col_btn, col_thresh, col_window = st.columns([2, 1.5, 1.5])
    
    with col_btn:
        st.write("<br>", unsafe_allow_html=True)
        run_dtw_fetch = st.button("🚀 Verileri Güncelle & Analizi Çalıştır", type="primary")
        
    with col_thresh:
        min_similarity = st.slider("🎯 Min. Benzerlik Skoru (%):", min_value=50, max_value=95, value=75, step=5)

    with col_window:
        max_warp_minutes = st.slider("⏱️ Max Zamansal Kayma (Dakika):", min_value=15, max_value=120, value=45, step=15)
        max_warping_window = max_warp_minutes // 5  # 5 dakikalık adımlara çevir

    time_penalty = 0.10

    # 1. Ham verileri JSON önbelleğinden yükle
    if 'dtw_data' not in st.session_state and os.path.exists("nasdaq_5m_cache.json"):
        try:
            with open("nasdaq_5m_cache.json", 'r', encoding='utf-8') as f:
                st.session_state.dtw_data = json.load(f).get("stocks", {})
        except Exception:
            st.session_state.dtw_data = {}

    # 2. Butona basıldıysa ham verileri yeniden çek ve hesapla
    if run_dtw_fetch:
        with st.spinner("NASDAQ 100 verileri Yahoo Finance'den çekiliyor ve Türkiye saatine çevriliyor..."):
            dtw_data = fetch_and_cache_5m_data(target_list)
            st.session_state.dtw_data = dtw_data
            
            if dtw_data:
                with st.spinner("DTW benzerlik matrisi hesaplanıyor..."):
                    # Kendi İçinde Benzerlik Hesapla
                    self_sim_results = []
                    stock_keys = list(dtw_data.keys())
                    for ticker in stock_keys:
                        d1 = dtw_data[ticker]["day1"]["prices"]
                        d2 = dtw_data[ticker]["day2"]["prices"]
                        sim, dist = compute_dtw_similarity(d1, d2, max_warping_window, time_penalty)
                        self_sim_results.append({
                            "Hisse": ticker,
                            "1. Gün Tarihi": dtw_data[ticker]["day1"]["date"],
                            "2. Gün Tarihi": dtw_data[ticker]["day2"]["date"],
                            "DTW Benzerlik Skoru %": sim,
                            "DTW Mesafesi": dist
                        })
                    st.session_state.self_sim_results = self_sim_results

                    # Çapraz Benzerlik Hesapla
                    cross_sim_results = compute_cross_similarity_parallel(
                        dtw_data, min_similarity=0, max_warping_window=max_warping_window, time_penalty=time_penalty
                    )
                    st.session_state.cross_sim_results = cross_sim_results

                    # Sonuçları diske kaydet
                    save_cached_dtw_results(max_warping_window, time_penalty, self_sim_results, cross_sim_results)
                    st.success(f"✅ {len(dtw_data)} hissenin analizi tamamlandı!")
            else:
                st.error("⚠️ Veri çekilemedi.")

    # 3. Diskteki cache sonuçlarını her durumda (parametreler uyuşuyorsa) oturuma otomatik yükle
    cached_self, cached_cross = load_cached_dtw_results(max_warping_window, time_penalty)
    if cached_self is not None and cached_cross is not None:
        st.session_state.self_sim_results = cached_self
        st.session_state.cross_sim_results = cached_cross
    elif 'self_sim_results' not in st.session_state or not st.session_state.self_sim_results:
        if 'dtw_data' in st.session_state and st.session_state.dtw_data:
            dtw_data = st.session_state.dtw_data
            self_sim_results = []
            stock_keys = list(dtw_data.keys())
            for ticker in stock_keys:
                d1 = dtw_data[ticker]["day1"]["prices"]
                d2 = dtw_data[ticker]["day2"]["prices"]
                sim, dist = compute_dtw_similarity(d1, d2, max_warping_window, time_penalty)
                self_sim_results.append({
                    "Hisse": ticker,
                    "1. Gün Tarihi": dtw_data[ticker]["day1"]["date"],
                    "2. Gün Tarihi": dtw_data[ticker]["day2"]["date"],
                    "DTW Benzerlik Skoru %": sim,
                    "DTW Mesafesi": dist
                })
            st.session_state.self_sim_results = self_sim_results
            st.session_state.cross_sim_results = compute_cross_similarity_parallel(
                dtw_data, min_similarity=0, max_warping_window=max_warping_window, time_penalty=time_penalty
            )
            save_cached_dtw_results(max_warping_window, time_penalty, self_sim_results, st.session_state.cross_sim_results)

    # 4. GÖRSELLEŞTİRME KISMI (SEKMELER VE GÜVENLİ FİLTRELEME)
    if 'dtw_data' in st.session_state and st.session_state.dtw_data:
        stocks_dict = st.session_state.dtw_data
        stock_keys = list(stocks_dict.keys())

        tab1, tab2, tab3 = st.tabs(["📌 1. Kendi İçinde Benzerlik", "🌐 2. Hisseler Arası Benzerlik", "📈 3. İnteraktif Karşılaştırmalı Grafik"])

        # TAB 1: KENDİ İÇİNDE BENZERLİK (Tip Güvenceli Filtreleme)
        with tab1:
            st.subheader("🔁 Hisselerin 1. Gün ve 2. Gün Fiyat Hareketi Benzerliği")
            if 'self_sim_results' in st.session_state and st.session_state.self_sim_results:
                df_self = pd.DataFrame(st.session_state.self_sim_results)
                
                # JSON'dan gelen sayısal skorları kesin olarak float tipine dönüştür
                df_self["DTW Benzerlik Skoru %"] = pd.to_numeric(df_self["DTW Benzerlik Skoru %"], errors='coerce')
                
                df_filtered_self = df_self[df_self["DTW Benzerlik Skoru %"] >= float(min_similarity)].sort_values(by="DTW Benzerlik Skoru %", ascending=False)
                
                st.caption(f"Toplam {len(df_self)} hisse içerisinden, %{min_similarity} ve üzeri benzerliğe sahip {len(df_filtered_self)} hisse listeleniyor.")
                
                if not df_filtered_self.empty:
                    st.dataframe(df_filtered_self, use_container_width=True, hide_index=True)
                else:
                    max_score = df_self["DTW Benzerlik Skoru %"].max() if not df_self.empty else 0
                    st.warning(f"⚠️ Seçtiğiniz **%{min_similarity}** eşik değerinin üzerinde öz-benzerlik gösteren hisse bulunamadı. (Bu veri setindeki en yüksek öz-benzerlik: **%{max_score}**).")
            else:
                st.warning("Veri bulunamadı. Lütfen yukarıdaki butona tıklayın.")

        # TAB 2: HİSSELER ARASI ÇAPRAZ BENZERLİK (Tip Güvenceli Filtreleme)
        with tab2:
            st.subheader("🔀 Farklı Hisselerin Son Gün Fiyat Hareketi Benzerliği")
            if 'cross_sim_results' in st.session_state and st.session_state.cross_sim_results:
                df_cross = pd.DataFrame(st.session_state.cross_sim_results)
                
                df_cross["DTW Benzerlik Skoru %"] = pd.to_numeric(df_cross["DTW Benzerlik Skoru %"], errors='coerce')
                
                df_filtered_cross = df_cross[df_cross["DTW Benzerlik Skoru %"] >= float(min_similarity)].sort_values(by="DTW Benzerlik Skoru %", ascending=False)
                
                st.caption(f"Toplam {len(df_cross)} çift içerisinden, %{min_similarity} ve üzeri benzerliğe sahip {len(df_filtered_cross)} çift listeleniyor.")
                
                if not df_filtered_cross.empty:
                    st.dataframe(df_filtered_cross, use_container_width=True, hide_index=True)
                else:
                    st.warning(f"Seçilen %{min_similarity} eşik değerinin üzerinde eşleşen hisse çifti bulunamadı.")
            else:
                st.warning("Veri bulunamadı. Lütfen yukarıdaki butona tıklayın.")

# TAB 3: İNTERAKTİF KARŞILAŞTIRMALI GRAFİK (Sadece Görselde Türkiye Saati Dönüşümü)
        with tab3:
            st.subheader("📈 Karşılaştırmalı Zaman Serisi Grafiği (Türkiye Saati)")
            comp_mode = st.radio("Karşılaştırma Tipi:", ["Aynı Hissenin 2 Günü (Gün 1 vs Gün 2)", "İki Farklı Hisse (Son Gün)"], horizontal=True)
            
            # New York zamanındaki saat listelerini Türkiye saatine çeviren yardımcı fonksiyon
 # New York zamanındaki saat listelerini Türkiye saatine çeviren yardımcı fonksiyon
            def convert_ny_to_tr(times_list):
                if not times_list:
                    return [], []
                # pd.to_datetime ile zaman serisine çeviriyoruz
                dt_series = pd.to_datetime(times_list)
                
                # Eğer zaman dilimi (tz) yoksa localize et, varsa New York'a çevir
                if dt_series.tz is None:
                    dt_series = dt_series.tz_localize('America/New_York', ambiguous='NaT', nonexistent='shift_forward')
                else:
                    dt_series = dt_series.tz_convert('America/New_York')
                
                # Türkiye saat dilimine (Europe/Istanbul) dönüştür
                dt_tr = dt_series.tz_convert('Europe/Istanbul')
                
                return dt_tr.strftime('%H:%M').tolist(), dt_tr.strftime('%Y-%m-%d %H:%M').tolist()
            if comp_mode == "Aynı Hissenin 2 Günü (Gün 1 vs Gün 2)":
                selected_t = st.selectbox("Hisseyi Seçin:", stock_keys)
                t_data = stocks_dict[selected_t]
                
                # Saatleri TRT'ye çevir
                times1_short, times1_full = convert_ny_to_tr(t_data["day1"]["times"])
                times2_short, times2_full = convert_ny_to_tr(t_data["day2"]["times"])
                prices1, prices2 = t_data["day1"]["prices"], t_data["day2"]["prices"]
                
                sim, _ = compute_dtw_similarity(prices1, prices2, max_warping_window, time_penalty)
                st.info(f"💡 **{selected_t}** için Gün 1 ve Gün 2 DTW Benzerlik Skoru: **%{sim}**")

                fig = go.Figure()
                fig.add_trace(go.Scatter(x=times1_short, y=prices1, mode='lines', name=f"{t_data['day1']['date']} (Gün 1)", line=dict(color='#00d2ff', width=2)))
                fig.add_trace(go.Scatter(x=times2_short, y=prices2, mode='lines', name=f"{t_data['day2']['date']} (Gün 2)", line=dict(color='#ff9f1c', width=2), yaxis="y2"))

                peaks1, troughs1 = find_local_extremes(times1_short, prices1)
                for _, tm, pr in peaks1:
                    fig.add_annotation(x=tm, y=pr, text=f"Tepe: {pr}<br>({tm})", showarrow=True, arrowhead=2, arrowcolor="#00d2ff", bgcolor="#1b4332")
                for _, tm, pr in troughs1:
                    fig.add_annotation(x=tm, y=pr, text=f"Dip: {pr}<br>({tm})", showarrow=True, arrowhead=2, arrowcolor="#00d2ff", bgcolor="#7209b7")

                fig.update_layout(
                    title=f"{selected_t} - 5 Dakikalık Fiyat Karşılaştırması (Türkiye Saati - TRT)",
                    template="plotly_dark", height=600,
                    xaxis=dict(title="Zaman (Türkiye Yerel Saati)"),
                    yaxis=dict(title=dict(text=f"Fiyat {t_data['day1']['date']} ($)", font=dict(color="#00d2ff"))),
                    yaxis2=dict(title=dict(text=f"Fiyat {t_data['day2']['date']} ($)", font=dict(color="#ff9f1c")), overlaying="y", side="right")
                )
                st.plotly_chart(fig, use_container_width=True)

            else:
                col_h1, col_h2 = st.columns(2)
                with col_h1:
                    t1_sel = st.selectbox("1. Hisse:", stock_keys, index=0)
                with col_h2:
                    t2_sel = st.selectbox("2. Hisse:", stock_keys, index=min(1, len(stock_keys)-1))

                data1 = stocks_dict[t1_sel]["day2"]
                data2 = stocks_dict[t2_sel]["day2"]

                t1_short, _ = convert_ny_to_tr(data1["times"])
                t2_short, _ = convert_ny_to_tr(data2["times"])

                sim, _ = compute_dtw_similarity(data1["prices"], data2["prices"], max_warping_window, time_penalty)
                st.info(f"💡 **{t1_sel}** ile **{t2_sel}** Arasındaki DTW Benzerlik Skoru: **%{sim}**")

                fig = go.Figure()
                fig.add_trace(go.Scatter(x=t1_short, y=data1["prices"], mode='lines', name=f"{t1_sel} ({data1['date']})", line=dict(color='#2ec4b6', width=2)))
                fig.add_trace(go.Scatter(x=t2_short, y=data2["prices"], mode='lines', name=f"{t2_sel} ({data2['date']})", line=dict(color='#e63946', width=2), yaxis="y2"))

                peaks1, troughs1 = find_local_extremes(t1_short, data1["prices"])
                for _, tm, pr in peaks1:
                    fig.add_annotation(x=tm, y=pr, text=f"{t1_sel} Tepe: {pr}<br>({tm})", showarrow=True, arrowhead=2, arrowcolor="#2ec4b6")
                for _, tm, pr in troughs1:
                    fig.add_annotation(x=tm, y=pr, text=f"{t1_sel} Dip: {pr}<br>({tm})", showarrow=True, arrowhead=2, arrowcolor="#2ec4b6")

                fig.update_layout(
                    title=f"{t1_sel} vs {t2_sel} - Son Gün 5m Fiyat Hareketi Kıyaslaması (TRT)",
                    template="plotly_dark", height=600,
                    xaxis=dict(title="Zaman (Türkiye Yerel Saati)"),
                    yaxis=dict(title=dict(text=f"{t1_sel} Fiyat ($)", font=dict(color="#2ec4b6"))),
                    yaxis2=dict(title=dict(text=f"{t2_sel} Fiyat ($)", font=dict(color="#e63946")), overlaying="y", side="right")
                )
                st.plotly_chart(fig, use_container_width=True)

# ==============================================================================
# 8. MODÜL: ALPACA CANLI POZİSYONLAR
# ==============================================================================
elif module == "🦙 Alpaca Canlı Pozisyonlar":
    st.header("🦙 Alpaca Canlı Pozisyonlar")
    st.caption("Açık pozisyonlar, güncel stop seviyeleri ve stoptan uzaklık. Stoplar structure-based trailing-stop GitHub Action tarafından yarım saatte bir güncellenir.")
    render_alpaca_dashboard(username)

# ==============================================================================
# 9. MODÜL: PREMIUM BUY POINT PORTFÖYÜ
# ==============================================================================
elif module == "🎯 Premium Buy Point Portföyü":
    st.header("🎯 Premium Buy Point Portföyü")
    st.caption("Seçtiğiniz hisseler için demand zone (premium buy point) taranır; fiyat zone'a girdiğinde otomatik alım yapılır.")
    render_premium_buy_portfolio(target_list, username)