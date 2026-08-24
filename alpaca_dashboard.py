from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from alpaca_client import AlpacaClient

TR_TZ = ZoneInfo("Europe/Istanbul")

STATUS_TR = {
    "new": "Aktif (Bekliyor)",
    "held": "Aktif (Bekliyor)",
    "accepted": "Aktif (Bekliyor)",
    "replaced": "Trail Edildi",
    "filled": "Tetiklendi",
    "canceled": "İptal Edildi",
    "expired": "Süresi Doldu",
    "rejected": "Reddedildi",
}


def _to_tr_time(iso_ts: str) -> datetime:
    return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).astimezone(TR_TZ)


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
    st.caption("Stoplar, structure-based trailing-stop GitHub Action tarafından yarım saatte bir güncellenir.")

    st.subheader("📜 Trailing Stop İşlem Geçmişi")

    history_rows = []
    for pos in positions:
        symbol = pos["symbol"]
        for order in client.get_stop_order_history(symbol):
            created = _to_tr_time(order["created_at"])
            history_rows.append({
                "_sort_ts": created,
                "Tarih (TRT)": created.strftime("%d.%m.%Y %H:%M:%S"),
                "Hisse": symbol,
                "Yön": "Satış" if order["side"] == "sell" else "Alış",
                "Stop Fiyatı": round(float(order["stop_price"]), 2),
                "Adet": float(order["qty"]),
                "Durum": STATUS_TR.get(order["status"], order["status"]),
            })

    if not history_rows:
        st.info("Henüz trailing-stop işlem geçmişi yok.")
        return

    history_df = (
        pd.DataFrame(history_rows)
        .sort_values("_sort_ts", ascending=False)
        .drop(columns=["_sort_ts"])
    )
    st.dataframe(history_df, use_container_width=True, hide_index=True)
    st.caption("Sütun başlıklarına tıklayarak sıralayabilirsiniz. Varsayılan sıralama: en yeni işlem en üstte.")
