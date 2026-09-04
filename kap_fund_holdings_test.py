"""Geçici doğrulama - bulunan iki kritik uç nokta doğrulanıyor:
  1) kap.org.tr/tr/fon-bilgileri/ozet/<KOD> (sadece kod, slug yok) yönlendirme
     yapıyor mu - yapıyorsa slug tahmin etmeye hiç gerek kalmaz.
  2) /tr/api/batch-news/file-by-year/<fundOid>/<yıl> gerçekten o fonun kendi
     bildirimlerini (Portföy Dağılım Raporu dahil) listeliyor mu - hem KYA
     hem TPR için (iki farklı fon/şirket) doğrulanıyor.
"""
import json

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}

# (kod, bilinen fundOid, bilinen rapor yılı)
FUNDS = [
    ("TPR", "33E5FED7ECE300EAE0530A4A622B2AEA", 2024),
    ("KYA", "33E5FED7E5D300EAE0530A4A622B2AEA", 2025),
]

print("### Bare-code redirect test ###")
for code, _, _ in FUNDS:
    for path in [f"https://www.kap.org.tr/tr/fon-bilgileri/ozet/{code}",
                 f"https://www.kap.org.tr/tr/fon-bilgileri/ozet/{code.lower()}"]:
        r = requests.get(path, headers=HEADERS, timeout=20, allow_redirects=True)
        print(f"{path} -> status={r.status_code}, final_url={r.url}, len={len(r.text)}")
        r2 = requests.get(path, headers=HEADERS, timeout=20, allow_redirects=False)
        print(f"  no-redirect: status={r2.status_code}, Location={r2.headers.get('Location')}")

print("\n### batch-news/file-by-year test ###")
for code, fund_oid, year in FUNDS:
    for y in (year, year + 1 if year < 2026 else year):
        url = f"https://www.kap.org.tr/tr/api/batch-news/file-by-year/{fund_oid}/{y}"
        r = requests.get(url, headers=HEADERS, timeout=20)
        print(f"\n{code} year={y}: status={r.status_code}, len={len(r.text)}")
        if r.ok:
            try:
                data = r.json()
            except Exception as e:
                print(f"  not JSON ({e}), first 500 chars: {r.text[:500]}")
                continue
            if isinstance(data, list):
                print(f"  {len(data)} items")
                for item in data[:5]:
                    print(f"    {json.dumps(item, ensure_ascii=False)[:400]}")
                # Look specifically for Portföy Dağılım Raporu.
                for item in data:
                    blob = json.dumps(item, ensure_ascii=False).lower()
                    if "portföy dağılım" in blob or "portfoy dagilim" in blob:
                        print(f"  MATCH: {json.dumps(item, ensure_ascii=False)}")
            else:
                print(f"  shape: {type(data)}, keys: {list(data.keys()) if isinstance(data, dict) else '?'}")
                print(f"  {json.dumps(data, ensure_ascii=False)[:1500]}")
