import pandas as pd
import streamlit as st

from alpaca_client import AlpacaClient


def render_alpaca_dashboard():
    key_id = st.secrets.get("APCA_API_KEY_ID")
    secret_key = st.secrets.get("APCA_API_SECRET_KEY")
    if not key_id or not secret_key:
        st.warning("`.streamlit/secrets.toml` içinde APCA_API_KEY_ID / APCA_API_SECRET_KEY tanımlı değil.")
        return

    client = AlpacaClient(key_id, secret_key)
    positions = client.get_all_positions()

    if not positions:
        st.info("Açık pozisyon yok.")
        return

    rows = []
    for pos in positions:
        symbol = pos["symbol"]
        stop_order = client.get_open_stop_order(symbol)
        entry = float(pos["avg_entry_price"])
        current = float(pos["current_price"])
        stop_price = float(stop_order["stop_price"]) if stop_order else None

        rows.append({
            "Hisse": symbol,
            "Yön": "Long" if float(pos["qty"]) > 0 else "Short",
            "Adet": abs(float(pos["qty"])),
            "Ortalama Giriş": round(entry, 2),
            "Güncel Fiyat": round(current, 2),
            "Kâr/Zarar %": round(float(pos["unrealized_plpc"]) * 100, 2),
            "Stop Fiyatı": round(stop_price, 2) if stop_price is not None else "—",
            "Stoptan Uzaklık %": round((current - stop_price) / current * 100, 2) if stop_price is not None else "—",
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption("Stoplar, structure-based trailing-stop GitHub Action tarafından saatlik olarak güncellenir.")
