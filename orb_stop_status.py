"""ORB pozisyonlarının GÜNCEL stop-loss seviyelerini Alpaca'dan okuyup
loglayan salt-okunur teşhis script'i. HİÇBİR emir vermez/değiştirmez -
sadece raporlar (bkz. alpaca_trailing_stop.py'nin manage_position'ı -
GERÇEK stop yönetimi hâlâ SADECE o dosyada, 5 dakikada bir çalışıyor).

Ayrıca yerel holdings state'inde kayıtlı ama Alpaca'da artık canlı
pozisyonu OLMAYAN semboller için (bkz. TREX vakası, 2026-09-25) son
fill'leri de loglar - stop tetiklenip pozisyon kapandıysa gerçek
satış fiyatını/zamanını görmek için."""

import json

import storage
from alpaca_client import AlpacaClient
from alpaca_account import build_job_client
from orb_core import holdings_path


def build_client() -> AlpacaClient:
    # Adres ve anahtarlar kullanıcının hesap türü ayarından (Sanal Para / Gerçek Para) -
    # bkz. alpaca_account.build_job_client.
    return build_job_client("berkakar")


def report(username: str = "berkakar") -> None:
    holdings = storage.load_json(holdings_path(username), {})

    client = build_client()
    for symbol in holdings:
        position = client.get_position(symbol)
        if position is None:
            print(f"{symbol}: Alpaca'da canlı pozisyon yok. Son fill'ler:")
            fills = client.get_symbol_fills(symbol, days=1)
            for o in fills:
                print(
                    f"  - {o['side']} {o['filled_qty']} @ {o['filled_avg_price']} "
                    f"({o['type']}) filled_at={o.get('filled_at')} id={o['id']}"
                )
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
