"""Geçici tanı scripti - TEFAS'tan verilen fon kodları için ham fiyat
geçmişini TAZE olarak çekip yazdırır, hiçbir şeyi önbelleğe kaydetmez.
Amaç: tefas_fonlari_cache.json'daki bir tarihe ait fiyatın, TEFAS'ın o
tarih için şu an döndürdüğü değerden farklı olup olmadığını (yani TEFAS'ın
yayınladıktan sonra fiyatı revize edip etmediğini) kontrol etmek.
"""
from tefas_client import _fetch_fund_rows

for code in ("THF", "DOH"):
    print(f"=== {code} (taze TEFAS çekimi) ===")
    rows = _fetch_fund_rows(code, lookback_days=10)
    for r in rows:
        print(f"  {r.get('date')}  price={r.get('price')}  shares={r.get('shares_outstanding')}")
