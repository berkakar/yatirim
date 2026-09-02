from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from alpaca_client import AlpacaClient
from alpaca_dashboard import format_order_row, TR_TZ
from alpaca_trailing_stop import get_regular_hours_bars, TIMEFRAME
from buy_algorithms import ALGORITHMS, DEFAULT_ALGORITHM, compute_all_signals, reject_if_marketable
from github_config import read_portfolio_config, write_portfolio_config
from ui_style import zebra_style

GITHUB_REPO = "berkakar/yatirim"
BUY_LOOKBACK_DAYS = 60
DAILY_LOOKBACK_DAYS = 400  # SMA(200) icin yeterli gunluk bar (hafta sonu/tatil payi ile)
PRICE_REFRESH_SECONDS = 30


@st.fragment(run_every=PRICE_REFRESH_SECONDS)
def _render_buy_point_table(client: AlpacaClient, current_symbols: list[str], active_algorithm: str):
    start = datetime.now(timezone.utc) - timedelta(days=BUY_LOOKBACK_DAYS)
    daily_start = datetime.now(timezone.utc) - timedelta(days=DAILY_LOOKBACK_DAYS)
    rows = []
    for symbol in current_symbols:
        bars = get_regular_hours_bars(client, symbol, TIMEFRAME, start, exclude_forming=True)
        if not bars:
            continue
        try:
            live_price = client.get_latest_trade_price(symbol)
        except Exception:
            live_price = None
        current_price = live_price if live_price is not None else bars[-1].c

        try:
            daily_closes = [b["c"] for b in client.get_raw_bars(symbol, "1Day", daily_start.isoformat())]
        except Exception:
            daily_closes = []

        signals = compute_all_signals(bars, daily_closes)
        signals = {algo_id: reject_if_marketable(sig, current_price) for algo_id, sig in signals.items()}
        active_signal = signals.get(active_algorithm)
        has_position = client.get_position(symbol) is not None

        row = {"Hisse": symbol, "Güncel Fiyat": round(current_price, 2)}
        for algo_id, (label, _) in ALGORITHMS.items():
            sig = signals.get(algo_id)
            row[label] = sig.price if sig else "—"
        row["Kullanılacak Fiyat"] = active_signal.price if active_signal else "—"
        row["Durum"] = "Pozisyon Açık" if has_position else ("Bekleniyor" if active_signal else "Sinyal Yok")
        rows.append(row)

    st.dataframe(zebra_style(pd.DataFrame(rows)), use_container_width=True, hide_index=True)
    st.caption(
        f"Son güncelleme: {datetime.now(TR_TZ).strftime('%H:%M:%S')} TRT "
        f"({PRICE_REFRESH_SECONDS} saniyede bir otomatik yenilenir). "
        f"Her sütun ilgili algoritmanın ürettiği fiyatı gösterir (\"—\" = sinyal yok). "
        f"'Kullanılacak Fiyat', aşağıda seçtiğiniz algoritmanın ({ALGORITHMS[active_algorithm][0]}) sonucudur - "
        "gerçek alım GitHub Action tarafından 5 dakikalık taramada bu fiyat/algoritma ile yapılır."
    )


