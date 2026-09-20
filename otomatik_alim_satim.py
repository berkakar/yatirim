from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from alpaca_client import AlpacaClient
from backtest_data import append_results
from buy_algorithms import ALGORITHMS
from config import load_group_markets, load_stock_groups
from github_config import read_json_from_github, write_json_to_github
from otomatik_alim_satim_core import (
    DEFAULT_ALGORITHM_ID, DEFAULT_DAYS_BEFORE_TRADING, DEFAULT_DAYS_OF_DATA, DEFAULT_MAX_CANDIDATES,
    DEFAULT_MIN_BACKTEST_PROFIT_PCT, DEFAULT_MOMENTUM_LOOKBACK_DAYS, TIMEFRAME_LABELS, build_universe,
    filter_profitable, narrow_by_momentum, run_backtests, scan_universe,
)
from ui_style import zebra_style, freshness_caption

TR_TZ = ZoneInfo("Europe/Istanbul")

GITHUB_REPO = "berkakar/yatirim"


def _config_path(username: str) -> str:
    return f"otomatik_alim_satim_config_{username}.json"


def _load_config(repo: str, token: str, username: str) -> dict:
    return read_json_from_github(repo, token, _config_path(username), {"enabled": False})


def _save_config(repo: str, token: str, config: dict, username: str) -> None:
    write_json_to_github(repo, token, _config_path(username), config, f"Update otomatik alım/satım config ({username})")


