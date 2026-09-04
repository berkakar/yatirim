"""Geçici doğrulama - 'genel' sayfasının RSC payload'ında /tr/Bildirim/<id>
linkleri bulundu (gerçek ama sadece 2 tanesi - küçük bir 'son bildirimler'
widget'ı olabilir). Bu turda: (1) tüm /tr/api/... yollarını (query string
dahil) daha geniş bir regex ile arıyoruz - "bildirim"/"disclosure"/
"notification"/"duyuru" geçenleri özellikle işaretliyoruz, (2) sayfadaki
TÜM Bildirim linklerini (sadece 2 değil, gerçekten kaç tane varsa) ve
etraflarındaki başlık/tarih bağlamını çıkarıyoruz - belki fazlası var ama
ilk regex'te encoding/tekrar sorunuyla kaçmıştır.
"""
import re

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
URL = "https://www.kap.org.tr/tr/fon-bilgileri/genel/tpr-is-portfoy-py-hisse-senedi-tl-ozel-fonu-hisse-senedi-yogun-fon"

r = requests.get(URL, headers=HEADERS, timeout=20)
print(f"status={r.status_code}, len={len(r.text)}")
html = r.text

# Broad API path capture (incl. query strings), excluding quotes/backslashes.
api_paths = set(re.findall(r'/tr/api/[^"\'\\\s]+', html))
print(f"\nAll /tr/api/ paths found ({len(api_paths)}):")
for p in sorted(api_paths):
    print(f"  {p}")

keyword_hits = [p for p in api_paths if re.search(r'bildirim|disclosure|notif|duyuru', p, re.I)]
print(f"\nPaths matching bildirim/disclosure/notif/duyuru: {keyword_hits}")

# All /tr/Bildirim/<id> occurrences (dedup) with a title guess from nearby
# "children":"..." text (RSC payload pattern seen: ...children":"<title>"...href":"/tr/Bildirim/<id>"...)
all_ids = re.findall(r'/tr/Bildirim/(\d+)', html)
print(f"\nAll /tr/Bildirim/<id> occurrences (with dupes): {len(all_ids)} -> {sorted(set(all_ids))}")

for m in re.finditer(r'"children\\?":\\?"([^"\\]{3,150})\\?"[^}]*?"href\\?":\\?"/tr/Bildirim/(\d+)', html):
    print(f"  title guess: {m.group(1)!r} -> id={m.group(2)}")

# Reverse order too (href appears before children in some component orders).
for m in re.finditer(r'/tr/Bildirim/(\d+)\\?"[^{]*?"children\\?":\\?"([^"\\]{3,150})\\?"', html):
    print(f"  (reverse) id={m.group(1)} -> title guess: {m.group(2)!r}")

# Also check for a distinct "tümünü gör" / "see all" / pagination link pattern.
see_all = set(re.findall(r'"(/tr/[a-zA-Z0-9\-/]*[Bb]ildirim[a-zA-Z0-9\-/]*)"', html))
print(f"\n'Bildirim' page-link patterns (non-api): {see_all}")
