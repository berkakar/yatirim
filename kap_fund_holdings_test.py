"""Hata ayıklama - resolve_fund_oid iki art arda çalıştırmada başarısız
oldu (önceki, neredeyse birebir aynı istekle başarılı olmuştu). Bu script
ham isteği (durum kodu, uzunluk, regex eşleşmesi) doğrudan yazdırıp
gerçek nedeni (WAF/oran sınırlama mı, kod hatası mı) ayırt ediyor.
"""
import re

import requests

from kap_client import HEADERS, FUND_PAGE_URL, slugify

CASES = [
    ("TPR", "İŞ PORTFÖY PY HİSSE SENEDİ (TL) ÖZEL FONU (HİSSE SENEDİ YOĞUN FON)"),
    ("KYA", "KARE PORTFÖY HİSSE SENEDİ FONU (HİSSE SENEDİ YOĞUN FON)"),
]

for code, name in CASES:
    slug = slugify(code, name)
    url = FUND_PAGE_URL.format(slug=slug)
    print(f"\n{code}: {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        print(f"  status={r.status_code}, len={len(r.text)}, headers_sent={HEADERS}")
        m = re.search(r"/tr/api/batch-news/file-by-year/([0-9A-Fa-f]{32})/", r.text)
        print(f"  regex match: {m.group(1) if m else None}")
        if not m:
            print(f"  first 500 chars: {r.text[:500]!r}")
            print(f"  response headers: {dict(r.headers)}")
    except Exception as e:
        print(f"  request failed: {e!r}")
