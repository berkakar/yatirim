"""Geçici doğrulama scripti - kullanıcının tarif ettiği KAP site akışını
(Ana Sayfa -> Fonlar -> Tüm Fonlar -> Fon Arama kutusu -> Bildirimler
sekmesi) esas alarak yeni uç nokta adayları deniyor. WebSearch ile
bulunan gerçek URL kalıpları:
  - kap.org.tr/tr/YatirimFonlari/ALL  (Tüm Fonlar listesi)
  - kap.org.tr/tr/fon-bilgileri/ozet/<kod>-<slug>
  - kap.org.tr/tr/fon-bilgileri/genel/<kod>-<slug>
  - kap.org.tr/tr/fon-bildirimleri/<kod>-<slug>   (muhtemelen "Bildirimler" sekmesi)
Bu turda: (1) YatirimFonlari/ALL sayfasının HTML/JS'inde arama API'sine dair
iz var mı, (2) bilinen bir fon için (TPR - İş Portföy PY Hisse Senedi) tahmini
slug'larla bu sayfalar gerçekten açılıyor mu ve içeriklerinde fundOid/bildirim
listesi gömülü mü.
"""
import re

import requests

CANDIDATE_PAGES = [
    "https://kap.org.tr/tr/YatirimFonlari/ALL",
    "https://kap.org.tr/tr/YatirimFonlari/YF",
]

# TPR = İş Portföy PY Hisse Senedi (TL) Özel Fonu (Hisse Senedi Yoğun Fon) - bilinen gerçek fon.
SLUG_GUESSES = [
    "tpr-is-portfoy-py-hisse-senedi-tl-ozel-fonu-hisse-senedi-yogun-fon",
    "tpr-is-portfoy-py-hisse-senedi-tl-ozel-fonu",
]
PAGE_TEMPLATES = [
    "https://www.kap.org.tr/tr/fon-bilgileri/ozet/{slug}",
    "https://www.kap.org.tr/tr/fon-bilgileri/genel/{slug}",
    "https://www.kap.org.tr/tr/fon-bildirimleri/{slug}",
]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}


def inspect(url: str):
    print(f"\n### {url} ###")
    try:
        r = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
    except Exception as e:
        print(f"error: {e}")
        return
    print(f"status={r.status_code}, final_url={r.url}, len={len(r.text)}")
    if not r.ok:
        return
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if m:
        print(f"__NEXT_DATA__ length={len(m.group(1))}, first 3000 chars:")
        print(m.group(1)[:3000])
    else:
        print("No __NEXT_DATA__ tag.")
    # Look for any oid-looking hex strings or api paths near "fon"/"bildirim"/"oid".
    oids = set(re.findall(r'[0-9a-fA-F]{32}', r.text))
    print(f"32-hex-char OID-looking strings found: {list(oids)[:10]} (total {len(oids)})")
    api_paths = set(re.findall(r'/tr/api/[a-zA-Z0-9/\-]+', r.text))
    print(f"api paths mentioned: {sorted(api_paths)[:30]}")
    title_match = re.search(r"<title>(.*?)</title>", r.text, re.S)
    print(f"<title>: {title_match.group(1) if title_match else None}")


for url in CANDIDATE_PAGES:
    inspect(url)

for slug in SLUG_GUESSES:
    for tmpl in PAGE_TEMPLATES:
        inspect(tmpl.format(slug=slug))
