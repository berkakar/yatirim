import streamlit as st
import streamlit_authenticator as stauth
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import json
import os
from datetime import datetime, timezone

from config import load_ticker_lists, save_ticker_lists, search_tickers, GITHUB_REPO, DEFAULT_NASDAQ_100, DEFAULT_NYSE, DEFAULT_BIST_100, load_stock_groups, save_stock_groups, load_group_markets, save_group_markets, MARKETS
from github_config import read_json_from_github, write_json_to_github
from ui_style import zebra_style
from scanner import (
    get_scanner_data, bars_from_df, fetch_daily_pairs, SCAN_TIMEFRAMES, SCAN_TIMEFRAME_LABELS,
    INTRADAY_DEFAULT_DAYS, INTRADAY_MAX_DAYS, DAILY_DEFAULT_DAYS, DAILY_MAX_DAYS,
)
from buy_algorithms import ALGORITHMS, reject_if_marketable
from backtest_engine import run_backtest
from backtest_data import append_results, new_run_id
from stoploss import get_stoploss_data
from valuation import fetch_tickers_with_shared_cache, calculate_sector_relative_scores, style_valuation_df
from dtw_analysis import (
    fetch_and_cache_5m_data,
    compute_dtw_similarity,
    compute_two_day_trend,
    find_local_extremes,
    load_cached_dtw_results,
    save_cached_dtw_results
)
from alpaca_client import AlpacaClient
from alpaca_dashboard import render_alpaca_dashboard, render_account_summary, render_positions_summary_table
from premium_buy_portfolio import render_premium_buy_portfolio
from otomatik_alim_satim import render_otomatik_alim_satim
from tefas_fonlari import render_turk_fonlari
from turk_fonlari_takip import render_turk_fonlari_takip
from hisse_patern import render_hisse_patern
from bicak_kanali_test import render_bicak_kanali_test
from backtest import render_backtest
from version_info import get_version_label

