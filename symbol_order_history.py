"""Belirli hisselerin Alpaca emir ve hesap hareketi geçmişini loglayan
salt-okunur teşhis script'i - HİÇBİR emir vermez/değiştirmez/iptal etmez,
hiçbir dosya yazmaz (bkz. .github/workflows/symbol_order_history.yml,
sadece elle tetiklenir).

Neden var: bir pozisyonun sistemin hiçbir iş akışı logunda görünmeyen bir
yolla kapanması (MRVL, 2026-09-30: stop hâlâ açıkken pozisyon kayboldu) ya
da bir stopun ortadan kalkması (MDB, 2026-09-28) gibi durumlarda, gerçekte
ne olduğunu yalnızca Alpaca'nın kendi kaydı gösterir. orb_stop_status.py
sadece ORB holdings'ine bakıyor; bu script herhangi bir sembol için:

  1. Güncel pozisyon (adet, ortalama giriş, güncel fiyat).
  2. Son `days` gündeki TÜM emirler (açık, dolan, iptal, replaced, expired,
     bracket bacakları dahil) - oluşturulma/dolum/iptal/son kullanma
     zamanları, tip, fiyatlar, client_order_id (hangi modülün verdiğini
     gösterir: "algo-", "hai-", stop etiketleri, ... elle verilen emirlerde
     Alpaca'nın rastgele UUID'si).
  3. Aynı dönemdeki hesap hareketleri (dolumlar + işlem dışı hareketler:
     ör. kurumsal işlem, zorla kapatma) - emir listesinde görünmeyen bir
     pozisyon değişikliği varsa burada çıkar.
  4. Pozisyonu olmayan sembolde açık emir kaldıysa uyarı.
"""

import argparse
from datetime import datetime, timedelta, timezone

from alpaca_client import AlpacaClient
from alpaca_account import build_job_client


def build_client() -> AlpacaClient:
    # Adres ve anahtarlar kullanıcının hesap türü ayarından (Sanal Para / Gerçek Para) -
    # bkz. alpaca_account.build_job_client.
    return build_job_client("berkakar")


def _flatten(orders: list[dict]) -> list[dict]:
    """nested=true ile gelen bracket/OTO bacaklarını ana emirlerle aynı listeye alır."""
    result = []
    for order in orders:
        result.append(order)
        for leg in order.get("legs") or []:
            result.append({**leg, "_parent": order["id"]})
    return result


def _fmt_order(o: dict) -> str:
    prices = []
    if o.get("limit_price"):
        prices.append(f"limit={o['limit_price']}")
    if o.get("stop_price"):
        prices.append(f"stop={o['stop_price']}")
    if o.get("filled_avg_price"):
        prices.append(f"dolum={o['filled_avg_price']}")
    times = [f"oluşturma={o.get('created_at')}"]
    for key, label in (("filled_at", "dolum"), ("canceled_at", "iptal"), ("expired_at", "süre sonu"),
                       ("replaced_at", "replaced"), ("failed_at", "başarısız")):
        if o.get(key):
            times.append(f"{label}={o[key]}")
    extra = []
    if o.get("replaced_by"):
        extra.append(f"replaced_by={o['replaced_by']}")
    if o.get("replaces"):
        extra.append(f"replaces={o['replaces']}")
    if o.get("_parent"):
        extra.append(f"bacak(ana={o['_parent']})")
    return (
        f"  {o.get('side'):4} {o.get('type'):10} {o.get('order_class') or '-':7} tif={o.get('time_in_force')} "
        f"ext={o.get('extended_hours')} adet={o.get('qty')} dolan={o.get('filled_qty')} "
        f"durum={o.get('status'):16} {' '.join(prices)}\n"
        f"      {' | '.join(times)}\n"
        f"      id={o.get('id')} client_order_id={o.get('client_order_id')} {' '.join(extra)}"
    )


def report(client: AlpacaClient, symbol: str, days: int) -> None:
    after = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    print(f"\n==================== {symbol} (son {days} gün) ====================")

    position = client.get_position(symbol)
    if position is None:
        print("Pozisyon: YOK")
    else:
        print(f"Pozisyon: adet={position['qty']} ort. giriş={position['avg_entry_price']} "
              f"güncel={position.get('current_price')} K/Z={position.get('unrealized_pl')}")

    r = client._get("/orders", params={
        "status": "all", "symbols": symbol, "after": after, "direction": "asc", "limit": 500, "nested": "true",
    })
    r.raise_for_status()
    orders = _flatten(r.json())
    orders.sort(key=lambda o: o.get("created_at") or "")
    print(f"\nEmirler ({len(orders)}):")
    for o in orders:
        print(_fmt_order(o))

    open_orders = [o for o in orders if o.get("status") in ("new", "accepted", "held", "pending_new",
                                                             "partially_filled", "accepted_for_bidding")]
    if position is None and open_orders:
        print(f"\nPozisyon yok ama {len(open_orders)} açık emir var (sahipsiz): "
              + ", ".join(f"{o['side']} {o['type']} {o.get('stop_price') or o.get('limit_price')} ({o['id']})"
                          for o in open_orders))

    r = client._get("/account/activities", params={"after": after, "direction": "asc", "page_size": 100})
    if not r.ok:
        print(f"\nHesap hareketleri alınamadı: {r.status_code} {r.text[:200]}")
        return
    activities = [a for a in r.json() if a.get("symbol") == symbol]
    print(f"\nHesap hareketleri ({len(activities)}):")
    for a in activities:
        when = a.get("transaction_time") or a.get("date")
        print(f"  {when} tip={a.get('activity_type')} {a.get('side', '')} adet={a.get('qty')} "
              f"fiyat={a.get('price')} emir={a.get('order_id', '-')} {a.get('description', '')}".rstrip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="MRVL,MDB", help="Virgülle ayrılmış semboller.")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    client = build_client()
    for symbol in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        try:
            report(client, symbol, args.days)
        except Exception as e:
            print(f"{symbol}: rapor alınamadı: {e}")


if __name__ == "__main__":
    main()
