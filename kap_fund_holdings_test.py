"""Geçici son doğrulama - tam çözüm zinciri artık biliniyor:
  1) fon kodu -> fundOid (bu adımı doğruluyoruz: bare-code sayfası
     düz HTTP isteğiyle fundOid içeriyor mu?)
  2) GET /tr/api/disclosure/filter/FILTERYFBF/<fundOid>/<PDR type OID>/365
     -> "Portföy Dağılım Raporu" bildirim listesi (en yeniden eskiye)
  3) en yeni disclosureIndex -> attachment-detail -> file/download
     (Java-wrapped) -> pdfplumber ile parse (zaten kanıtlanmıştı)
Bu script sadece adım 1'i (bare-code sayfasından fundOid çıkarımı) hem
ozet hem genel sayfası için, hem TPR hem KYA için test ediyor.
"""
import re

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}

# (kod, bilinen doğru fundOid)
FUNDS = [
    ("TPR", "33E5FED7ECE300EAE0530A4A622B2AEA"),
    ("KYA", "33E5FED7E5D300EAE0530A4A622B2AEA"),
]

for code, known_oid in FUNDS:
    for page_type in ["ozet", "genel"]:
        url = f"https://www.kap.org.tr/tr/fon-bilgileri/{page_type}/{code}"
        r = requests.get(url, headers=HEADERS, timeout=20)
        found = known_oid in r.text
        all_oids = set(re.findall(r'[0-9A-Fa-f]{32}', r.text))
        print(f"{code} {page_type} (bare code): status={r.status_code}, len={len(r.text)}, "
              f"known_oid_present={found}, total_hex_oids_on_page={len(all_oids)}")
        if found:
            # Show a snippet of context around the OID to see how it's embedded.
            idx = r.text.find(known_oid)
            print(f"  context: ...{r.text[max(0,idx-150):idx+50]}...")
