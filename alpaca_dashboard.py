from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from alpaca_client import AlpacaClient
from ui_style import zebra_style

TR_TZ = ZoneInfo("Europe/Istanbul")
HISTORY_DAYS = 30

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

TYPE_TR = {
    "market": "Piyasa Emri",
    "stop": "Stop",
    "stop_limit": "Stop-Limit",
    "limit": "Limit",
}


def _to_tr_time(iso_ts: str) -> datetime:
    return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).astimezone(TR_TZ)


def _order_price(order: dict) -> float | None:
    for field in ("stop_price", "filled_avg_price", "limit_price"):
        if order.get(field):
            return float(order[field])
    return None


def format_order_row(order: dict) -> dict:
    created = _to_tr_time(order["created_at"])
    price = _order_price(order)

    if order.get("qty"):
        amount = f"{float(order['qty']):g} adet"
    elif order.get("notional"):
        amount = f"${float(order['notional']):.2f}"
    else:
        amount = f"{float(order.get('filled_qty') or 0):g} adet"

    return {
        "_sort_ts": created,
        "Tarih (TRT)": created.strftime("%d.%m.%Y %H:%M:%S"),
        "Hisse": order["symbol"],
        "Tip": TYPE_TR.get(order["type"], order["type"]),
        "Yön": "Satış" if order["side"] == "sell" else "Alış",
        "Fiyat": round(price, 2) if price is not None else "—",
        "Adet/Tutar": amount,
        "Durum": STATUS_TR.get(order["status"], order["status"]),
    }


def render_alpaca_dashboard(username):
    user_alpaca = st.secrets.get("alpaca", {}).get(username, {})
    key_id = user_alpaca.get("key_id")
    secret_key = user_alpaca.get("secret_key")
    if not key_id or not secret_key:
        st.warning(f"'{username}' için Alpaca hesabı tanımlı değil (`.streamlit/secrets.toml` içinde `[alpaca.{username}]`).")
        return

    client = AlpacaClient(key_id, secret_key)
    positions = client.get_all_positions()

    if not positions:
        st.info("Açık pozisyon yok.")
    else:
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

        st.dataframe(zebra_style(pd.DataFrame(rows)), use_container_width=True, hide_index=True)
        st.caption("Stoplar, structure-based trailing-stop GitHub Action tarafından yarım saatte bir güncellenir.")

    st.subheader(f"📜 Son {HISTORY_DAYS} Gün İşlem Geçmişi")

    history_rows = [format_order_row(o) for o in client.get_recent_orders(days=HISTORY_DAYS)]

    if not history_rows:
        st.info(f"Son {HISTORY_DAYS} günde işlem yok.")
        return

    history_df = (
        pd.DataFrame(history_rows)
        .sort_values("_sort_ts", ascending=False)
        .drop(columns=["_sort_ts"])
    )
    st.dataframe(zebra_style(history_df), use_container_width=True, hide_index=True)
    st.caption("Sütun başlıklarına tıklayarak sıralayabilirsiniz. Varsayılan sıralama: en yeni işlem en üstte. Kapanmış pozisyonlar da dahildir.")
