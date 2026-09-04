"""SON uçtan uca doğrulama (2. deneme) - kap_client.resolve_fund_oid'e
cache-busting + retry eklendi (KAP'ın CDN önbelleği zaman zaman fundOid'in
gömülü olduğu bölümü içermeyen bir kopya döndürüyordu - iki canlı denemede
gözlemlendi, aynı uzunlukta ama farklı içerikte). Bu artık gerçek üretim
modüllerini uçtan uca test ediyor.
"""
from kap_client import download_portfolio_pdf, find_latest_portfolio_report, resolve_fund_oid
from kap_holdings_parser import parse_top_holdings

CASES = [
    ("TPR", "İŞ PORTFÖY PY HİSSE SENEDİ (TL) ÖZEL FONU (HİSSE SENEDİ YOĞUN FON)"),
    ("KYA", "KARE PORTFÖY HİSSE SENEDİ FONU (HİSSE SENEDİ YOĞUN FON)"),
]

for code, name in CASES:
    print(f"\n{'=' * 60}\n{code}\n{'=' * 60}")
    fund_oid = resolve_fund_oid(code, name)
    print(f"fund_oid = {fund_oid}")

    latest = find_latest_portfolio_report(fund_oid)
    print(f"latest report = {latest}")
    if not latest:
        print("No report found - FAIL")
        continue

    pdf_bytes = download_portfolio_pdf(latest["disclosure_index"])
    print(f"PDF downloaded: {len(pdf_bytes)} bytes, starts with {pdf_bytes[:8]!r}")

    holdings = parse_top_holdings(pdf_bytes)
    print(f"Top holdings ({len(holdings)}):")
    for h in holdings:
        print(f"  {h}")

    if not holdings:
        print("NO HOLDINGS PARSED - FAIL")
    else:
        total = sum(h["Oran %"] for h in holdings)
        print(f"Sum of top-{len(holdings)} percentages: {total:.2f}%")
