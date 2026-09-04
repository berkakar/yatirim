"""Hata ayıklama turu 2 - önceki tur, sayfa uzunluğunun ÖNCEKİ başarılı
denemeyle birebir aynı (301267) olduğunu ama regex'in artık eşleşmediğini
gösterdi. Bu, sayfa içeriğinin GERÇEKTEN değiştiğini (ör. bot/tekrar
isteği tespit edilip daha sade bir sürüm servis edilmesi) düşündürüyor.
Bu turda: "batch-news" alt dizesinin sayfada hiç geçip geçmediğini,
toplam kaç tane 32-hex OID bulunduğunu ve iki farklı User-Agent ile
(hiç UA vermeden ve tarayıcı benzeri tam bir UA ile) sonucun değişip
değişmediğini kontrol ediyoruz.
"""
import re

import requests

from kap_client import FUND_PAGE_URL, slugify

FULL_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

CASES = [
    ("TPR", "İŞ PORTFÖY PY HİSSE SENEDİ (TL) ÖZEL FONU (HİSSE SENEDİ YOĞUN FON)"),
    ("KYA", "KARE PORTFÖY HİSSE SENEDİ FONU (HİSSE SENEDİ YOĞUN FON)"),
]

for code, name in CASES:
    slug = slugify(code, name)
    url = FUND_PAGE_URL.format(slug=slug)
    print(f"\n{'=' * 60}\n{code}: {url}")

    for label, headers in [
        ("no UA", {}),
        ("simple UA", {"User-Agent": "Mozilla/5.0 (compatible; yatirim-app/1.0)"}),
        ("full browser UA", {"User-Agent": FULL_UA, "Accept-Language": "tr-TR,tr;q=0.9"}),
    ]:
        r = requests.get(url, headers=headers, timeout=20)
        has_batch_news = "batch-news" in r.text
        oids = re.findall(r"[0-9A-Fa-f]{32}", r.text)
        m = re.search(r"/tr/api/batch-news/file-by-year/([0-9A-Fa-f]{32})/", r.text)
        print(f"  [{label}] status={r.status_code} len={len(r.text)} "
              f"has_batch_news_substr={has_batch_news} total_hex32={len(oids)} "
              f"regex_match={m.group(1) if m else None}")
