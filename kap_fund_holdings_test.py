"""Geçici doğrulama - fon-bildirimleri sayfasının ham HTML'inde
/tr/Bildirim/<id> linkleri var mı (varsa, private API tahmin etmeye hiç
gerek kalmadan doğrudan bu sayfadan fonun bildirim listesi kazınabilir).
Ayrıca fon-bilgileri/ozet sayfasının TAM içeriğinde "Portföy Dağılım
Raporu" ifadesi geçen bir tablo/bölüm var mı kontrol ediliyor.
"""
import re

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}

PAGES = {
    "TPR bildirimleri (tam slug)": "https://www.kap.org.tr/tr/fon-bildirimleri/tpr-is-portfoy-py-hisse-senedi-tl-ozel-fonu-hisse-senedi-yogun-fon",
    "TPR ozet (tam slug)": "https://www.kap.org.tr/tr/fon-bilgileri/ozet/tpr-is-portfoy-py-hisse-senedi-tl-ozel-fonu-hisse-senedi-yogun-fon",
    "TPR genel (tam slug)": "https://www.kap.org.tr/tr/fon-bilgileri/genel/tpr-is-portfoy-py-hisse-senedi-tl-ozel-fonu-hisse-senedi-yogun-fon",
}


def strip_tags(html: str) -> str:
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"\s+", " ", html)
    return html


for label, url in PAGES.items():
    print(f"\n### {label}: {url} ###")
    r = requests.get(url, headers=HEADERS, timeout=20)
    print(f"status={r.status_code}, len={len(r.text)}")
    if not r.ok:
        continue

    bildirim_links = sorted(set(re.findall(r'/tr/Bildirim/(\d+)', r.text)))
    print(f"/tr/Bildirim/<id> links found: {len(bildirim_links)} -> {bildirim_links[:30]}")

    text = strip_tags(r.text)
    idxs = [m.start() for m in re.finditer(r"[Pp]ortf[öo]y [Dd]a[ğg][ıi]l[ıi]m", text)]
    print(f"'portföy dağılım' occurrences in stripped text: {len(idxs)}")
    for i in idxs[:10]:
        print(f"  ...{text[max(0, i-150):i+250]}...")

    # Also check for date-like patterns near bildirim links (context for the
    # first few links, from raw HTML around each match).
    for bid in bildirim_links[:5]:
        pos = r.text.find(f"/tr/Bildirim/{bid}")
        snippet = strip_tags(r.text[max(0, pos - 300):pos + 300])
        print(f"  context for Bildirim/{bid}: ...{snippet}...")
