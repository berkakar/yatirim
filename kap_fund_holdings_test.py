"""SON uçtan uca doğrulama - artık gerçek üretim modüllerini (kap_client.py,
kap_holdings_parser.py) doğrudan kullanıyor, tahmini/keşif kodu değil.
İki farklı fon (TPR/İş Portföy, KYA/Kare Portföy) için tam akışı test
ediyor: kod+isim -> fundOid -> en güncel Portföy Dağılım Raporu -> PDF
indir -> en yüksek 6 hisse. Bu geçtiyse özellik tamamen doğrulanmış olur
ve bu geçici test dosyaları kaldırılabilir.
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
        print(f"Sum of top-{len(holdings)} percentages: {total:.2f}% (sanity check, should be a plausible slice of 100%)")
