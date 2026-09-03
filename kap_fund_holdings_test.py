"""Geçici manuel doğrulama scripti - önceki turda gerçek "Portföy Dağılım
Raporu" tablosunun file/download eki üzerinden başarıyla okunduğu doğrulandı.
Bu turda: (1) o bildirimin mkkMemberOid'ini çıkarıp o OID'e göre scoped
byCriteria araması gerçekten çalışıyor mu test ediyor, (2) kritik alanları
log'un SONUNDA kompakt biçimde yazdırıyor (önceki turlarda dev PDF tablo
dökümleri yüzünden log tail'i JSON'u kesiyordu).
"""
import json
from datetime import date, timedelta

import requests

DETAIL_URL = "https://www.kap.org.tr/tr/api/notification/attachment-detail/{idx}"
BY_CRITERIA_URL = "https://www.kap.org.tr/tr/api/disclosure/members/byCriteria"
MEMBER_FILTER_URL = "https://www.kap.org.tr/tr/api/member/filter/{code}"

KNOWN_IDS = [1473544, 1247775]
summary_lines = []


def log(line: str):
    print(line)
    summary_lines.append(line)


for idx in KNOWN_IDS:
    log(f"\n### idx={idx} ###")
    r = requests.get(DETAIL_URL.format(idx=idx), timeout=20)
    log(f"detail status: {r.status_code}")
    if not r.ok:
        continue
    detail = r.json()
    if not (isinstance(detail, list) and detail):
        log(f"unexpected detail shape: {type(detail)}")
        continue
    entry = detail[0]
    disclosure = entry.get("disclosure") or entry
    basic = disclosure.get("disclosureBasic") or {}
    det = disclosure.get("disclosureDetail") or {}
    log(f"basic.title={basic.get('title')!r}")
    log(f"basic.companyTitle={basic.get('companyTitle')!r}")
    log(f"basic.mkkMemberOid={basic.get('mkkMemberOid')!r}")
    log(f"basic.disclosureId={basic.get('disclosureId')!r}")
    log(f"basic.publishDate={basic.get('publishDate')!r}")
    log(f"basic.fundType={basic.get('fundType')!r}")
    log(f"basic.stockCode={basic.get('stockCode')!r}")
    log(f"det.fundOid={det.get('fundOid')!r}")
    log(f"det.memberType={det.get('memberType')!r}")
    attachments = entry.get("attachments") or []
    log(f"attachments={json.dumps(attachments, ensure_ascii=False)}")

    # Try member/filter with the fund's own title words / oid, and try
    # byCriteria scoped to this exact mkkMemberOid over a window covering
    # the known publish date, to see whether OID-scoped search finds it
    # (isolating: is search broken in general, or just full-text/fundCode
    # matching on an unscoped query?).
    oid = basic.get("mkkMemberOid")
    if oid:
        pub = basic.get("publishDate") or ""
        try:
            pub_date = date(int(pub[:4]), int(pub[5:7]), int(pub[8:10])) if pub[:4].isdigit() else date.today()
        except Exception:
            pub_date = date.today()
        start = pub_date - timedelta(days=10)
        end = pub_date + timedelta(days=10)
        payload = {
            "fromDate": start.strftime("%Y-%m-%d"),
            "toDate": end.strftime("%Y-%m-%d"),
            "mkkMemberOidList": [oid],
            "subjectList": [],
        }
        br = requests.post(BY_CRITERIA_URL, json=payload, timeout=20)
        log(f"byCriteria scoped to mkkMemberOid={oid} [{start}..{end}]: status={br.status_code}")
        if br.ok:
            items = br.json()
            log(f"  -> {len(items)} disclosures found")
            for it in items[:10]:
                log(f"     idx={it.get('disclosureIndex')} kapTitle={it.get('kapTitle')!r} "
                    f"summary={it.get('summary')!r} subject={it.get('subject')!r} "
                    f"fundCode={it.get('fundCode')!r} publishDate={it.get('publishDate')!r}")

    # Try member/filter using the fund's companyTitle as a naive guess (KAP's
    # filter may do substring match on title, not just ticker).
    company_title = basic.get("companyTitle") or ""
    first_word = company_title.split()[0] if company_title else ""
    if first_word:
        mr = requests.get(MEMBER_FILTER_URL.format(code=first_word), timeout=15)
        log(f"member/filter({first_word!r}): status={mr.status_code}, body[:300]={mr.text[:300]!r}")

log("\n### SUMMARY (compact, should survive tail truncation) ###")
for line in summary_lines:
    print(f"SUMMARY| {line}")
