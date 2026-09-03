"""Geçici manuel doğrulama scripti - KAP'ın gerçek uç noktalarını ve
"Portföy Dağılım Raporu" bildiriminin gerçek veri şeklini keşfetmek için.
Bu depoda kalıcı bir özellik değil; doğrulama sonrası kap_test.yml ile
birlikte kaldırılacak.
"""
import argparse
import glob
import json
import struct
from datetime import date, timedelta

import pdfplumber
import requests

parser = argparse.ArgumentParser()
parser.add_argument("--code", required=True, help="Fon kodu (ör. THF)")
parser.add_argument("--days", type=int, default=65)
args = parser.parse_args()

BY_CRITERIA_URL = "https://www.kap.org.tr/tr/api/disclosure/members/byCriteria"
DETAIL_URL = "https://www.kap.org.tr/tr/api/notification/attachment-detail/{idx}"
FILE_DOWNLOAD_URL = "https://www.kap.org.tr/tr/api/file/download/{obj_id}"
BILDIRIM_PDF_URL = "https://www.kap.org.tr/en/api/BildirimPdf/{idx}"


def _tr_lower(s):
    return (s or "").replace("İ", "i").replace("I", "ı").lower()


today = date.today()
start = today - timedelta(days=args.days)
print(f"Search window: {start} -> {today}")

all_matches = []
cursor = start
while cursor <= today:
    chunk_end = min(cursor + timedelta(days=6), today)
    payload = {
        "fromDate": cursor.strftime("%Y-%m-%d"),
        "toDate": chunk_end.strftime("%Y-%m-%d"),
        "mkkMemberOidList": [],
        "subjectList": [],
    }
    r = requests.post(BY_CRITERIA_URL, json=payload, timeout=20)
    print(f"chunk {cursor}..{chunk_end}: status={r.status_code} count={len(r.json()) if r.ok else 'N/A'}")
    if r.ok:
        for item in r.json():
            fund_code = item.get("fundCode")
            title_ish = f"{item.get('kapTitle', '')} {item.get('summary', '')} {item.get('subject', '')}"
            if (fund_code and fund_code.upper() == args.code.upper()) or args.code.upper() in _tr_lower(title_ish).upper():
                all_matches.append(item)
    cursor = chunk_end + timedelta(days=1)

print(f"\nTotal matches for fundCode/title containing '{args.code}': {len(all_matches)}")
for m in all_matches[:20]:
    print(json.dumps(m, ensure_ascii=False))

# Also: print unique "subject" values seen for records whose summary/kapTitle
# mentions "portföy dağılım" anywhere in the whole window (not just this fund) -
# helps discover the exact subject string used for this report type.
print("\nRe-scanning first chunk for any 'portföy dağılım' subject/summary values (sample)...")
r = requests.post(BY_CRITERIA_URL, json={
    "fromDate": start.strftime("%Y-%m-%d"), "toDate": today.strftime("%Y-%m-%d"),
    "mkkMemberOidList": [], "subjectList": [],
}, timeout=30)
if r.ok:
    seen_subjects = set()
    sample_pdr = []
    for item in r.json():
        blob = _tr_lower(f"{item.get('summary','')} {item.get('subject','')}")
        if "portföy dağılım" in blob or "portfoy dagilim" in blob:
            seen_subjects.add(item.get("subject"))
            if len(sample_pdr) < 5:
                sample_pdr.append(item)
    print(f"Distinct subject values for 'portföy dağılım' disclosures: {seen_subjects}")
    for s in sample_pdr:
        print(json.dumps(s, ensure_ascii=False))
else:
    print(f"Full-window request failed: {r.status_code} {r.text[:300]}")

if not all_matches:
    print("\nNo match found for this fund code in the window - stopping here.")
    raise SystemExit(0)

# Take the disclosure with the highest disclosureIndex as "latest".
best = max(all_matches, key=lambda m: m.get("disclosureIndex", 0))
idx = best["disclosureIndex"]
print(f"\nUsing disclosureIndex={idx} as latest match. Fetching detail...")

detail_r = requests.get(DETAIL_URL.format(idx=idx), timeout=20)
print(f"detail status: {detail_r.status_code}")
if detail_r.ok:
    detail = detail_r.json()
    print(json.dumps(detail, ensure_ascii=False)[:6000])

    if isinstance(detail, list) and detail:
        entry = detail[0]
        body = entry.get("disclosureBody")
        if body:
            joined = " ".join(body) if isinstance(body, list) else str(body)
            print(f"\ndisclosureBody length: {len(joined)} chars")
            print(joined[:3000])

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
            if raw[:4] == b"\xac\xed\x00\x05":
                try:
                    i = raw.index(b"\x78\x70", 10)
                    arr_len = struct.unpack(">I", raw[i + 2:i + 6])[0]
                    pdf_bytes = raw[i + 6:i + 6 + arr_len]
                    print(f"Unwrapped Java byte[]: {len(pdf_bytes)} bytes, starts with {pdf_bytes[:10]!r}")
                    with open(f"/tmp/kap_test_{obj_id}.pdf", "wb") as f:
                        f.write(pdf_bytes)
                    print(f"Saved to /tmp/kap_test_{obj_id}.pdf")
                except Exception as e:
                    print(f"Java unwrap failed: {e}")
            elif raw[:4] == b"%PDF":
                print("Already a raw PDF (no Java wrapper).")
                with open(f"/tmp/kap_test_{obj_id}.pdf", "wb") as f:
                    f.write(raw)

# Also try the alternative BildirimPdf endpoint for comparison.
pdf_r = requests.get(BILDIRIM_PDF_URL.format(idx=idx), timeout=30)
print(f"\nBildirimPdf status: {pdf_r.status_code}, {len(pdf_r.content)} bytes, starts with {pdf_r.content[:10]!r}")
if pdf_r.ok and pdf_r.content[:4] == b"%PDF":
    with open(f"/tmp/kap_test_bildirimpdf_{idx}.pdf", "wb") as f:
        f.write(pdf_r.content)

# Inspect every saved PDF's table structure.
for path in glob.glob("/tmp/kap_test_*.pdf"):
    print(f"\n=== Inspecting {path} ===")
    try:
        with pdfplumber.open(path) as pdf:
            print(f"pages: {len(pdf.pages)}")
            for pi, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                print(f"--- page {pi} text (first 1500 chars) ---")
                print(text[:1500])
                tables = page.extract_tables() or []
                print(f"--- page {pi}: {len(tables)} table(s) ---")
                for ti, table in enumerate(tables):
                    print(f"table {ti} ({len(table)} rows):")
                    for row in table[:15]:
                        print(row)
    except Exception as e:
        print(f"pdfplumber failed on {path}: {e}")