def render_premium_buy_portfolio(target_list: list[str], username: str):
    user_alpaca = st.secrets.get("alpaca", {}).get(username, {})
    key_id = user_alpaca.get("key_id")
    secret_key = user_alpaca.get("secret_key")
    github_token = st.secrets.get("GITHUB_TOKEN")

    if not key_id or not secret_key:
        st.warning(f"'{username}' için Alpaca hesabı tanımlı değil (`.streamlit/secrets.toml` içinde `[alpaca.{username}]`).")
        return
    if not github_token:
        st.warning("`.streamlit/secrets.toml` içinde GITHUB_TOKEN tanımlı değil - portföy ayarları kaydedilemez.")
        return

    watchlist_name = f"premium-buy-portfolio-{username}"
    client = AlpacaClient(key_id, secret_key)
    watchlist = client.get_or_create_watchlist(watchlist_name)
    current_symbols = [a["symbol"] for a in watchlist.get("assets", [])]
    config = read_portfolio_config(GITHUB_REPO, github_token, username)

    st.subheader("🎯 Portföy Seçimi")
    st.caption("Bu listedeki hisseler için premium buy point (demand zone) taranır ve fiyat oraya ulaştığında otomatik alım yapılır.")

    picker_df = pd.DataFrame({"Hisse": target_list})
    picker_df["Seçili"] = picker_df["Hisse"].isin(current_symbols)
    edited_picker = st.data_editor(
        picker_df,
        column_config={"Seçili": st.column_config.CheckboxColumn(required=True)},
        hide_index=True,
        use_container_width=True,
        key="premium_buy_symbol_picker",
    )
    selected_symbols = edited_picker[edited_picker["Seçili"]]["Hisse"].tolist()

    st.subheader("💰 Bütçe ve Hisse Ağırlıkları")
    budget = st.number_input(
        "Toplam portföy bütçesi ($)", min_value=0.0, value=float(config.get("budget") or 0), step=100.0,
    )

    edited_weights = pd.DataFrame(columns=["Hisse", "Ağırlık %"])
    if selected_symbols:
        existing_weights = config.get("weights") or {}
        equal_share = round(100 / len(selected_symbols), 2)
        weight_df = pd.DataFrame({
            "Hisse": selected_symbols,
            "Ağırlık %": [float(existing_weights.get(s, equal_share)) for s in selected_symbols],
        })
        edited_weights = st.data_editor(
            weight_df,
            hide_index=True,
            use_container_width=True,
            key="premium_buy_weight_editor",
            column_config={"Ağırlık %": st.column_config.NumberColumn(min_value=0.0, max_value=100.0, step=1.0)},
        )
        total_weight = edited_weights["Ağırlık %"].sum()
        if abs(total_weight - 100) < 0.01:
            st.caption(f"Toplam ağırlık: %{total_weight:.1f}")
        else:
            st.warning(f"Toplam ağırlık: %{total_weight:.1f} — %100 olması önerilir, aksi halde bütçe tam kullanılmaz veya aşılır.")
    else:
        st.info("Portföye en az bir hisse seçin.")

    st.subheader("🧠 Emir Algoritması")
    st.caption("Aşağıdaki tabloda tüm algoritmaların sonucu karşılaştırmalı gösterilir; burada seçtiğiniz ise "
               "**tüm hisseler için ortak olarak** Alpaca'ya gerçek emir olarak geçirilir.")
    algorithm_ids = list(ALGORITHMS.keys())
    current_algorithm = config.get("algorithm", DEFAULT_ALGORITHM)
    if current_algorithm not in algorithm_ids:
        current_algorithm = DEFAULT_ALGORITHM
    selected_algorithm = st.selectbox(
        "Emir için kullanılacak algoritma:",
        algorithm_ids,
        index=algorithm_ids.index(current_algorithm),
        format_func=lambda k: ALGORITHMS[k][0],
    )

    if st.button("💾 Portföyü Kaydet", type="primary"):
        client.set_watchlist_symbols(watchlist["id"], selected_symbols)
        new_config = {
            "budget": float(budget),
            "weights": {row["Hisse"]: float(row["Ağırlık %"]) for _, row in edited_weights.iterrows()},
            "algorithm": selected_algorithm,
        }
        write_portfolio_config(GITHUB_REPO, github_token, new_config, username)
        st.success("Portföy kaydedildi.")
        st.rerun()

    if not current_symbols:
        return

    st.subheader("📍 Premium Buy Point Karşılaştırması")
    _render_buy_point_table(client, current_symbols, selected_algorithm)

    st.subheader("📜 Son 30 Gün Alım/Satım Emirleri")
    history_rows = [
        format_order_row(o) for o in client.get_recent_orders(days=30) if o["symbol"] in current_symbols
    ]
    if not history_rows:
        st.info("Son 30 günde bu portföy için işlem yok.")
        return

    history_df = (
        pd.DataFrame(history_rows)
        .sort_values("_sort_ts", ascending=False)
        .drop(columns=["_sort_ts"])
    )
    st.dataframe(zebra_style(history_df), use_container_width=True, hide_index=True)
