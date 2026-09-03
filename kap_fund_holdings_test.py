"""Geçici manuel doğrulama scripti - kap_client.py / kap_holdings_parser.py'nin
gerçek KAP verisiyle çalıştığını GitHub Actions (workflow_dispatch) üzerinden
teyit etmek için. Bu depoda kalıcı bir özellik değil; doğrulama sonrası
kap_test.yml ile birlikte kaldırılacak.
"""
import argparse
import json

from kap_client import download_pdf, find_latest_portfolio_report, resolve_member_oid
from kap_holdings_parser import parse_top_holdings

parser = argparse.ArgumentParser()
parser.add_argument("--code", required=True)
args = parser.parse_args()

oid = resolve_member_oid(args.code)
print(f"OID: {oid}")

report = find_latest_portfolio_report(args.code)
print(f"Latest report: {json.dumps(report, ensure_ascii=False)}")

if report:
    pdf_bytes = download_pdf(report["disclosure_id"])
    print(f"PDF size: {len(pdf_bytes)} bytes")
    holdings = parse_top_holdings(pdf_bytes)
    print(f"Holdings: {json.dumps(holdings, ensure_ascii=False, indent=2)}")
