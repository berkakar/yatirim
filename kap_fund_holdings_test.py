"""Geçici manuel doğrulama scripti - bilinen gerçek "Portföy Dağılım Raporu"
bildirim ID'lerini (WebSearch ile bulunan) doğrudan sorgulayıp KAP'ın gerçek
veri şeklini keşfetmek için. Bu depoda kalıcı bir özellik değil; doğrulama
sonrası kap_test.yml ile birlikte kaldırılacak.
"""
import glob
import json
import struct

import pdfplumber
import requests

DETAIL_URL = "https://www.kap.org.tr/tr/api/notification/attachment-detail/{idx}"
FILE_DOWNLOAD_URL = "https://www.kap.org.tr/tr/api/file/download/{obj_id}"
BILDIRIM_PDF_URL = "https://www.kap.org.tr/en/api/BildirimPdf/{idx}"

# WebSearch ile bulunan gerçek örnekler:
#  - kap.org.tr/tr/Bildirim/1473544  "kya fon portföy dağılım raporu temmuz 2025"
#  - kap.org.tr/en/api/BildirimPdf/1247775  "Portföy Dağılım Raporu İŞ PORTFÖY PY HİSSE SENEDİ (TL) ÖZEL FONU"
KNOWN_IDS = [1473544, 1247775]


def unwrap_java_bytes(raw: bytes) -> bytes | None:
    if raw[:4] != b"\xac\xed\x00\x05":
        return None
    try:
        i = raw.index(b"\x78\x70", 10)
        arr_len = struct.unpack(">I", raw[i + 2:i + 6])[0]
        return raw[i + 6:i + 6 + arr_len]
    except Exception as e:
        print(f"Java unwrap failed: {e}")
        return None


for idx in KNOWN_IDS:
    print(f"\n{'=' * 60}\nDETAIL for disclosureIndex={idx}\n{'=' * 60}")
    r = requests.get(DETAIL_URL.format(idx=idx), timeout=20)
    print(f"status: {r.status_code}")
    if not r.ok:
        print(r.text[:500])
        continue

    detail = r.json()
    print(json.dumps(detail, ensure_ascii=False)[:4000])

    if not (isinstance(detail, list) and detail):
        continue
    entry = detail[0]

    body = entry.get("disclosureBody")
    if body:
        joined = " ".join(body) if isinstance(body, list) else str(body)
        print(f"\ndisclosureBody length: {len(joined)} chars, first 2000:")
        print(joined[:2000])

    attachments = entry.get("attachments") or []
    print(f"\nattachments: {attachments}")
    for att in attachments:
        obj_id = att.get("objId")
        if not obj_id:
            continue
        file_r = requests.get(FILE_DOWNLOAD_URL.format(obj_id=obj_id), timeout=30)
        print(f"file/download status for {obj_id}: {file_r.status_code}, {len(file_r.content)} bytes, "
              f"starts with: {file_r.content[:20]!r}")
        raw = file_r.content
        pdf_bytes = unwrap_java_bytes(raw) if raw[:4] != b"%PDF" else raw
        if pdf_bytes:
            print(f"PDF bytes: {len(pdf_bytes)}, starts with {pdf_bytes[:10]!r}")
            path = f"/tmp/kap_test_{idx}_{obj_id}.pdf"
            with open(path, "wb") as f:
                f.write(pdf_bytes)
            print(f"Saved to {path}")

    pdf_r = requests.get(BILDIRIM_PDF_URL.format(idx=idx), timeout=30)
    print(f"\nBildirimPdf status: {pdf_r.status_code}, {len(pdf_r.content)} bytes, "
          f"starts with {pdf_r.content[:10]!r}")
    if pdf_r.ok and pdf_r.content[:4] == b"%PDF":
        path = f"/tmp/kap_test_bildirimpdf_{idx}.pdf"
        with open(path, "wb") as f:
            f.write(pdf_r.content)
        print(f"Saved to {path}")

print(f"\n{'=' * 60}\nInspecting all saved PDFs\n{'=' * 60}")
for path in sorted(glob.glob("/tmp/kap_test_*.pdf")):
    print(f"\n--- {path} ---")
    try:
        with pdfplumber.open(path) as pdf:
            print(f"pages: {len(pdf.pages)}")
            for pi, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                print(f"page {pi} text (first 1200 chars):")
                print(text[:1200])
                tables = page.extract_tables() or []
                print(f"page {pi}: {len(tables)} table(s)")
                for ti, table in enumerate(tables):
                    print(f"table {ti} ({len(table)} rows):")
                    for row in table[:15]:
                        print(row)
    except Exception as e:
        print(f"pdfplumber failed: {e}")
