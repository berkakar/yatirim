"""Geçici doğrulama - SON adım: gerçek uç nokta bulundu:
GET /tr/api/disclosure/filter/FILTERYFBF/<fundOid>/<disclosureTypeOid>/<gün>
Bu turda düz requests ile (tarayıcısız) çağırıp:
  1) JSON şeklini (disclosureIndex/disclosureId alan adları) doğruluyoruz,
  2) "Portföy Dağılım Raporu" tip OID'inin (8aca490d502e34b801502e380044002b)
     BAŞKA bir fon (KYA) için de aynı/evrensel olup olmadığını kontrol
     ediyoruz - eğer öyleyse, sabit bir sabit olarak kullanılabilir.
"""
import json

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
FILTER_URL = "https://www.kap.org.tr/tr/api/disclosure/filter/FILTERYFBF/{fund_oid}/{type_oid}/{days}"
PDR_TYPE_OID = "8aca490d502e34b801502e380044002b"

FUNDS = [
    ("TPR", "33E5FED7ECE300EAE0530A4A622B2AEA"),
    ("KYA", "33E5FED7E5D300EAE0530A4A622B2AEA"),
]

for code, fund_oid in FUNDS:
    url = FILTER_URL.format(fund_oid=fund_oid, type_oid=PDR_TYPE_OID, days=365)
    r = requests.get(url, headers=HEADERS, timeout=20)
    print(f"\n### {code}: {url} ###")
    print(f"status={r.status_code}")
    if not r.ok:
        print(r.text[:500])
        continue
    data = r.json()
    print(f"Response type: {type(data)}")
    if isinstance(data, dict):
        print(f"Top-level keys: {list(data.keys())}")
        items = data.get("data") or data.get("items") or data.get("results") or data.get("list")
    else:
        items = data
    print(f"items type: {type(items)}, count: {len(items) if hasattr(items, '__len__') else '?'}")
    if items:
        print("First 3 raw items:")
        for it in items[:3]:
            print(f"  {json.dumps(it, ensure_ascii=False)}")
    else:
        print(f"Full raw response (first 2000 chars): {json.dumps(data, ensure_ascii=False)[:2000]}")
