"""Geçici manuel doğrulama scripti - son tur şunu ortaya çıkardı:
disclosureBasic.mkkMemberOid FON YÖNETİM ŞİRKETİ'nin OID'i (fonun değil);
byCriteria bu OID'e scoped arama şirket düzeyi bildirimleri döndürüyor,
fonun kendi "Portföy Dağılım Raporu"nu DEĞİL. disclosureBasic.stockCode
ise TEFAS'taki fon koduyla birebir aynı görünüyor (KYA, TPR).
member/filter('KARE') şirket adında serbest metin/substring eşleşmesi
yaptığını gösterdi (tam ticker değil). Bu turda: member/filter'ı fonun
KENDİ stockCode'u ile (KYA, TPR) deniyoruz - eğer bu fonun kendi OID'ini
(det.fundOid ile eşleşen) döndürüyorsa, TEFAS kodu -> KAP kimliği sorunu
tamamen çözülmüş olur.
"""
import requests

MEMBER_FILTER_URL = "https://www.kap.org.tr/tr/api/member/filter/{code}"
BY_CRITERIA_URL = "https://www.kap.org.tr/tr/api/disclosure/members/byCriteria"

# KYA -> det.fundOid bilinen: 33E5FED7E5D300EAE0530A4A622B2AEA (Kare fon)
# TPR -> det.fundOid bilinen: 33E5FED7ECE300EAE0530A4A622B2AEA (İş Portföy fon)
CASES = [
    ("KYA", "33E5FED7E5D300EAE0530A4A622B2AEA"),
    ("TPR", "33E5FED7ECE300EAE0530A4A622B2AEA"),
]

for code, known_fund_oid in CASES:
    print(f"\n### stockCode={code} (known fundOid={known_fund_oid}) ###")
    r = requests.get(MEMBER_FILTER_URL.format(code=code), timeout=15)
    print(f"member/filter({code!r}): status={r.status_code}")
    print(f"body: {r.text[:1500]}")

    # Also try the fon-bilgileri page's own API pattern, if any - probe a
    # couple of plausible fund-search endpoints.
    for probe_url in [
        f"https://www.kap.org.tr/tr/api/fund/filter/{code}",
        f"https://www.kap.org.tr/tr/api/fon/filter/{code}",
        f"https://www.kap.org.tr/tr/api/member/fund/filter/{code}",
    ]:
        try:
            pr = requests.get(probe_url, timeout=10)
            print(f"probe {probe_url}: status={pr.status_code}, body[:200]={pr.text[:200]!r}")
        except Exception as e:
            print(f"probe {probe_url}: error {e}")

    # Try byCriteria scoped to the FUND's own oid (not the management company's).
    payload = {
        "fromDate": "2025-01-01",
        "toDate": "2025-09-03",
        "mkkMemberOidList": [known_fund_oid],
        "subjectList": [],
    }
    br = requests.post(BY_CRITERIA_URL, json=payload, timeout=20)
    print(f"byCriteria scoped to fundOid={known_fund_oid} [2025-01-01..2025-09-03]: status={br.status_code}")
    if br.ok:
        items = br.json()
        print(f"  -> {len(items)} disclosures found")
        for it in items[:15]:
            print(f"     idx={it.get('disclosureIndex')} kapTitle={it.get('kapTitle')!r} "
                  f"summary={it.get('summary')!r} subject={it.get('subject')!r} "
                  f"publishDate={it.get('publishDate')!r}")