NAV_HOME = "🏠 Giriş Sayfası"
MODULE_GROUPS = {
    "🔍 Alım Bölgesi Tarama": ["Alım Bölgesi Tarama"],
    "📊 Analiz": [
        "Stop Loss Hesaplayıcı",
        "💎 Değerleme & Ucuzluk Skoru",
        "🔄 DTW Zaman Serisi & Benzerlik Analizi",
        "📐 Hisse Patern Analizi",
        "📊 Bağımsız Hisse Grafiği",
        "🔪 Bıçak Kanalı Testi",
    ],
    "🇹🇷 Türk Fonları": ["Türk Fonları", "Fonlarım"],
    "🤖 Algoritmik Ticaret": ["🦙 Alpaca Canlı Pozisyonlar", "🎯 Premium Buy Point Portföyü", "BackTest", "🤖 Otomatik Alım/Satım"],
    "⚙️ Hisse Liste Düzenleme": ["⚙️ Hisse Listelerini Yönet", "🗂️ Hisse Gruplarını Yönet"],
}
# Modül düğmelerinde gösterilecek ikonlu etiketler (yönlendirme için kullanılan
# değerler MODULE_GROUPS'takiyle aynı kalır, sadece görünen metin değişir)
MODULE_DISPLAY = {
    "Alım Bölgesi Tarama": "🔍 Alım Bölgesi Tarama",
    "Stop Loss Hesaplayıcı": "🛡️ Stop Loss Hesaplayıcı",
    "Türk Fonları": "🇹🇷 Türk Fonları",
    "Fonlarım": "💼 Fonlarım",
    "BackTest": "🧪 BackTest",
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
_LOGO_PATH = "assets/logo.jpg"
_LOGIN_BOX_WIDTH = 380
if st.session_state.get("authentication_status") is not True and os.path.exists(_LOGO_PATH):
    # Giriş formu (st.form) varsayılan olarak kolonun tüm genişliğine yayılır -
    # logoyla aynı boyutta görünmesi için ikisini de aynı sabit genişliğe sabitliyoruz.
    st.markdown(
        f"""
        <style>
        div[data-testid="stForm"] {{
            max-width: {_LOGIN_BOX_WIDTH}px;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    _login_col, _logo_col = st.columns([1, 1], gap="large")
    with _login_col:
        authenticator.login(location="main")
    with _logo_col:
        st.image(_LOGO_PATH, width=_LOGIN_BOX_WIDTH)
else:
    authenticator.login(location="main")

_auth_status = st.session_state.get("authentication_status")
if _auth_status is False:
    st.error("❌ Kullanıcı adı veya şifre hatalı.")
    st.stop()
elif _auth_status is None:
    st.warning("🔒 Devam etmek için giriş yapın.")
    st.stop()

username = st.session_state["username"]

st.title("📈 Yatırım Terminali")
authenticator.logout("🚪 Çıkış Yap", "sidebar")

# ------------------------------------------------------------------------------
# DİNAMİK LİSTE YÜKLEME VE SESSION STATE
# ------------------------------------------------------------------------------
if 'ticker_lists' not in st.session_state:
    st.session_state.ticker_lists = load_ticker_lists(username)

if 'stock_groups' not in st.session_state:
    st.session_state.stock_groups = load_stock_groups(username)

if 'group_markets' not in st.session_state:
    st.session_state.group_markets = load_group_markets(username)

# Bir grup silindiğinde, o gruba ait widget zaten bu run'da oluşturulmuş olabileceği
# için session_state'i doğrudan değiştiremeyiz (StreamlitWidgetAlreadyInstantiatedError).
# Bu yüzden silme isteğini burada, multiselect widget'ı oluşturulmadan önce uygularız.
removed_group = st.session_state.pop("_pending_group_removal", None)
if removed_group and "selected_stock_groups" in st.session_state:
    st.session_state.selected_stock_groups = [
        g for g in st.session_state.selected_stock_groups if g != removed_group
    ]

# ------------------------------------------------------------------------------
# YAN MENÜ (SIDEBAR) AYARLARI
# ------------------------------------------------------------------------------
market = st.sidebar.selectbox("Piyasa Seçimi", MARKETS)

# Piyasa değiştiğinde, artık seçili piyasaya ait olmayan grup seçimlerini
# multiselect widget'ı oluşturulmadan önce temizlememiz gerekir (aksi halde
# Streamlit, options listesinde olmayan bir seçili değer için hata verir).
if st.session_state.get("_prev_sidebar_market") != market and "selected_stock_groups" in st.session_state:
    st.session_state.selected_stock_groups = [
        g for g in st.session_state.selected_stock_groups
        if st.session_state.group_markets.get(g) == market
    ]
st.session_state["_prev_sidebar_market"] = market

groups_for_market = [
    g for g in st.session_state.stock_groups.keys()
    if st.session_state.group_markets.get(g) == market
]

selected_groups = []
if groups_for_market:
    selected_groups = st.sidebar.multiselect(
        f"🗂️ Hisse Grubu — {market} (seçilirse piyasa yerine kullanılır)",
        groups_for_market,
        key="selected_stock_groups",
    )
elif st.session_state.stock_groups:
    st.sidebar.caption(f"🗂️ **{market}** piyasasıyla ilişkilendirilmiş bir hisse grubunuz yok.")
else:
    st.sidebar.caption("🗂️ Henüz hisse grubunuz yok — Hisse Liste Düzenleme'den oluşturabilirsiniz.")

_unassigned_groups = [g for g in st.session_state.stock_groups.keys() if g not in st.session_state.group_markets]
if _unassigned_groups:
    st.sidebar.caption(
        f"⚠️ {len(_unassigned_groups)} grubun piyasası atanmamış — "
        "'🗂️ Hisse Gruplarını Yönet' bölümünden atayabilirsiniz."
    )

st.sidebar.divider()
st.markdown("""
<style>
/* Kategori (üst seviye) başlıkları: kalın yazı, alt öğelerden ayrışsın */
[class*="st-key-navhead_wrap_"] button,
[class*="st-key-navhead_wrap_"] button p {
    font-weight: 700 !important;
}
/* Modül (alt seviye) öğeleri: girintili ve ince yazı, hiyerarşi belli olsun */
[class*="st-key-navitem_wrap_"] {
    padding-left: 1.15rem;
}
[class*="st-key-navitem_wrap_"] button,
[class*="st-key-navitem_wrap_"] button p {
    font-weight: 400 !important;
    font-size: 0.87rem !important;
}
</style>
""", unsafe_allow_html=True)

if "nav_category" not in st.session_state:
    st.session_state["nav_category"] = NAV_HOME
if "open_category" not in st.session_state:
    st.session_state["open_category"] = None

def _select_home():
    st.session_state["nav_category"] = NAV_HOME

def _toggle_category(cat_name):
    st.session_state["open_category"] = None if st.session_state["open_category"] == cat_name else cat_name

def _select_module(state_key, cat_name, mod_name):
    st.session_state[state_key] = mod_name
    st.session_state["nav_category"] = cat_name
    st.session_state["open_category"] = cat_name

category = st.session_state["nav_category"]

with st.sidebar.container(key="navhead_wrap_home"):
    st.button(
        NAV_HOME, key="navbtn_home", use_container_width=True,
        type="primary" if category == NAV_HOME else "secondary",
        on_click=_select_home,
    )

for ci, (cat_name, modules_in_category) in enumerate(MODULE_GROUPS.items()):
    module_state_key = f"active_module_{cat_name}"
    if module_state_key not in st.session_state:
        st.session_state[module_state_key] = modules_in_category[0]

    open_category = st.session_state["open_category"]
    is_open = (open_category == cat_name) if open_category is not None else (category == cat_name)

    with st.sidebar.container(key=f"navhead_wrap_{ci}"):
        st.button(
            f"{'▾' if is_open else '▸'} {cat_name}",
            key=f"navhead_{cat_name}",
            use_container_width=True,
            type="primary" if category == cat_name else "secondary",
            on_click=_toggle_category,
            args=(cat_name,),
        )
    if is_open:
        for mi, mod_name in enumerate(modules_in_category):
            with st.sidebar.container(key=f"navitem_wrap_{ci}_{mi}"):
                st.button(
                    "‣ " + MODULE_DISPLAY.get(mod_name, mod_name),
                    key=f"navbtn_{cat_name}_{mod_name}",
                    use_container_width=True,
                    type="primary" if (category == cat_name and st.session_state[module_state_key] == mod_name) else "secondary",
                    on_click=_select_module,
                    args=(module_state_key, cat_name, mod_name),
                )

if category == NAV_HOME:
    module = NAV_HOME
else:
    module_state_key = f"active_module_{category}"
    module = st.session_state[module_state_key]

if selected_groups:
    group_tickers = list(dict.fromkeys(
        t for g in selected_groups for t in st.session_state.stock_groups.get(g, [])
    ))
    market_tickers = st.session_state.ticker_lists[market]

    SCOPE_MARKET_ONLY = f"🌐 Sadece {market} (Piyasanın Tamamı)"
    SCOPE_MARKET_PLUS_GROUPS = f"🌐+🗂️ {market} + Seçili Gruplar"
    SCOPE_GROUPS_ONLY = "🗂️ Sadece Seçili Gruplar"
    group_scope = st.sidebar.radio(
        "📌 Analiz Kapsamı:",
        [SCOPE_MARKET_ONLY, SCOPE_MARKET_PLUS_GROUPS, SCOPE_GROUPS_ONLY],
        index=2,
        key="stock_group_scope",
    )

    if group_scope == SCOPE_MARKET_ONLY:
        target_list = market_tickers
        scope_label = market
    elif group_scope == SCOPE_MARKET_PLUS_GROUPS:
        target_list = list(dict.fromkeys(market_tickers + group_tickers))
        scope_label = f"{market} + " + " + ".join(selected_groups)
        market = scope_label
    else:
        target_list = group_tickers
        scope_label = " + ".join(selected_groups)
        market = scope_label

    with st.expander(f"📌 Aktif Analiz Kapsamı: **{scope_label}** — Toplam {len(target_list)} hisse", expanded=False):
        if group_scope == SCOPE_MARKET_ONLY:
            st.caption("ℹ️ Seçili gruplar bu kapsamda kullanılmıyor; sadece piyasanın tam listesi analiz girdisi.")
        else:
            max_len = max((len(st.session_state.stock_groups.get(g, [])) for g in selected_groups), default=0)
            table_data = {
                g: st.session_state.stock_groups.get(g, []) + [""] * (max_len - len(st.session_state.stock_groups.get(g, [])))
                for g in selected_groups
            }
            st.dataframe(pd.DataFrame(table_data), use_container_width=True, hide_index=True)
else:
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
# 0. MODÜL: GİRİŞ SAYFASI (ANA SAYFA)
# ==============================================================================
if module == NAV_HOME:
    st.header("🏠 Genel Bakış")
    st.caption("Hesap özeti, liste durumu ve bu oturumdaki son tarama sonuçları.")

    key_id, secret_key = get_user_alpaca_creds(username)

    alpaca_positions = None
    if key_id and secret_key:
        try:
            alpaca_client = AlpacaClient(key_id, secret_key)
            alpaca_positions = alpaca_client.get_all_positions()
            render_account_summary(alpaca_client, username, alpaca_positions, show_initial_capital_setting=False)
        except Exception as e:
            st.warning(f"⚠️ Alpaca hesap özeti alınamadı: {e}")
    else:
        st.info(f"'{username}' için Alpaca hesabı tanımlı değil (`.streamlit/secrets.toml` içinde `[alpaca.{username}]`).")

    st.divider()
    st.subheader("📋 Hisse Listeleri")
    lc1, lc2, lc3, lc4 = st.columns(4)
    lc1.metric("NASDAQ 100 Listesi", len(st.session_state.ticker_lists["NASDAQ 100"]))
    lc2.metric("NYSE Listesi", len(st.session_state.ticker_lists["NYSE"]))
    lc3.metric("BIST 100 Listesi", len(st.session_state.ticker_lists["BIST 100"]))
    lc4.metric("Hisse Grupları", len(st.session_state.stock_groups))

    st.divider()
    st.subheader("🎯 Bu Oturumdaki Son Tarama Sonuçları")
    if 'scan_signals' in st.session_state:
        st.metric("Alım Bölgesi Sinyali", len(st.session_state.scan_signals))
    else:
        st.info("Alım Bölgesi Tarama bu oturumda henüz çalıştırılmadı.")

    st.divider()
    st.subheader("🚀 Hızlı Erişim")

    def _go_to_category(cat_name):
        st.session_state["nav_category"] = cat_name
        st.session_state["open_category"] = cat_name

    nav_cols = st.columns(len(MODULE_GROUPS))
    for col, cat_name in zip(nav_cols, MODULE_GROUPS.keys()):
        col.button(
            cat_name, use_container_width=True, key=f"quicknav_{cat_name}",
            on_click=_go_to_category, args=(cat_name,),
        )

    if key_id and secret_key:
        st.divider()
        st.subheader("🦙 Alpaca Canlı Pozisyonlar")
        if alpaca_positions is not None:
            render_positions_summary_table(alpaca_positions)

# ==============================================================================
# 1. MODÜL: ALIM BÖLGESİ TARAMA (Fincan-Kulp + OBO/TOBO birleşik)
# ==============================================================================
elif module == "Alım Bölgesi Tarama":
    st.header("🔍 Alım Bölgesi Tarama")
    st.caption("Taramak istediğiniz formasyon(lar)ı ve mum periyodu/periyotlarını seçip aşağıdaki butona basın.")

    scan_cb1, scan_cb2 = st.columns(2)
    use_cup = scan_cb1.checkbox("Fincan-Kulp", value=True, key="scan_use_cup")
    use_obo = scan_cb2.checkbox("OBO & TOBO", value=True, key="scan_use_obo")

    st.caption("Alım Sinyali Algoritmaları (opsiyonel - Premium Buy Point Portföyü'ndekiyle aynı algoritmalar)")
    algo_cols = st.columns(len(ALGORITHMS))
    selected_algo_ids = [
        algo_id for col, algo_id in zip(algo_cols, ALGORITHMS.keys())
        if col.checkbox(ALGORITHMS[algo_id][0], value=False, key=f"scan_algo_{algo_id}")
    ]

    st.caption("Mum Periyodu")
    tf_cols = st.columns(len(SCAN_TIMEFRAMES))
    selected_timeframes = [
        tf_code for col, tf_code in zip(tf_cols, SCAN_TIMEFRAMES)
        if col.checkbox(SCAN_TIMEFRAME_LABELS[tf_code], value=(tf_code == "1Day"), key=f"scan_tf_{tf_code}")
    ]

    days_col1, days_col2 = st.columns(2)
    intraday_days = days_col1.number_input(
        "15dk / 30dk / 1sa mumlar için geriye gidilecek gün sayısı",
        min_value=1, max_value=INTRADAY_MAX_DAYS, value=INTRADAY_DEFAULT_DAYS, step=1,
        key="scan_intraday_days",
        help=f"Yahoo Finance gün-içi mumlarda en fazla {INTRADAY_MAX_DAYS} gün geriye gidebiliyor.",
    )
    daily_days = days_col2.number_input(
        "1 gün mumlar için geriye gidilecek gün sayısı",
        min_value=1, max_value=DAILY_MAX_DAYS, value=DAILY_DEFAULT_DAYS, step=1,
        key="scan_daily_days",
        help=f"En fazla {DAILY_MAX_DAYS} gün (yaklaşık 2 yıl) geriye gidilebiliyor.",
    )

    if st.button(
        "🚀 Seçili Tarayıcılarla Tara", type="primary",
        disabled=not (use_cup or use_obo or selected_algo_ids) or not selected_timeframes,
    ):
        with st.spinner(f'{market} listesi taranıyor...'):
            signals = []
            for tf_code in selected_timeframes:
                tf_label = SCAN_TIMEFRAME_LABELS[tf_code]
                tf_days = daily_days if tf_code == "1Day" else intraday_days
                for t in target_list:
                    df_temp, cup, obo, tobo = get_scanner_data(t, timeframe=tf_code, period_days=tf_days)
                    if df_temp is None or df_temp.empty:
                        continue
                    if use_cup and isinstance(cup, dict) and all(k in cup for k in ['A', 'B', 'C', 'D']):
                        signals.append({
                            "Hisse": t, "Tarayıcı Türü": "Fincan-Kulp", "Mum Periyodu": tf_label,
                            "_tf_code": tf_code, "_kind": "pattern", "Mum Seviyesi": round(float(cup['D']['Close']), 2),
                        })
                    if use_obo:
                        if isinstance(obo, dict) and all(k in obo for k in ['left_shoulder', 'head', 'right_shoulder']):
                            signals.append({
                                "Hisse": t, "Tarayıcı Türü": "OBO", "Mum Periyodu": tf_label,
                                "_tf_code": tf_code, "_kind": "pattern", "Mum Seviyesi": round(float(obo['right_shoulder']['Close']), 2),
                            })
                        elif isinstance(tobo, dict) and all(k in tobo for k in ['left_shoulder', 'head', 'right_shoulder']):
                            signals.append({
                                "Hisse": t, "Tarayıcı Türü": "TOBO", "Mum Periyodu": tf_label,
                                "_tf_code": tf_code, "_kind": "pattern", "Mum Seviyesi": round(float(tobo['right_shoulder']['Close']), 2),
                            })
                    if selected_algo_ids:
                        bars = bars_from_df(df_temp)
                        current_price = float(df_temp['Close'].iloc[-1])
                        # "trend_pullback" günlük SMA(200) filtresi için günlük kapanışlara
                        # ihtiyaç duyuyor - bu tek algoritma seçilmediyse gereksiz bir ekstra
                        # (günlük) veri çekimi yapmaya gerek yok.
                        daily_closes = None
                        if tf_code == "1Day":
                            daily_closes = df_temp['Close'].tolist()
                        elif "trend_pullback" in selected_algo_ids:
                            daily_df, _, _, _ = get_scanner_data(t, timeframe="1Day", period_days=daily_days)
                            daily_closes = daily_df['Close'].tolist() if daily_df is not None else None
                        for algo_id in selected_algo_ids:
                            algo_label, algo_fn = ALGORITHMS[algo_id]
                            sig = reject_if_marketable(algo_fn(bars, daily_closes), current_price)
                            if sig is not None:
                                signals.append({
                                    "Hisse": t, "Tarayıcı Türü": algo_label, "Mum Periyodu": tf_label,
                                    "_tf_code": tf_code, "_kind": "algo", "_algo_id": algo_id,
                                    "Mum Seviyesi": round(float(sig.price), 2),
                                })
            # Yeni bir tarama, eski sonuç tablosundaki satır sayısını/sırasını
            # değiştirebilir - "scan_pick_<index>" gibi pozisyona bağlı eski
            # seçim/backtest-gün durumları yeni (alakasız) satırlara yapışmasın
            # diye temizlenir. Eski backtest sonuçları da artık bu taramaya ait
            # değil, onlar da silinir.
            stale_prefixes = ("scan_pick_", "scan_bt_days_", "scan_bt_pick_")
            for key in list(st.session_state.keys()):
                if key.startswith(stale_prefixes) or key == "scan_select_all":
                    del st.session_state[key]
            st.session_state.pop("scan_backtest_runs", None)
            st.session_state.scan_signals = signals

    if 'scan_signals' in st.session_state and st.session_state.scan_signals:
        st.subheader("🎯 Bulunan Formasyonlar")
        scan_results_df = pd.DataFrame(st.session_state.scan_signals)

        def _toggle_all_scan_rows():
            val = st.session_state.get("scan_select_all", False)
            for i in range(len(scan_results_df)):
                st.session_state[f"scan_pick_{i}"] = val

        st.checkbox("Tümünü seç", key="scan_select_all", on_change=_toggle_all_scan_rows)

        col_ratios = [2, 1.5, 1.1, 1.1, 1.4, 0.7]
        head_cols = st.columns(col_ratios)
        for col, label in zip(head_cols, ["Hisse (seç)", "Tarayıcı Türü", "Mum Periyodu", "Mum Seviyesi", "Backtest Gün Sayısı", "Grafik"]):
            col.markdown(f"**{label}**")

        selected_scan_tickers = []
        selected_backtest_rows = []
        for idx, row in scan_results_df.iterrows():
            c1, c2, c3, c4, c5, c6 = st.columns(col_ratios)
            picked = c1.checkbox(row["Hisse"], key=f"scan_pick_{idx}")
            if picked:
                selected_scan_tickers.append(row["Hisse"])
            c2.write(row["Tarayıcı Türü"])
            c3.write(row["Mum Periyodu"])
            c4.write(row["Mum Seviyesi"])
            is_algo_row = row["_kind"] == "algo"
            if is_algo_row:
                bt_max = DAILY_MAX_DAYS if row["_tf_code"] == "1Day" else INTRADAY_MAX_DAYS
                bt_days = c5.number_input(
                    f"{row['Hisse']} backtest gün sayısı", min_value=1, max_value=bt_max, value=bt_max, step=1,
                    key=f"scan_bt_days_{idx}", label_visibility="collapsed",
                )
                if picked:
                    selected_backtest_rows.append({
                        "Hisse": row["Hisse"], "algo_id": row["_algo_id"], "tf_code": row["_tf_code"],
                        "bt_days": int(bt_days),
                    })
            else:
                c5.caption("— (grafik formasyonu, backtest yok)")
            if c6.button("📊", key=f"scan_chart_{idx}", help=f"{row['Hisse']} ({row['Mum Periyodu']}) grafiğini göster"):
                st.session_state.selected_ticker = row["Hisse"]
                st.session_state.selected_ticker_timeframe = row["_tf_code"]
                st.session_state.selected_ticker_signal = {
                    "kind": row["_kind"], "label": row["Tarayıcı Türü"], "price": row["Mum Seviyesi"],
                }
                st.session_state.show_chart = True

        st.divider()
        xfer_col1, xfer_col2 = st.columns([3, 2])
        xfer_col1.caption(
            f"✅ {len(selected_scan_tickers)} hisse seçili."
            if selected_scan_tickers else "Aktarmak istediğiniz hisseleri yukarıdaki kutulardan seçin."
        )
        if xfer_col2.button(
            "➡️ Premium Buy Point Portföyüne Aktar", use_container_width=True,
            disabled=not selected_scan_tickers, key="scan_xfer_signals_btn",
        ):
            st.session_state["premium_buy_pending_transfer"] = list(dict.fromkeys(selected_scan_tickers))
            st.session_state["nav_category"] = "🤖 Algoritmik Ticaret"
            st.session_state["open_category"] = "🤖 Algoritmik Ticaret"
            st.session_state["active_module_🤖 Algoritmik Ticaret"] = "🎯 Premium Buy Point Portföyü"
            st.rerun()

        st.divider()
        bt_col1, bt_col2 = st.columns([3, 2])
        bt_col1.caption(
            f"🧪 {len(selected_backtest_rows)} algoritma sinyali seçili."
            if selected_backtest_rows
            else "Backtest çalıştırmak için algoritma sinyali veren satırlardan seçim yapın (grafik formasyonları - Fincan-Kulp/OBO/TOBO - için backtest yok)."
        )
        if bt_col2.button(
            "🧪 Seçilenler İçin Backtest Çalıştır", use_container_width=True,
            disabled=not selected_backtest_rows, key="scan_run_backtest_btn",
        ):
            progress = st.progress(0.0)
            run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            new_runs = []
            bt_runs = []
            for i, item in enumerate(selected_backtest_rows):
                symbol, algo_id, tf_code, bt_days = item["Hisse"], item["algo_id"], item["tf_code"], item["bt_days"]
                df_bt, _, _, _ = get_scanner_data(symbol, timeframe=tf_code, period_days=bt_days)
                if df_bt is not None and not df_bt.empty:
                    result = run_backtest(
                        symbol=symbol, algorithm=algo_id, timeframe=tf_code, bars=bars_from_df(df_bt),
                        daily_pairs=fetch_daily_pairs(symbol), days_of_data=bt_days, days_before_trading=0,
                        starting_budget=10000.0,
                    )
                    new_runs.append({
                        "run_id": new_run_id(symbol, algo_id, tf_code),
                        "run_at": run_at,
                        "symbol": symbol,
                        "algorithm": algo_id,
                        "timeframe": tf_code,
                        "days_of_data": bt_days,
                        "days_before_trading": 0,
                        "starting_budget": 10000.0,
                        "final_value": result.final_value,
                        "pnl": result.pnl,
                        "pnl_pct": result.pnl_pct,
                        "stop_loss_enabled": False,
                        "max_loss_pct": None,
                        "stop_loss_triggered": result.stop_loss_triggered,
                        "stop_loss_triggered_at": result.stop_loss_triggered_at,
                        "trades": [vars(t) for t in result.trades],
                        "source": "Yahoo Finance",
                    })
                    bt_runs.append({
                        "Hisse": symbol, "Algoritma": ALGORITHMS[algo_id][0],
                        "Mum Periyodu": SCAN_TIMEFRAME_LABELS[tf_code], "Gün": bt_days,
                        "K/Z %": result.pnl_pct, "İşlem Sayısı": len(result.trades),
                    })
                progress.progress((i + 1) / len(selected_backtest_rows))
            progress.empty()
            if new_runs:
                append_results(username, new_runs)
            st.session_state.scan_backtest_runs = bt_runs
            if not bt_runs:
                st.warning("Seçilenler için veri çekilemediğinden backtest çalıştırılamadı.")
    elif 'scan_signals' in st.session_state:
        st.warning("Tarama sonucunda uygun formasyon bulunamadı.")

    if 'scan_backtest_runs' in st.session_state and st.session_state.scan_backtest_runs:
        st.subheader("🧪 Backtest Sonuçları")
        st.caption(
            "Yahoo Finance verisiyle çalışır (Alpaca hesabı gerekmez) - sonuçlar BackTest modülünün kalıcı "
            "geçmişine \"Yahoo Finance\" kaynağıyla etiketlenerek ekleniyor, bu yüzden Premium Buy Point "
            "Portföyü'ndeki hisse bazlı algoritma seçiminde de görünür."
        )
        bt_runs_df = pd.DataFrame(st.session_state.scan_backtest_runs)
        with st.expander(f"Tüm çalıştırmalar ({len(bt_runs_df)})"):
            st.dataframe(bt_runs_df, use_container_width=True, hide_index=True)

        best_df = (
            bt_runs_df.sort_values("K/Z %", ascending=False)
            .drop_duplicates(subset="Hisse", keep="first")[["Hisse", "Algoritma", "Mum Periyodu", "K/Z %"]]
            .rename(columns={"K/Z %": "En Yüksek Karlılık (%)"})
            .reset_index(drop=True)
        )

        bh_ratios = [2, 1.8, 1.2, 1.5]
        bh0, bh1, bh2, bh3 = st.columns(bh_ratios)
        bh0.markdown("**Hisse (aktarmak için seç)**")
        bh1.markdown("**Algoritma**")
        bh2.markdown("**Mum Periyodu**")
        bh3.markdown("**En Yüksek Karlılık (%)**")
        selected_bt_tickers = []
        for idx, row in best_df.iterrows():
            r0, r1, r2, r3 = st.columns(bh_ratios)
            if r0.checkbox(row["Hisse"], key=f"scan_bt_pick_{idx}"):
                selected_bt_tickers.append(row["Hisse"])
            r1.write(row["Algoritma"])
            r2.write(row["Mum Periyodu"])
            r3.write(row["En Yüksek Karlılık (%)"])

        bt_xfer_col1, bt_xfer_col2 = st.columns([3, 2])
        bt_xfer_col1.caption(
            f"✅ {len(selected_bt_tickers)} hisse seçili."
            if selected_bt_tickers else "Aktarmak istediğiniz hisseleri yukarıdaki kutulardan seçin."
        )
        if bt_xfer_col2.button(
            "➡️ Premium Buy Point Portföyüne Aktar", use_container_width=True,
            disabled=not selected_bt_tickers, key="scan_bt_xfer_btn",
        ):
            st.session_state["premium_buy_pending_transfer"] = list(dict.fromkeys(selected_bt_tickers))
            st.session_state["nav_category"] = "🤖 Algoritmik Ticaret"
            st.session_state["open_category"] = "🤖 Algoritmik Ticaret"
            st.session_state["active_module_🤖 Algoritmik Ticaret"] = "🎯 Premium Buy Point Portföyü"
            st.rerun()

    if st.session_state.show_chart and st.session_state.selected_ticker:
        active_t = st.session_state.selected_ticker
        active_tf = st.session_state.get("selected_ticker_timeframe") or "1Day"
        st.write("---")
        st.markdown(f"### 📊 Formasyon Analiz Grafiği: **{active_t}** ({SCAN_TIMEFRAME_LABELS.get(active_tf, active_tf)})")
        active_days = daily_days if active_tf == "1Day" else intraday_days
        df, cup_pat, obo_pat, tobo_pat = get_scanner_data(active_t, timeframe=active_tf, period_days=active_days)
        if df is not None and not df.empty:
            viz_bars = {"15Min": 400, "30Min": 300, "1Hour": 250, "1Day": 126}.get(active_tf, 126)
            df_viz = df.iloc[-viz_bars:]
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
            active_signal = st.session_state.get("selected_ticker_signal") or {}
            if active_signal.get("kind") == "algo" and active_signal.get("price") is not None:
                fig.add_hline(
                    y=active_signal["price"], line_dash="dot", line_color="#ffd166",
                    annotation_text=f"{active_signal.get('label', 'Alım Sinyali')}: {active_signal['price']}",
                    annotation_position="bottom right",
                )
            fig.update_layout(
                title=f"{active_t} ({SCAN_TIMEFRAME_LABELS.get(active_tf, active_tf)}) - Alım Bölgesi Grafiği",
                template="plotly_dark", height=500, xaxis_rangeslider_visible=False,
            )
            st.plotly_chart(fig, use_container_width=True)


# ==============================================================================
# 2. MODÜL: STOP LOSS HESAPLAYICI
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
            st.dataframe(zebra_style(df_res), use_container_width=True, hide_index=True)

# ==============================================================================
# 3. MODÜL: DEĞERLEME & UCUZLUK SKORU (MİKRO İŞ MODELİ GRUPLAMALI)
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
            progress_bar = st.progress(0)
            status_text = st.empty()

            def _report_progress(done, total, ticker):
                status_text.text(f"Veriler kontrol ediliyor ({done}/{total}): {ticker}")
                progress_bar.progress(done / total)

            raw_results, freshly_fetched = fetch_tickers_with_shared_cache(scan_list, progress_callback=_report_progress)

            status_text.empty()
            progress_bar.empty()
            cached_count = len(scan_list) - len(freshly_fetched)
            st.caption(f"💾 {cached_count} hisse paylaşımlı önbellekten kullanıldı, {len(freshly_fetched)} hisse Yahoo Finance'den yeniden çekildi.")

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
# 4. MODÜL: BAĞIMSIZ HİSSE GRAFİĞİ
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
# 5. MODÜL: HİSSE LİSTELERİNİ YÖNET
# ==============================================================================
elif module == "⚙️ Hisse Listelerini Yönet":
    st.header("⚙️ Hisse Listelerini Düzenleme ve Kalıcı Kaydetme")

    selected_m = st.selectbox("Düzenlenecek Piyasayı Seçin:", ["NASDAQ 100", "BIST 100", "NYSE"])
    current_market_list = st.session_state.ticker_lists[selected_m]

    col_add, col_del = st.columns(2)

    with col_add:
        st.subheader("➕ Yeni Hisse Ekle")
        search_query = st.text_input(
            "Şirket adı veya sembol yazıp Enter'a basın:", key="ticker_search_query"
        )

        if "ticker_add_message" in st.session_state:
            kind, msg = st.session_state.pop("ticker_add_message")
            getattr(st, kind)(msg)

        def _add_symbol(symbol, market):
            if market == "BIST 100" and not symbol.endswith(".IS"):
                symbol += ".IS"
            if symbol in st.session_state.ticker_lists[market]:
                st.session_state.ticker_add_message = ("warning", f"⚠️ **{symbol}** zaten {market} listesinde mevcut.")
                return
            st.session_state.ticker_lists[market].append(symbol)
            save_ticker_lists(st.session_state.ticker_lists, username)
            st.session_state.ticker_add_message = ("success", f"✅ **{symbol}**, {market} listesine eklendi ve kaydedildi!")
            st.session_state.ticker_search_query = ""
            st.session_state.pop("ticker_search_results", None)
            st.session_state.pop("ticker_search_last_query", None)

        if search_query and search_query.strip():
            if st.session_state.get("ticker_search_last_query") != search_query:
                st.session_state.ticker_search_results = search_tickers(search_query)
                st.session_state.ticker_search_last_query = search_query

            search_results = st.session_state.get("ticker_search_results", [])
            if not search_results:
                st.info("Eşleşen sonuç bulunamadı.")
            else:
                for r in search_results:
                    label = f"{r['symbol']} — {r['name']}" + (f" ({r['exchange']})" if r['exchange'] else "")
                    st.button(
                        label, key=f"add_search_{r['symbol']}", use_container_width=True,
                        on_click=_add_symbol, args=(r['symbol'], selected_m),
                    )

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
# 6. MODÜL: HİSSE GRUPLARINI YÖNET
# ==============================================================================
elif module == "🗂️ Hisse Gruplarını Yönet":
    st.header("🗂️ Hisse Gruplarını Yönetme ve Kalıcı Kaydetme")
    st.caption(
        "Kendi hisse gruplarınızı oluşturun; her grup bir piyasayla (NASDAQ 100 / NYSE / "
        "BIST 100) ilişkilendirilir ve o piyasadaki hisseler arasından seçilir. Bu sayede "
        "gruplar kendi aralarında anlamlı biçimde analiz edilebilir. Sol menüde piyasa "
        "seçtiğinizde, '🗂️ Hisse Grubu' alanında sadece o piyasayla ilişkili gruplar listelenir."
    )

    st.subheader("➕ Yeni Hisse Grubu Oluştur")
    with st.form("new_group_form", clear_on_submit=True):
        new_group_name = st.text_input("Grup Adı:", placeholder="örn: Favorilerim, Temettü Hisseleri")
        new_group_market = st.selectbox("İlişkili Piyasa:", MARKETS)
        submitted = st.form_submit_button("Grup Oluştur")
        if submitted:
            name = new_group_name.strip()
            if not name:
                st.warning("⚠️ Lütfen bir grup adı girin.")
            elif name in st.session_state.stock_groups:
                st.warning(f"⚠️ **{name}** adında bir grup zaten mevcut.")
            else:
                st.session_state.stock_groups[name] = []
                st.session_state.group_markets[name] = new_group_market
                save_stock_groups(st.session_state.stock_groups, username)
                save_group_markets(st.session_state.group_markets, username)
                st.success(f"✅ **{name}** grubu **{new_group_market}** piyasasıyla ilişkilendirilerek oluşturuldu! Şimdi hisse ekleyebilirsiniz.")
                st.rerun()

    st.divider()

    if not st.session_state.stock_groups:
        st.info("Henüz hiç hisse grubunuz yok. Yukarıdan yeni bir grup oluşturarak başlayın.")
    else:
        st.subheader("📂 Mevcut Gruplar")
        group_names = list(st.session_state.stock_groups.keys())
        selected_group = st.selectbox("Düzenlenecek grubu seçin:", group_names, key="group_editor_select")
        current_group_tickers = st.session_state.stock_groups[selected_group]
        current_group_market = st.session_state.group_markets.get(selected_group)

        MARKET_PLACEHOLDER = "— Piyasa Seçin —"
        market_options = [MARKET_PLACEHOLDER] + MARKETS
        market_default_index = MARKETS.index(current_group_market) + 1 if current_group_market in MARKETS else 0
        chosen_market = st.selectbox(
            "🔗 İlişkili Piyasa:", market_options, index=market_default_index, key=f"market_select_{selected_group}"
        )
        if chosen_market != MARKET_PLACEHOLDER and chosen_market != current_group_market:
            st.session_state.group_markets[selected_group] = chosen_market
            save_group_markets(st.session_state.group_markets, username)
            st.success(f"🔗 **{selected_group}** grubu **{chosen_market}** piyasasıyla ilişkilendirildi.")
            st.rerun()
        if current_group_market is None:
            st.warning(
                "⚠️ Bu grubun piyasası henüz atanmadı. Yukarıdan bir piyasa seçmeden bu grup "
                "sol menüdeki piyasa filtresinde görünmeyecek."
            )

        col_add, col_del = st.columns(2)

        with col_add:
            st.markdown("**➕ Bu Gruba Hisse Ekle**")
            add_mode = st.radio(
                "Ekleme yöntemi:", ["Borsadan Seç", "Kendi Ticker'ımı Gireyim"],
                key=f"add_mode_{selected_group}", horizontal=True,
            )

            if add_mode == "Borsadan Seç":
                if not current_group_market:
                    st.info("ℹ️ Borsadan seçim yapabilmek için önce yukarıdan bu grubun piyasasını seçin.")
                else:
                    st.caption(f"Bu grup **{current_group_market}** piyasasıyla ilişkili; sadece bu piyasadaki hisseler listelenir.")
                    available = [t for t in st.session_state.ticker_lists[current_group_market] if t not in current_group_tickers]
                    picks = st.multiselect("Eklenecek hisseler:", available, key=f"picks_{selected_group}")
                    if st.button("Seçilenleri Gruba Ekle", key=f"add_picks_{selected_group}"):
                        if picks:
                            st.session_state.stock_groups[selected_group] = list(
                                dict.fromkeys(current_group_tickers + picks)
                            )
                            save_stock_groups(st.session_state.stock_groups, username)
                            st.success(f"✅ {len(picks)} hisse **{selected_group}** grubuna eklendi!")
                            st.rerun()
                        else:
                            st.warning("⚠️ Lütfen en az bir hisse seçin.")
            else:
                st.caption("💡 BIST hisseleri için `.IS` uzantısını eklemeyi unutmayın (örn: THYAO.IS).")
                custom_ticker = st.text_input(
                    "Ticker (örn: AAPL, THYAO.IS):", key=f"custom_ticker_{selected_group}"
                ).strip().upper()
                if st.button("Ticker'ı Gruba Ekle", key=f"add_custom_{selected_group}"):
                    if not custom_ticker:
                        st.warning("⚠️ Lütfen bir ticker girin.")
                    elif custom_ticker in current_group_tickers:
                        st.warning(f"⚠️ **{custom_ticker}** zaten bu grupta mevcut.")
                    else:
                        st.session_state.stock_groups[selected_group].append(custom_ticker)
                        save_stock_groups(st.session_state.stock_groups, username)
                        st.success(f"✅ **{custom_ticker}**, **{selected_group}** grubuna eklendi!")
                        st.rerun()

        with col_del:
            st.markdown("**🗑️ Gruptan Hisse Çıkar**")
            if current_group_tickers:
                ticker_to_remove = st.selectbox(
                    "Çıkarılacak hisse:", current_group_tickers, key=f"remove_sel_{selected_group}"
                )
                if st.button("Hisseyi Gruptan Çıkar", key=f"remove_btn_{selected_group}"):
                    st.session_state.stock_groups[selected_group].remove(ticker_to_remove)
                    save_stock_groups(st.session_state.stock_groups, username)
                    st.success(f"🗑️ **{ticker_to_remove}** çıkarıldı.")
                    st.rerun()
            else:
                st.caption("Bu grupta henüz hisse yok.")

        st.write("---")
        st.subheader(f"📋 {selected_group} İçeriği ({len(current_group_tickers)} Hisse)")
        st.write(", ".join(current_group_tickers) if current_group_tickers else "_Bu grup henüz boş._")

        st.write("<br>", unsafe_allow_html=True)
        st.markdown("**⚠️ Grubu Sil**")
        confirm_key = f"confirm_delete_group_{selected_group}"
        if st.button(f"🗑️ '{selected_group}' Grubunu Sil", key=f"del_group_btn_{selected_group}"):
            st.session_state[confirm_key] = True

        if st.session_state.get(confirm_key):
            st.warning(f"❓ **{selected_group}** grubunu silmek istediğinize emin misiniz? Bu işlem geri alınamaz.")
            cc1, cc2 = st.columns(2)
            if cc1.button("✅ Evet, Sil", type="primary", key=f"confirm_yes_{selected_group}"):
                del st.session_state.stock_groups[selected_group]
                st.session_state.group_markets.pop(selected_group, None)
                save_stock_groups(st.session_state.stock_groups, username)
                save_group_markets(st.session_state.group_markets, username)
                st.session_state.pop(confirm_key, None)
                st.session_state["_pending_group_removal"] = selected_group
                st.success(f"🗑️ **{selected_group}** grubu silindi.")
                st.rerun()
            if cc2.button("❌ Vazgeç", key=f"confirm_no_{selected_group}"):
                st.session_state.pop(confirm_key, None)
                st.rerun()

# ==============================================================================
# MODÜL: DTW ZAMAN SERİSİ & BENZERLİK ANALİZİ (GÖRÜNTÜLEME & TİP GÜVENCELİ)
# ==============================================================================
elif module == "🔄 DTW Zaman Serisi & Benzerlik Analizi":
    st.header("🔄 DTW (Dynamic Time Warping) Zaman Serisi & Benzerlik Analizi")
    st.caption(f"Seçili **{market}** kaynağındaki hisselerin son 2 gününün 5 dakikalık seans içi fiyat hareketlerini kıyaslar.")

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
        with st.spinner(f"{market} verileri Yahoo Finance'den çekiliyor ve Türkiye saatine çevriliyor..."):
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
                        change_pct, trend = compute_two_day_trend(d1, d2)
                        self_sim_results.append({
                            "Hisse": ticker,
                            "1. Gün Tarihi": dtw_data[ticker]["day1"]["date"],
                            "2. Gün Tarihi": dtw_data[ticker]["day2"]["date"],
                            "DTW Benzerlik Skoru %": sim,
                            "DTW Mesafesi": dist,
                            "2 Günlük Değişim %": change_pct,
                            "Trend": trend
                        })
                    st.session_state.self_sim_results = self_sim_results

                    # Sonuçları diske kaydet
                    save_cached_dtw_results(max_warping_window, time_penalty, self_sim_results)
                    st.success(f"✅ {len(dtw_data)} hissenin analizi tamamlandı!")
            else:
                st.error("⚠️ Veri çekilemedi.")

    # 3. Diskteki cache sonuçlarını her durumda (parametreler uyuşuyorsa) oturuma otomatik yükle
    cached_self = load_cached_dtw_results(max_warping_window, time_penalty)
    if cached_self is not None:
        st.session_state.self_sim_results = cached_self
    elif 'self_sim_results' not in st.session_state or not st.session_state.self_sim_results:
        if 'dtw_data' in st.session_state and st.session_state.dtw_data:
            dtw_data = st.session_state.dtw_data
            self_sim_results = []
            stock_keys = list(dtw_data.keys())
            for ticker in stock_keys:
                d1 = dtw_data[ticker]["day1"]["prices"]
                d2 = dtw_data[ticker]["day2"]["prices"]
                sim, dist = compute_dtw_similarity(d1, d2, max_warping_window, time_penalty)
                change_pct, trend = compute_two_day_trend(d1, d2)
                self_sim_results.append({
                    "Hisse": ticker,
                    "1. Gün Tarihi": dtw_data[ticker]["day1"]["date"],
                    "2. Gün Tarihi": dtw_data[ticker]["day2"]["date"],
                    "DTW Benzerlik Skoru %": sim,
                    "DTW Mesafesi": dist,
                    "2 Günlük Değişim %": change_pct,
                    "Trend": trend
                })
            st.session_state.self_sim_results = self_sim_results
            save_cached_dtw_results(max_warping_window, time_penalty, self_sim_results)

    # 4. GÖRSELLEŞTİRME KISMI (SEKMELER VE GÜVENLİ FİLTRELEME)
    if 'dtw_data' in st.session_state and st.session_state.dtw_data:
        stocks_dict = st.session_state.dtw_data
        stock_keys = list(stocks_dict.keys())

        tab1, tab2 = st.tabs(["📌 1. Kendi İçinde Benzerlik", "📈 2. İnteraktif Karşılaştırmalı Grafik"])

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
                    up_count = int((df_filtered_self["2 Günlük Değişim %"] >= 0).sum())
                    down_count = int((df_filtered_self["2 Günlük Değişim %"] < 0).sum())
                    tc1, tc2 = st.columns(2)
                    tc1.metric("📈 2 Günlük Yükseliş Trendinde", up_count)
                    tc2.metric("📉 2 Günlük Düşüş Trendinde", down_count)
                    st.dataframe(zebra_style(df_filtered_self), use_container_width=True, hide_index=True)
                else:
                    max_score = df_self["DTW Benzerlik Skoru %"].max() if not df_self.empty else 0
                    st.warning(f"⚠️ Seçtiğiniz **%{min_similarity}** eşik değerinin üzerinde öz-benzerlik gösteren hisse bulunamadı. (Bu veri setindeki en yüksek öz-benzerlik: **%{max_score}**).")
            else:
                st.warning("Veri bulunamadı. Lütfen yukarıdaki butona tıklayın.")

        # TAB 2: İNTERAKTİF KARŞILAŞTIRMALI GRAFİK (Sadece Görselde Türkiye Saati Dönüşümü)
        with tab2:
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
# 7. MODÜL: ALPACA CANLI POZİSYONLAR
# ==============================================================================
elif module == "🦙 Alpaca Canlı Pozisyonlar":
    st.header("🦙 Alpaca Canlı Pozisyonlar")
    st.caption("Açık pozisyonlar, güncel stop seviyeleri ve stoptan uzaklık. Stoplar structure-based trailing-stop GitHub Action tarafından 5 dakikada bir güncellenir.")
    render_alpaca_dashboard(username)

# ==============================================================================
# 8. MODÜL: PREMIUM BUY POINT PORTFÖYÜ
# ==============================================================================
elif module == "🎯 Premium Buy Point Portföyü":
    st.header("🎯 Premium Buy Point Portföyü")
    st.caption("Seçtiğiniz hisseler için demand zone (premium buy point) taranır; fiyat zone'a girdiğinde otomatik alım yapılır.")
    render_premium_buy_portfolio(target_list, username)

# ==============================================================================
# 8b. MODÜL: OTOMATİK ALIM/SATIM
# ==============================================================================
elif module == "🤖 Otomatik Alım/Satım":
    st.header("🤖 Otomatik Alım/Satım")
    st.caption(
        "Bu mod tamamen Alpaca'daki verilerle çalışır: NASDAQ 100, NYSE ve bu piyasalara bağlı "
        "kullanıcı tanımlı hisse gruplarını tarar, RSI14/RSI21 + EMA50/EMA200 momentum teyidiyle en "
        "fazla 10 hisseye daraltır, backtest uygular ve %10 üzeri kârlılık gösterenleri Premium Buy "
        "Point Portföyü'ne aktarır - günde 1 kez tamamen otomatik de çalışabilir."
    )
    render_otomatik_alim_satim(username)

# ==============================================================================
# 9. MODÜL: TÜRK FONLARI
# ==============================================================================
elif module == "Türk Fonları":
    st.header("🇹🇷 Türk Fonları")
    st.caption("TEFAS'tan günlük çekilen Hisse Senedi Yoğun, Değişken, Mutlak Getiri ve İstatistiksel Arbitraj fonlarının fiyat/hacim değişim tablosu.")
    render_turk_fonlari()

# ==============================================================================
# 10b. MODÜL: FONLARIM (TAKİP EDİLEN FONLAR)
# ==============================================================================
elif module == "Fonlarım":
    st.header("💼 Fonlarım")
    st.caption("Elinizde bulunan fonları kaydedin; her fon için KAP'tan çekilen en büyük 10 yatırım aracının yüzdesini takip edin.")
    render_turk_fonlari_takip(username)

# ==============================================================================
# 10. MODÜL: HİSSE PATERN ANALİZİ
# ==============================================================================
elif module == "📐 Hisse Patern Analizi":
    st.header("📐 Hisse Patern Analizi")
    st.caption("Seçilen hisselerin yıllık (3 yıllık), 3 aylık (son 2 yıl) ve aylık (son 12 ay) periyotlar arasındaki tekrarlayan fiyat paterni benzerliğini DTW ile ölçer.")
    render_hisse_patern(target_list)

# ==============================================================================
# 11. MODÜL: BACKTEST
# ==============================================================================
elif module == "BackTest":
    st.header("🧪 BackTest")
    st.caption("Premium buy-point algoritmalarını ve Alpaca'daki structure-based trailing stop'u seçtiğiniz hisse üzerinde geçmiş veriyle yeniden oynatır.")
    render_backtest(target_list, username)

# ==============================================================================
# 12. MODÜL: BIÇAK KANALI TESTİ
# ==============================================================================
elif module == "🔪 Bıçak Kanalı Testi":
    render_bicak_kanali_test(target_list)

# ==============================================================================
# YAZILIM SÜRÜMÜ (sol menünün en altı)
# ==============================================================================
st.sidebar.divider()
st.sidebar.markdown(
    f"<div style='font-style: italic; font-size: 8pt;'>Yazılım Sürümü: {get_version_label()}</div>",
    unsafe_allow_html=True,
)
