"""Relative Strength Rotasyonu - Streamlit sayfası.

Bu sistemdeki DİĞER algoritmik ticaret sayfalarıyla (premium_buy_portfolio.py,
otomatik_alim_satim.py) AYNI ilke: bu sayfa GERÇEK bir Alpaca emri
YERLEŞTİRMEZ - sadece ayarları (evren, top_n, geri bakış penceresi, nakit
payı, stop algoritması) GitHub'a kalıcı olarak kaydeder ve salt-okunur bir
ÖNİZLEME gösterir. Gerçek rebalance (satış/alım), kullanıcı "otomatik
çalıştır" kutusunu işaretlerse, haftada 1 kez relative_strength_runner.py
(GitHub Actions) tarafından yürütülür - bkz. relative_strength_core.py'nin
modül üstü notu.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from alpaca_client import AlpacaClient
from config import load_group_markets, load_stock_groups
from github_config import read_json_from_github, write_json_to_github
from otomatik_alim_satim_core import DEFAULT_MIN_AVG_DOLLAR_VOLUME, build_universe, filter_by_liquidity
from relative_strength_core import (
    DEFAULT_CASH_ALLOCATION_PCT, DEFAULT_LOOKBACK_WEEKS, DEFAULT_MIN_SCORE_PCT, DEFAULT_TOP_N,
    config_path, holdings_path, plan_rebalance,
)
from stop_algorithms import DEFAULT_STOP_ALGORITHM, STOP_ALGORITHMS
from ui_style import zebra_style, freshness_caption

TR_TZ = ZoneInfo("Europe/Istanbul")
GITHUB_REPO = "berkakar/yatirim"


def _load_config(repo: str, token: str, username: str) -> dict:
    return read_json_from_github(repo, token, config_path(username), {"enabled": False})


def _save_config(repo: str, token: str, config: dict, username: str) -> None:
    write_json_to_github(repo, token, config_path(username), config, f"Update Relative Strength Rotasyonu config ({username})")


def _load_holdings(repo: str, token: str, username: str) -> dict:
    return read_json_from_github(repo, token, holdings_path(username), {})


def render_relative_strength(username: str):
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
        "💰 **Nakit payı nasıl çalışır?** Bu modül, hesabınızın TOPLAM canlı nakdinden (yukarıdaki "
        "tutar) aşağıda belirlediğiniz yüzdeyi kendine AYIRIR - Premium Buy Point'in kullanabileceği "
        "nakit, otomatik olarak `toplam nakit × (1 - bu yüzde)` ile sınırlanır (bkz. alpaca_buy_points."
        "compute_available_cash_for_buying). Yani ikisi de TEK bir nakit havuzundan besleniyor, aynı "
        "dolarları iki kez harcamaya çalışmazlar. Bu modül devre dışıyken (\"otomatik çalıştır\" "
        "işaretli değilken) pay 0 kabul edilir - Premium Buy Point tüm nakdi kullanmaya devam eder."
    )
    cash_allocation_pct = st.number_input(
        "Bu modüle ayrılacak nakit payı (%)",
        min_value=0.0, max_value=100.0,
        value=float(config.get("cash_allocation_pct") or DEFAULT_CASH_ALLOCATION_PCT), step=5.0,
        key="rs_cash_allocation_pct",
        help="Örn. %30 girilirse, bu modül toplam nakdin %30'unu kullanır, Premium Buy Point kalan "
             "%70'i - modül devre dışıyken bu yüzde geçersizdir, PBP %100'ü kullanır.",
    )
    if live_cash is not None and cash_allocation_pct > 0:
        st.caption(f"Bu ayarla bu modüle düşen tutar: **${live_cash * cash_allocation_pct / 100:,.2f}**")

    st.subheader("🌐 Rotasyon Evreni")
    st.caption(
        "Premium Buy Point'in watchlist'indeki semboller bu evrenden HER ZAMAN hariç tutulur (bkz. "
        "relative_strength_core.rebalance) - aynı sembolde iki bağımsız sistemin çakışmaması için. "
        "Zaten büyük/likit bir evren istiyorsanız NASDAQ 100/NYSE, daha volatil bir evren istiyorsanız "
        "Russell 2000 seçin."
    )
    c1, c2, c3 = st.columns(3)
    include_nasdaq = c1.checkbox("NASDAQ 100", value=config.get("include_nasdaq", True), key="rs_include_nasdaq")
    include_nyse = c2.checkbox("NYSE", value=config.get("include_nyse", False), key="rs_include_nyse")
    include_russell = c3.checkbox("Russell 2000", value=config.get("include_russell", False), key="rs_include_russell")

    group_markets = load_group_markets(username)
    stock_groups = load_stock_groups(username)
    eligible_groups = [g for g in stock_groups if group_markets.get(g) in ("NASDAQ 100", "NYSE", "Russell 2000")]
    custom_groups = st.multiselect(
        "NASDAQ/NYSE/Russell 2000'e bağlı özel hisse grupları (varsa)",
        eligible_groups,
        default=[g for g in (config.get("custom_groups") or []) if g in eligible_groups],
        key="rs_custom_groups",
    )
    min_avg_dollar_volume = st.number_input(
        "Likidite eşiği - ortalama günlük ciro ($, son 20 işlem günü)",
        min_value=0.0, value=float(config.get("min_avg_dollar_volume") or DEFAULT_MIN_AVG_DOLLAR_VOLUME),
        step=500_000.0, format="%.0f", key="rs_min_avg_dollar_volume",
        help="Bu eşiğin altında ortalama günlük dolar cirosu olan semboller taramadan önce elenir - "
             "market emriyle giren bir sistemde kayma (slippage) riskini sınırlamak için.",
    )

    st.subheader("📊 Sıralama Parametreleri")
    p1, p2, p3 = st.columns(3)
    top_n = p1.number_input(
        "Kaç hisse tutulsun (top N)", min_value=1, max_value=50,
        value=int(config.get("top_n") or DEFAULT_TOP_N), step=1, key="rs_top_n",
        help="Her yeniden dengelemede evrenin en güçlü bu kadar hissesi hedeflenir - eşit ağırlıkla.",
    )
    lookback_weeks = p2.number_input(
        "Relative strength penceresi (hafta)", min_value=1, max_value=52,
        value=int(config.get("lookback_weeks") or DEFAULT_LOOKBACK_WEEKS), step=1, key="rs_lookback_weeks",
        help="Skor, bu pencerenin ilk ve son günlük kapanışı arasındaki yüzdesel getiridir.",
    )
    min_score_pct = p3.number_input(
        "Minimum mutlak momentum (%)", min_value=-100.0, max_value=100.0,
        value=float(config.get("min_score_pct") if config.get("min_score_pct") is not None else DEFAULT_MIN_SCORE_PCT),
        step=1.0, key="rs_min_score_pct",
        help="Bu eşiğin altında skoru olan hisseler, sıralamada en güçlü top_n'e girse bile ASLA "
             "seçilmez (dual momentum: göreceli güçlü olmak yetmez, mutlak olarak da pozitif olmalı). "
             "0 = sadece pozitif getirili hisseler.",
    )

    stop_algorithm_ids = list(STOP_ALGORITHMS.keys())
    current_stop_algorithm = config.get("stop_algorithm", DEFAULT_STOP_ALGORITHM)
    if current_stop_algorithm not in stop_algorithm_ids:
        current_stop_algorithm = DEFAULT_STOP_ALGORITHM
    stop_algorithm = st.selectbox(
        "Stop-Loss Algoritması (güvenlik ağı)",
        stop_algorithm_ids, index=stop_algorithm_ids.index(current_stop_algorithm),
        format_func=lambda k: STOP_ALGORITHMS[k].label, key="rs_stop_algorithm",
        help="Sıralamadan düşme (rank-based exit) ASIL çıkış disiplinidir - bu stop sadece iki "
             "yeniden dengeleme arasında fiyat çökerse diye bir güvenlik ağıdır. Trailing Stop "
             "GitHub Action'ı bu modülün elindeki pozisyonları da (Premium Buy Point'ten AYRI olarak) "
             "bu seçili algoritmayla yönetir.",
    )

    st.subheader("🕐 Haftalık Otomatik Çalıştırma")
    st.caption(
        "Etkinleştirilirse, yukarıdaki ayarlarla haftada 1 kez (her Pazartesi) gerçek satış/alım "
        "emirleri GitHub Actions üzerinden verilir. Manuel önizleme butonu her zaman kullanılabilir "
        "kalır ama HİÇBİR ZAMAN gerçek emir vermez - sadece ne olacağını gösterir."
    )
    automated = st.checkbox(
        "Bu süreci otomatik çalıştır (haftada 1 kez, Pazartesi)", value=bool(config.get("enabled")), key="rs_enabled",
    )
    if config.get("last_run_at"):
        summary = config.get("last_run_summary") or {}
        if summary.get("skipped"):
            st.caption(f"Son otomatik koşu: {config['last_run_at']} — atlandı ({summary.get('reason')}).")
        else:
            st.caption(
                f"Son otomatik koşu: {config['last_run_at']} — evren {summary.get('universe_size', '—')}, "
                f"hedef: {', '.join(summary.get('target_symbols') or []) or 'yok'}, "
                f"satılan: {', '.join(summary.get('sold') or []) or 'yok'}, "
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
            "top_n": int(top_n),
            "lookback_weeks": int(lookback_weeks),
            "min_score_pct": float(min_score_pct),
            "stop_algorithm": stop_algorithm,
        })
        _save_config(GITHUB_REPO, github_token, new_config, username)
        st.success("Ayarlar kaydedildi.")
        st.rerun()

    st.divider()
    st.subheader("🔎 Önizleme (salt-okunur - gerçek emir vermez)")
    if st.button("🔎 Şimdi Sırala ve Önizle"):
        with st.spinner("Evren taranıyor ve sıralanıyor..."):
            universe = build_universe(username, include_nasdaq, include_nyse, custom_groups, include_russell)
            try:
                pbp_watchlist = client.get_watchlist_by_name(f"premium-buy-portfolio-{username}")
                pbp_symbols = {a["symbol"] for a in pbp_watchlist["assets"]} if pbp_watchlist else set()
            except Exception:
                pbp_symbols = set()
            universe = [t for t in universe if t not in pbp_symbols]
            universe = filter_by_liquidity(client, universe, float(min_avg_dollar_volume))
            plan = plan_rebalance(client, username, universe, int(top_n), int(lookback_weeks), float(min_score_pct))
        st.session_state["rs_preview_plan"] = plan
        st.session_state["rs_preview_fetched_at"] = datetime.now(TR_TZ)

    plan = st.session_state.get("rs_preview_plan")
    if plan is not None:
        fetched_at = st.session_state.get("rs_preview_fetched_at")
        if fetched_at:
            freshness_caption(f"Veri güncelliği: {fetched_at:%d.%m.%Y %H:%M:%S} TRT (Alpaca'dan önizleme anında çekildi).")
        st.caption(f"Taranan evren: {plan.universe_size} hisse (Premium Buy Point watchlist'i hariç, likidite eşiğinden geçmiş).")

        rows = [
            {"Hisse": s, "Skor (%)": round(plan.scores.get(s, 0.0) * 100, 2),
             "Durum": "Tutuluyor" if s in plan.to_hold else "YENİ ALINACAK"}
            for s in plan.target_symbols
        ]
        if rows:
            st.dataframe(zebra_style(pd.DataFrame(rows)), use_container_width=True, hide_index=True)
        else:
            st.info("Bu kriterlere uyan hiçbir hisse yok (mutlak momentum eşiği çok yüksek olabilir).")

        if plan.to_sell:
            st.warning(f"🔻 Sıralamadan düşecek (SATILACAK): {', '.join(plan.to_sell)}")

    st.divider()
    st.subheader("📦 Şu Anki Pozisyonlar")
    holdings = _load_holdings(GITHUB_REPO, github_token, username)
    if not holdings:
        st.info("Bu modülün şu an elinde hiçbir pozisyon yok.")
    else:
        rows = [
            {"Hisse": symbol, "Adet": info.get("qty"), "Giriş Fiyatı": info.get("entry_price"),
             "Giriş Skoru (%)": info.get("score_pct"), "Giriş Tarihi": info.get("entered_at")}
            for symbol, info in holdings.items()
        ]
        st.dataframe(zebra_style(pd.DataFrame(rows)), use_container_width=True, hide_index=True)
