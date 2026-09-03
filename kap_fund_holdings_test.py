"""Geçici son doğrulama turu. Önceki tur şunu gösterdi: byCriteria,
mkkMemberOidList=[fon yönetim şirketi OID'i] ile scoped arandığında o
şirketin KURUMSAL bildirimlerini (Sorumluluk Beyanı, Faaliyet Raporu vb.)
döndürüyor ama fonun "Portföy Dağılım Raporu"nu DÖNDÜRMÜYOR - aynı tarih
aralığında olmasına rağmen. mkkMemberOidList=[fonun kendi fundOid'i] ise
0 sonuç döndürdü (bu alan sadece şirket OID'i kabul ediyor gibi).
Bu turda: (1) payload'a ayrı bir "fundOidList" alanı eklenerek fon bazlı
scoping deneniyor, (2) fon özet sayfasının HTML'i (varsa __NEXT_DATA__
içinde gömülü veri) inceleniyor - KAP'ın kendi arayüzünün bu veriyi nasıl
çektiğini ortaya çıkarabilir.
"""
import re

import requests

BY_CRITERIA_URL = "https://www.kap.org.tr/tr/api/disclosure/members/byCriteria"
FUND_OZET_URL = "https://www.kap.org.tr/tr/fon-bilgileri/ozet/{oid}"

CASES = [
    ("KYA", "402821814da3a99c014e2ab71fee6e8e", "33E5FED7E5D300EAE0530A4A622B2AEA"),
    ("TPR", "4028e4a141733b5601417434d6a42b9f", "33E5FED7ECE300EAE0530A4A622B2AEA"),
]

for code, company_oid, fund_oid in CASES:
    print(f"\n### {code} ###")

    # Variant A: fundOidList alongside mkkMemberOidList.
    payload_a = {
        "fromDate": "2025-01-01", "toDate": "2025-09-03",
        "mkkMemberOidList": [company_oid], "fundOidList": [fund_oid],
        "subjectList": [],
    }
    ra = requests.post(BY_CRITERIA_URL, json=payload_a, timeout=20)
    print(f"variant A (mkkMemberOidList + fundOidList): status={ra.status_code}")
    if ra.ok:
        items = ra.json()
        print(f"  -> {len(items)} disclosures")
        for it in items[:20]:
            print(f"     idx={it.get('disclosureIndex')} summary={it.get('summary')!r} "
                  f"subject={it.get('subject')!r} fundCode={it.get('fundCode')!r}")

    # Variant B: fundOidList only, no mkkMemberOidList.
    payload_b = {
        "fromDate": "2025-01-01", "toDate": "2025-09-03",
        "mkkMemberOidList": [], "fundOidList": [fund_oid],
        "subjectList": [],
    }
    rb = requests.post(BY_CRITERIA_URL, json=payload_b, timeout=20)
    print(f"variant B (fundOidList only): status={rb.status_code}")
    if rb.ok:
        items = rb.json()
        print(f"  -> {len(items)} disclosures")
        for it in items[:20]:
            print(f"     idx={it.get('disclosureIndex')} summary={it.get('summary')!r} "
                  f"subject={it.get('subject')!r} fundCode={it.get('fundCode')!r}")

    # Fund summary page HTML - look for __NEXT_DATA__ or any embedded JSON,
    # and for hints of the real search endpoint the frontend calls.
    fr = requests.get(FUND_OZET_URL.format(oid=fund_oid), timeout=20)
    print(f"fon-bilgileri/ozet page: status={fr.status_code}, len={len(fr.text)}")
    if fr.ok:
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', fr.text, re.S)
        if m:
            print(f"__NEXT_DATA__ found, length={len(m.group(1))}, first 2000 chars:")
            print(m.group(1)[:2000])
        else:
            print("No __NEXT_DATA__ script tag found.")
        # Look for any api path mentioned in the raw HTML/JS referencing "fon" or "fund".
        api_hints = set(re.findall(r'/tr/api/[a-zA-Z0-9/\-]*fon[a-zA-Z0-9/\-]*', fr.text, re.I))
        api_hints |= set(re.findall(r'/tr/api/[a-zA-Z0-9/\-]*fund[a-zA-Z0-9/\-]*', fr.text, re.I))
        print(f"API path hints containing fon/fund: {api_hints}")
