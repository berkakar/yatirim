"""Açılış Aralığı Kırılımı (ORB) - Streamlit sayfası.

Bu sistemdeki DİĞER algoritmik ticaret sayfalarıyla (premium_buy_portfolio.py,
otomatik_alim_satim.py, relative_strength.py) AYNI ilke: bu sayfa GERÇEK bir
Alpaca emri YERLEŞTİRMEZ - sadece ayarları (evren, top_n, mum periyodu,
hacim/kırılım eşikleri, nakit payı, stop algoritması) GitHub'a kalıcı olarak
kaydeder ve salt-okunur bir ÖNİZLEME gösterir. Gerçek tarama+alım, kullanıcı
"otomatik çalıştır" kutusunu işaretlerse, her gün piyasa açılışından bir süre
sonra orb_scan_runner.py (GitHub Actions) tarafından yürütülür - bkz.
orb_core.py'nin modül üstü notu.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from alpaca_client import AlpacaClient
from config import load_group_markets, load_stock_groups
from github_config import read_json_from_github, write_json_to_github
from orb_core import (
    DEFAULT_CASH_ALLOCATION_PCT, DEFAULT_MAX_BARS_AFTER_OPEN, DEFAULT_TIMEFRAME, DEFAULT_TOP_N,
    DEFAULT_VOLUME_MULT, ORB_DEFAULT_STOP_ALGORITHM, config_path, holdings_path, scan_candidates,
    select_top_candidates,
)
from otomatik_alim_satim_core import DEFAULT_MIN_AVG_DOLLAR_VOLUME, TIMEFRAME_LABELS, build_universe, filter_by_liquidity
from stop_algorithms import STOP_ALGORITHMS
from ui_style import zebra_style, freshness_caption

TR_TZ = ZoneInfo("Europe/Istanbul")
GITHUB_REPO = "berkakar/yatirim"


def _load_config(repo: str, token: str, username: str) -> dict:
    return read_json_from_github(repo, token, config_path(username), {"enabled": False})


def _save_config(repo: str, token: str, config: dict, username: str) -> None:
    write_json_to_github(repo, token, config_path(username), config, f"Update ORB Scan config ({username})")


def _load_holdings(repo: str, token: str, username: str) -> dict:
    return read_json_from_github(repo, token, holdings_path(username), {})


def render_orb_scan(username: str):
    user_alpaca = st.secrets.get("alpaca", {}).get(username, {})
    key_id = user_alpaca.get("key_id")
    secret_key = user_alpaca.get("secret_key")
    github_token = st.secrets.get("GITHUB_TOKEN")

    if not key_id or not secret_key:
        st.warning(f"'{username}' için Alpaca hesabı tanımlı değil (`.streamlit/secrets.toml` içinde `[alpaca.{username}]`).")
        return
    if not github_token:
        st.warning("`.streamlit/secrets.toml` içinde GITHUB_TOKEN tanımlı değil - ayarlar kaydedilemez.")
        return

    client = AlpacaClient(key_id, secret_key)
    config = _load_config(GITHUB_REPO, github_token, username)

    try:
        live_cash = float(client.get_account()["cash"])
    except Exception:
        live_cash = None

    if live_cash is not None:
        st.metric("Alpaca'daki kullanılabilir nakit (toplam)", f"${live_cash:,.2f}")
    else:
        st.warning("⚠️ Alpaca'daki güncel nakit bakiye alınamadı.")

    st.info(
        "💰 **Nakit payı nasıl çalışır?** Bu modül de (Relative Strength Rotasyonu ile aynı ilke) "
        "hesabınızın TOPLAM canlı nakdinden bir yüzde AYIRIR - Premium Buy Point'in ve Relative "
        "Strength Rotasyonu'nun kullanabileceği nakit buna göre otomatik küçülür (bkz. alpaca_buy_points."
        "compute_available_cash_for_buying). Üç modül de TEK bir nakit havuzundan besleniyor. Bu modül "
        "devre dışıyken pay 0 kabul edilir."
    )
    cash_allocation_pct = st.number_input(
        "Bu modüle ayrılacak nakit payı (%)",
        min_value=0.0, max_value=100.0,
        value=float(config.get("cash_allocation_pct") or DEFAULT_CASH_ALLOCATION_PCT), step=5.0,
        key="orb_cash_allocation_pct",
        help="Örn. %20 girilirse, bu modül toplam nakdin %20'sini kullanır. Diğer modüllerle "
             "toplamı %100'ü aşarsa alpaca_buy_points.py bunu otomatik sınırlar.",
    )
    if live_cash is not None and cash_allocation_pct > 0:
        st.caption(f"Bu ayarla bu modüle düşen tutar: **${live_cash * cash_allocation_pct / 100:,.2f}**")

    st.subheader("🌐 Tarama Evreni")
    st.caption(
        "Premium Buy Point'in watchlist'indeki VE Relative Strength Rotasyonu'nun elindeki semboller "
        "bu evrenden HER ZAMAN hariç tutulur - üç bağımsız sistemin çakışmaması için. **Russell 2000, "
        "ORB literatüründeki 'kâr getiren hisse' profiline (küçük/orta cap, düşük float, volatil) en "
        "yakın evren olduğundan varsayılan olarak seçili gelir.**"
    )
    c1, c2, c3 = st.columns(3)
    include_nasdaq = c1.checkbox("NASDAQ 100", value=config.get("include_nasdaq", False), key="orb_include_nasdaq")
    include_nyse = c2.checkbox("NYSE", value=config.get("include_nyse", False), key="orb_include_nyse")
    include_russell = c3.checkbox("Russell 2000", value=config.get("include_russell", True), key="orb_include_russell")

    group_markets = load_group_markets(username)
    stock_groups = load_stock_groups(username)
    eligible_groups = [g for g in stock_groups if group_markets.get(g) in ("NASDAQ 100", "NYSE", "Russell 2000")]
    custom_groups = st.multiselect(
        "NASDAQ/NYSE/Russell 2000'e bağlı özel hisse grupları (varsa)",
        eligible_groups,
        default=[g for g in (config.get("custom_groups") or []) if g in eligible_groups],
        key="orb_custom_groups",
    )
    min_avg_dollar_volume = st.number_input(
        "Likidite eşiği - ortalama günlük ciro ($, son 20 işlem günü)",
        min_value=0.0, value=float(config.get("min_avg_dollar_volume") or DEFAULT_MIN_AVG_DOLLAR_VOLUME),
        step=500_000.0, format="%.0f", key="orb_min_avg_dollar_volume",
        help="Bu eşiğin altında ortalama günlük dolar cirosu olan semboller taramadan önce elenir - "
             "market emriyle giren bu sistemde kayma (slippage) riskini sınırlamak için.",
    )

    st.subheader("📊 Kırılım Parametreleri")
    p1, p2, p3 = st.columns(3)
    # "1Day" kasıtlı olarak yok - o periyotta "seansın açılış barı" tüm gün
    # demek olur, orb_signal bu durumda anlamlı bir sinyal üretemez (session_bars
    # her zaman 1 bar uzunluğunda kalır).
    timeframe_ids = [tf for tf in TIMEFRAME_LABELS if tf != "1Day"]
    current_timeframe = config.get("timeframe", DEFAULT_TIMEFRAME)
    if current_timeframe not in timeframe_ids:
        current_timeframe = DEFAULT_TIMEFRAME
    timeframe = p1.selectbox(
        "Mum periyodu", timeframe_ids, index=timeframe_ids.index(current_timeframe),
        format_func=lambda tf: TIMEFRAME_LABELS[tf], key="orb_timeframe",
        help="Açılış aralığı, seçilen periyottaki İLK bar sayılır - ORB literatüründe 15 dakika "
             "önerilir (daha kısa periyotlar bu sistemde henüz desteklenmiyor).",
    )
    top_n = p2.number_input(
        "Kaç hisse alınsın (top N)", min_value=1, max_value=20,
        value=int(config.get("top_n") or DEFAULT_TOP_N), step=1, key="orb_top_n",
        help="Her gün, geçerli bir kırılım sinyali üreten adaylar arasından en yüksek puanlı bu kadarı "
             "eşit ağırlıkla alınır.",
    )
    volume_mult = p3.number_input(
        "Hacim çarpanı eşiği", min_value=1.0, max_value=10.0,
        value=float(config.get("volume_mult") or DEFAULT_VOLUME_MULT), step=0.1, key="orb_volume_mult",
        help="Kırılım barının hacmi, açılış barının hacminin en az bu katı olmalı.",
    )
    max_bars_after_open = st.number_input(
        "Kırılım seansın ilk kaç barı içinde geçerli sayılsın", min_value=1, max_value=20,
        value=int(config.get("max_bars_after_open") or DEFAULT_MAX_BARS_AFTER_OPEN), step=1,
        key="orb_max_bars_after_open",
        help="Bu pencere dışında oluşan bir kırılım artık 'açılış' kırılımı sayılmaz.",
    )

    stop_algorithm_ids = list(STOP_ALGORITHMS.keys())
    current_stop_algorithm = config.get("stop_algorithm", ORB_DEFAULT_STOP_ALGORITHM)
    if current_stop_algorithm not in stop_algorithm_ids:
        current_stop_algorithm = ORB_DEFAULT_STOP_ALGORITHM
    stop_algorithm = st.selectbox(
        "Stop-Loss Algoritması",
        stop_algorithm_ids, index=stop_algorithm_ids.index(current_stop_algorithm),
        format_func=lambda k: STOP_ALGORITHMS[k].label, key="orb_stop_algorithm",
        help="Varsayılan 'Açılış Aralığı (ORB) Stop' burada GERÇEKTEN yapısal çalışır (Relative "
             "Strength Rotasyonu'nun aksine, bu modül stopu girişin yapıldığı seansın açılış barına "
             "göre kuruyor) - ilk stop, sabit yüzde yerine açılış aralığının ters ucuna (long için low) "
             "kurulur.",
    )

    st.subheader("🕐 Günlük Otomatik Çalıştırma")
    st.caption(
        "Etkinleştirilirse, yukarıdaki ayarlarla her gün piyasa açılışından bir süre sonra "
        "GitHub Actions üzerinden evren taranır, en yüksek puanlı top N market emriyle alınır ve "
        "anında stop kurulur. Manuel önizleme butonu her zaman kullanılabilir kalır ama HİÇBİR ZAMAN "
        "gerçek emir vermez - sadece ne olacağını gösterir."
    )
    automated = st.checkbox(
        "Bu süreci otomatik çalıştır (her gün, piyasa açılışında)", value=bool(config.get("enabled")), key="orb_enabled",
    )
    if config.get("last_run_at"):
        summary = config.get("last_run_summary") or {}
        if summary.get("skipped"):
            st.caption(f"Son otomatik koşu: {config['last_run_at']} — atlandı ({summary.get('reason')}).")
        else:
            st.caption(
                f"Son otomatik koşu: {config['last_run_at']} — evren {summary.get('universe_size', '—')}, "
                f"aday {summary.get('candidate_count', '—')}, "
                f"seçilen: {', '.join(summary.get('selected_symbols') or []) or 'yok'}, "
                f"alınan: {', '.join(summary.get('bought') or []) or 'yok'}."
            )

    if st.button("💾 Ayarları Kaydet", type="primary"):
        new_config = dict(config)
        new_config.update({
            "enabled": automated,
            "cash_allocation_pct": float(cash_allocation_pct),
            "include_nasdaq": include_nasdaq,
            "include_nyse": include_nyse,
            "include_russell": include_russell,
            "custom_groups": custom_groups,
            "min_avg_dollar_volume": float(min_avg_dollar_volume),
            "timeframe": timeframe,
            "top_n": int(top_n),
            "volume_mult": float(volume_mult),
            "max_bars_after_open": int(max_bars_after_open),
            "stop_algorithm": stop_algorithm,
        })
        _save_config(GITHUB_REPO, github_token, new_config, username)
        st.success("Ayarlar kaydedildi.")
        st.rerun()

    st.divider()
    st.subheader("🔎 Önizleme (salt-okunur - gerçek emir vermez)")
    if st.button("🔎 Şimdi Tara ve Önizle"):
        with st.spinner("Evren taranıyor ve kırılım adayları puanlanıyor..."):
            universe = build_universe(username, include_nasdaq, include_nyse, custom_groups, include_russell)
            try:
                pbp_watchlist = client.get_watchlist_by_name(f"premium-buy-portfolio-{username}")
                pbp_symbols = {a["symbol"] for a in pbp_watchlist["assets"]} if pbp_watchlist else set()
            except Exception:
                pbp_symbols = set()
            from relative_strength_core import load_holdings_local as load_rs_holdings
            rs_symbols = set(load_rs_holdings(username).keys())
            own_holdings = _load_holdings(GITHUB_REPO, github_token, username)
            universe = [
                t for t in universe
                if t not in pbp_symbols and t not in rs_symbols and t not in own_holdings
            ]
            universe = filter_by_liquidity(client, universe, float(min_avg_dollar_volume))
            candidates = scan_candidates(client, universe, timeframe, float(volume_mult), int(max_bars_after_open))
            selected = select_top_candidates(candidates, int(top_n))
        st.session_state["orb_preview_candidates"] = candidates
        st.session_state["orb_preview_selected"] = {c.symbol for c in selected}
        st.session_state["orb_preview_universe_size"] = len(universe)
        st.session_state["orb_preview_fetched_at"] = datetime.now(TR_TZ)

    candidates = st.session_state.get("orb_preview_candidates")
    if candidates is not None:
        fetched_at = st.session_state.get("orb_preview_fetched_at")
        if fetched_at:
            freshness_caption(f"Veri güncelliği: {fetched_at:%d.%m.%Y %H:%M:%S} TRT (Alpaca'dan önizleme anında çekildi).")
        st.caption(f"Taranan evren: {st.session_state.get('orb_preview_universe_size', 0)} hisse.")

        selected_symbols = st.session_state.get("orb_preview_selected") or set()
        if candidates:
            rows = [
                {"Hisse": c.symbol, "Fiyat": c.price, "Alım Puanı": c.score,
                 "Seçili": c.symbol in selected_symbols, "Neden": c.reason}
                for c in candidates
            ]
            st.dataframe(zebra_style(pd.DataFrame(rows)), use_container_width=True, hide_index=True)
        else:
            st.info("Şu an geçerli bir kırılım sinyali üreten hisse yok (bu, seansın erken/geç bir "
                    "saatinde beklenen bir durumdur).")

    st.divider()
    st.subheader("📦 Şu Anki Pozisyonlar")
    holdings = _load_holdings(GITHUB_REPO, github_token, username)
    if not holdings:
        st.info("Bu modülün şu an elinde hiçbir pozisyon yok.")
    else:
        rows = [
            {"Hisse": symbol, "Adet": info.get("qty"), "Giriş Fiyatı": info.get("entry_price"),
             "Giriş Puanı": info.get("score"), "Giriş Tarihi": info.get("entered_at")}
            for symbol, info in holdings.items()
        ]
        st.dataframe(zebra_style(pd.DataFrame(rows)), use_container_width=True, hide_index=True)
