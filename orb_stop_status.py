"""ORB pozisyonlarının GÜNCEL stop-loss seviyelerini Alpaca'dan okuyup
loglayan salt-okunur teşhis script'i. HİÇBİR emir vermez/değiştirmez -
sadece raporlar (bkz. alpaca_trailing_stop.py'nin manage_position'ı -
GERÇEK stop yönetimi hâlâ SADECE o dosyada, 5 dakikada bir çalışıyor)."""

import json
import os

from alpaca_client import AlpacaClient, DEFAULT_TRADING_URL, DEFAULT_DATA_URL
from orb_core import holdings_path


def build_client() -> AlpacaClient:
    key_id = os.environ["APCA_API_KEY_ID"]
    secret_key = os.environ["APCA_API_SECRET_KEY"]
    trading_url = os.environ.get("APCA_API_BASE_URL", DEFAULT_TRADING_URL)
    data_url = os.environ.get("APCA_API_DATA_URL", DEFAULT_DATA_URL)
    return AlpacaClient(key_id, secret_key, trading_url, data_url)


def report(username: str = "berkakar") -> None:
    with open(holdings_path(username), encoding="utf-8") as f:
        holdings = json.load(f)

    client = build_client()
    for symbol in holdings:
        position = client.get_position(symbol)
        if position is None:
            print(f"{symbol}: Alpaca'da canlı pozisyon yok.")
            continue
        stop_order = client.get_open_stop_order(symbol)
        entry = float(position["avg_entry_price"])
        current = float(position.get("current_price") or 0)
        qty = float(position["qty"])
        if stop_order:
            stop_price = float(stop_order["stop_price"])
            risk_pct = (entry - stop_price) / entry * 100 if entry else 0
            print(
                f"{symbol}: qty={qty:g} entry={entry:.2f} güncel={current:.2f} "
                f"stop={stop_price:.2f} (girişten -%{risk_pct:.2f}), emir id={stop_order['id']}"
            )
        else:
            print(f"{symbol}: qty={qty:g} entry={entry:.2f} güncel={current:.2f} STOP YOK (korumasız!)")


if __name__ == "__main__":
    report()
