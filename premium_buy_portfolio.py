from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from alpaca_client import AlpacaClient
from alpaca_dashboard import format_order_row, TR_TZ
from alpaca_trailing_stop import get_regular_hours_bars, TIMEFRAME
from demand_zones import find_buy_point
from github_config import read_portfolio_config, write_portfolio_config
from ui_style import zebra_style

GITHUB_REPO = "berkakar/yatirim"
BUY_LOOKBACK_DAYS = 60
PRICE_REFRESH_SECONDS = 30


@st.fragment(run_every=PRICE_REFRESH_SECONDS)
def _render_buy_point_table(client: AlpacaClient, current_symbols: list[str]):
    start = datetime.now(timezone.utc) - timedelta(days=BUY_LOOKBACK_DAYS)
    rows = []
    for symbol in current_symbols:
        bars = get_regular_hours_bars(client, symbol, TIMEFRAME, start)
        if not bars:
            continue
        try:
            live_price = client.get_latest_trade_price(symbol)
        except Exception:
            live_price = None
        current_price = live_price if live_price is not None else bars[-1].c

        zone = find_buy_point(bars)
        if zone is not None and zone.top >= current_price:
            zone = None  # not a pullback target if it's not below the current price
        has_position = client.get_position(symbol) is not None

        rows.append({
            "Hisse": symbol,
            "Güncel Fiyat": round(current_price, 2),
            "Buy Point": round(zone.top, 2) if zone else "—",
            "Mesafe %": round((current_price - zone.top) / current_price * 100, 2) if zone else "—",
            "Dokunuş Sayısı": zone.tap_count if zone else "—",
            "Durum": "Pozisyon Açık" if has_position else ("Bekleniyor" if zone else "Zone Yok"),
        })

    st.dataframe(zebra_style(pd.DataFrame(rows)), use_container_width=True, hide_index=True)
    st.caption(
        f"Son güncelleme: {datetime.now(TR_TZ).strftime('%H:%M:%S')} TRT "
        f"({PRICE_REFRESH_SECONDS} saniyede bir otomatik yenilenir). "
        "Mesafe %, güncel fiyatın buy point'in ne kadar üzerinde olduğunu gösterir. "
        "Fiyat buy point'e indiğinde (mesafe ≤ 0), gerçek alım GitHub Action tarafından yarım saatlik taramada yapılır."
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

    if st.button("💾 Portföyü Kaydet", type="primary"):
        client.set_watchlist_symbols(watchlist["id"], selected_symbols)
        new_config = {
            "budget": float(budget),
            "weights": {row["Hisse"]: float(row["Ağırlık %"]) for _, row in edited_weights.iterrows()},
        }
        write_portfolio_config(GITHUB_REPO, github_token, new_config, username)
        st.success("Portföy kaydedildi.")
        st.rerun()

    if not current_symbols:
        return

    st.subheader("📍 Premium Buy Point Mesafeleri")
    _render_buy_point_table(client, current_symbols)

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