def render_otomatik_alim_satim(username: str):
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
        st.metric("Alpaca'daki kullanılabilir nakit", f"${live_cash:,.2f}")
    else:
        st.warning("⚠️ Alpaca'daki güncel nakit bakiye alınamadı.")

    cash_allocation = st.number_input(
        "Otomatik modda kullanılacak nakit tutarı ($)",
        min_value=0.0,
        value=float(config.get("cash_allocation") or (live_cash or 0.0)),
        step=100.0,
        key="oas_cash_allocation",
        help="Nakdin ne kadarı bu otomatik alım/satım stratejisine ayrılacak - kalanı nakitte tutulur.",
    )
    if live_cash is not None and cash_allocation > live_cash:
        st.warning(f"Girdiğiniz tutar (${cash_allocation:,.2f}), Alpaca'daki nakit bakiyeyi (${live_cash:,.2f}) aşıyor.")

    st.subheader("🌐 Tarama Evreni")
    st.caption(
        "Bu tarama **NASDAQ 100**, **NYSE** ve bu piyasalara bağlı (🗂️ Hisse Gruplarını Yönet'te "
        "atanmış) kullanıcı tanımlı hisse gruplarını kapsar - Alpaca'da işlem görmeyen BIST hisseleri "
        "bu modüle dahil değildir."
    )
    c1, c2 = st.columns(2)
    include_nasdaq = c1.checkbox("NASDAQ 100", value=config.get("include_nasdaq", True), key="oas_include_nasdaq")
    include_nyse = c2.checkbox("NYSE", value=config.get("include_nyse", True), key="oas_include_nyse")

    group_markets = load_group_markets(username)
    stock_groups = load_stock_groups(username)
    eligible_groups = [g for g in stock_groups if group_markets.get(g) in ("NASDAQ 100", "NYSE")]
    custom_groups = st.multiselect(
        "NASDAQ/NYSE'ye bağlı özel hisse grupları (varsa)",
        eligible_groups,
        default=[g for g in (config.get("custom_groups") or []) if g in eligible_groups],
        key="oas_custom_groups",
    )

    st.subheader("🧮 Buy Point Algoritmaları")
    saved_algorithms = config.get("algorithms") or [DEFAULT_ALGORITHM_ID]
    selected_algo_ids = []
    algo_cols = st.columns(len(ALGORITHMS))
    for col, (algo_id, (label, _fn)) in zip(algo_cols, ALGORITHMS.items()):
        if col.checkbox(label, value=algo_id in saved_algorithms, key=f"oas_algo_{algo_id}"):
            selected_algo_ids.append(algo_id)

    st.subheader("🕯️ Mum Periyodu")
    saved_timeframes = config.get("timeframes") or ["1Day"]
    selected_timeframes = []
    tf_cols = st.columns(len(TIMEFRAME_LABELS))
    for col, (tf_code, tf_label) in zip(tf_cols, TIMEFRAME_LABELS.items()):
        if col.checkbox(tf_label, value=tf_code in saved_timeframes, key=f"oas_tf_{tf_code}"):
            selected_timeframes.append(tf_code)

    if st.button("🚀 Seçili Kriterlerle Tara", type="primary"):
        if not selected_algo_ids or not selected_timeframes:
            st.error("En az bir algoritma ve bir mum periyodu seçin.")
        else:
            universe = build_universe(username, include_nasdaq, include_nyse, custom_groups)
            with st.spinner(f"{len(universe)} hisse taranıyor..."):
                st.session_state["oas_scan_rows"] = scan_universe(client, universe, selected_algo_ids, selected_timeframes)
            st.session_state["oas_scan_fetched_at"] = datetime.now(TR_TZ)
            st.session_state.pop("oas_candidates", None)
            st.session_state.pop("oas_backtest_rows", None)

    scan_rows = st.session_state.get("oas_scan_rows") or []
    if scan_rows:
        st.subheader(f"📋 Tarama Sonuçları ({len(scan_rows)})")
        scan_fetched_at = st.session_state.get("oas_scan_fetched_at")
        if scan_fetched_at:
            freshness_caption(f"Veri güncelliği: {scan_fetched_at:%d.%m.%Y %H:%M:%S} TRT (Alpaca'dan tarama anında çekildi).")
        display_cols = ["Hisse", "Tarayıcı Türü", "Mum Periyodu", "Mum Seviyesi"]
        st.dataframe(zebra_style(pd.DataFrame(scan_rows)[display_cols]), use_container_width=True, hide_index=True)

        momentum_lookback_days = st.number_input(
            "Ek algoritmik filtre kaç gün geriye dönük uygulansın",
            min_value=1, max_value=180,
            value=int(config.get("momentum_lookback_days") or DEFAULT_MOMENTUM_LOOKBACK_DAYS),
            step=1,
            key="oas_momentum_lookback_days",
            help="RSI14/RSI21 ve EMA50/EMA200 kesişimlerinin aranacağı geriye dönük gün sayısı.",
        )

        if st.button("🧭 Ek Algoritma ile Daralt (RSI14/RSI21 + EMA50/EMA200)"):
            with st.spinner(f"Son {int(momentum_lookback_days)} gündeki momentum teyidi kontrol ediliyor..."):
                st.session_state["oas_candidates"] = narrow_by_momentum(
                    client, scan_rows, DEFAULT_MAX_CANDIDATES, int(momentum_lookback_days),
                )
            st.session_state["oas_candidates_fetched_at"] = datetime.now(TR_TZ)
            st.session_state.pop("oas_backtest_rows", None)

    candidates = st.session_state.get("oas_candidates")
    if candidates is not None:
        used_lookback_days = int(st.session_state.get("oas_momentum_lookback_days", DEFAULT_MOMENTUM_LOOKBACK_DAYS))
        st.subheader(f"🎯 Daraltılmış Adaylar ({len(candidates)}/{DEFAULT_MAX_CANDIDATES})")
        st.caption(
            f"Son {used_lookback_days} gün içinde RSI14'ün RSI21'i YUKARI kesmesi VE EMA50'nin EMA200'ü "
            "yukarı kesmesi (\"Golden Cross\") olaylarını BİRLİKTE gösteren hisseler - iki bağımsız "
            "momentum sinyalinin aynı pencerede teyidi. Bu sıkı bir filtredir, kısa bir pencerede bazı "
            "taramalarda 0 aday çıkması beklenir; 10, üst sınırdır."
        )
        if not candidates:
            st.info("Bu kriterlere uyan hisse bulunamadı.")
        else:
            candidates_fetched_at = st.session_state.get("oas_candidates_fetched_at")
            if candidates_fetched_at:
                freshness_caption(f"Veri güncelliği: {candidates_fetched_at:%d.%m.%Y %H:%M:%S} TRT (Alpaca'dan tarama anında çekildi).")
            display_cols = ["Hisse", "Tarayıcı Türü", "Mum Periyodu", "Mum Seviyesi"]
            st.dataframe(zebra_style(pd.DataFrame(candidates)[display_cols]), use_container_width=True, hide_index=True)

            if st.button("🧪 Backtest Uygula", type="primary"):
                with st.spinner("Backtest çalıştırılıyor..."):
                    all_results = run_backtests(
                        client, candidates, float(cash_allocation), username,
                        DEFAULT_DAYS_OF_DATA, DEFAULT_DAYS_BEFORE_TRADING,
                    )
                    append_results(username, all_results)
                    st.session_state["oas_backtest_rows"] = filter_profitable(all_results, DEFAULT_MIN_BACKTEST_PROFIT_PCT)

    backtest_rows = st.session_state.get("oas_backtest_rows")
    if backtest_rows is not None:
        st.subheader(f"📊 Backtest Sonuçları (K/Z > %{DEFAULT_MIN_BACKTEST_PROFIT_PCT:g})")
        if not backtest_rows:
            st.info("Hiçbir aday %10'un üzerinde kârlılık göstermedi.")
        else:
            # backtest_rows her "Backtest Uygula" tıklamasında değişebilir (farklı
            # aday sayısı/sırası) - picker'ın key'i sabit kalsaydı, Streamlit eski
            # çalıştırmadan kalan satır seçimlerini yeni satırlara yanlış eşleyebilirdi
            # (premium_buy_portfolio.py'deki aynı sorunun aynı çözümü: sayaç arttıkça
            # widget'ı SIFIRDAN saydırmak).
            if "oas_backtest_picker_token" not in st.session_state:
                st.session_state["oas_backtest_picker_token"] = 0
            if st.session_state.get("oas_backtest_rows_token_source") is not backtest_rows:
                st.session_state["oas_backtest_picker_token"] += 1
                st.session_state["oas_backtest_rows_token_source"] = backtest_rows
            picker_token = st.session_state["oas_backtest_picker_token"]

            backtest_run_at = backtest_rows[0].get("run_at") if backtest_rows else None
            if backtest_run_at:
                freshness_caption(f"Bu backtest çalıştırması: {backtest_run_at} UTC.")

            df = pd.DataFrame(backtest_rows)[["symbol", "algorithm", "timeframe", "pnl_pct", "final_value"]].copy()
            df["algorithm"] = df["algorithm"].map(lambda a: ALGORITHMS[a][0])
            df["timeframe"] = df["timeframe"].map(lambda tf: TIMEFRAME_LABELS.get(tf, tf))
            df.columns = ["Hisse", "Algoritma", "Mum Periyodu", "Karlılık %", "Son Değer ($)"]
            df["Seçili"] = True
            edited = st.data_editor(
                df,
                column_config={"Seçili": st.column_config.CheckboxColumn(required=True)},
                hide_index=True,
                use_container_width=True,
                key=f"oas_backtest_picker_{picker_token}",
            )
            selected_backtest_rows = [backtest_rows[i] for i in edited.index[edited["Seçili"]]]

            if st.button("➡️ Premium Buy Portföyüne Aktar", type="primary"):
                if not selected_backtest_rows:
                    st.error("Aktarmak için en az bir hisse seçin.")
                else:
                    # Ağırlık burada hesaplanmıyor - Premium Buy Point Portföyü sayfası,
                    # toplam bütçenin DAHA ÖNCE yüzdesi belirlenmiş hisselere ayrılan
                    # kısmını düşüp kalanı aktarılan hisse sayısına eşit bölerek kendi
                    # hesaplıyor (bkz. premium_buy_portfolio.py _default_weight_pct).
                    st.session_state["premium_buy_pending_transfer"] = [r["symbol"] for r in selected_backtest_rows]
                    st.session_state["premium_buy_pending_symbol_settings"] = {
                        r["symbol"]: {"algorithm": r["algorithm"], "timeframe": r["timeframe"]}
                        for r in selected_backtest_rows
                    }
                    st.session_state["active_module_🤖 Algoritmik Ticaret"] = "🎯 Premium Buy Point Portföyü"
                    st.session_state["nav_category"] = "🤖 Algoritmik Ticaret"
                    st.session_state["open_category"] = "🤖 Algoritmik Ticaret"
                    st.rerun()

    st.divider()
    st.subheader("🕐 Günlük Otomatik Çalıştırma")
    st.caption(
        "Etkinleştirilirse, yukarıdaki ayarlarla (nakit tutarı, evren, algoritma, mum periyodu) bu "
        "pipeline'ın tamamı (tara → RSI14/RSI21 + EMA50/EMA200 ile daralt → backtest → %10 üzeri "
        "kârlılık gösterenleri Premium Buy Point Portföyü'ne ekle) GitHub Actions üzerinden **günde 1 "
        "kez** otomatik çalışır - manuel buton tıklamaya gerek kalmaz. Gerçek alım/satım emirleri, bu "
        "portföyü zaten her 5 dakikada bir tarayan mevcut Alpaca botları tarafından yürütülür; bu "
        "modülün otomatik kısmı sadece o botların kullandığı portföyü günlük olarak günceller. Yukarıdaki "
        "manuel adım butonları her zaman kullanılabilir kalır."
    )
    automated = st.checkbox(
        "Bu süreci otomatik çalıştır (günde 1 kez)", value=bool(config.get("enabled")), key="oas_enabled",
    )
    if config.get("last_run_at"):
        summary = config.get("last_run_summary") or {}
        st.caption(
            f"Son otomatik koşu: {config['last_run_at']} — evren {summary.get('universe_size', '—')}, "
            f"sinyal {summary.get('scan_signal_count', '—')}, aday {summary.get('candidate_count', '—')}, "
            f"seçilen: {', '.join(summary.get('selected_symbols') or []) or 'yok'}."
        )

    if st.button("💾 Otomatik Alım/Satım Ayarlarını Kaydet", type="primary"):
        new_config = dict(config)
        new_config.update({
            "enabled": automated,
            "cash_allocation": float(cash_allocation),
            "include_nasdaq": include_nasdaq,
            "include_nyse": include_nyse,
            "custom_groups": custom_groups,
            "algorithms": selected_algo_ids,
            "timeframes": selected_timeframes,
            "min_backtest_profit_pct": DEFAULT_MIN_BACKTEST_PROFIT_PCT,
            "max_candidates": DEFAULT_MAX_CANDIDATES,
            "momentum_lookback_days": int(st.session_state.get("oas_momentum_lookback_days", DEFAULT_MOMENTUM_LOOKBACK_DAYS)),
        })
        _save_config(GITHUB_REPO, github_token, new_config, username)
        st.success("Ayarlar kaydedildi.")
        st.rerun()
