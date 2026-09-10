"""Kullanıcının kendi elinde bulunan (alım yaptığı) Türk fonlarını
kaydedip takip edebildiği modülün kalıcılık katmanı.

İki ayrı veri var:
  - Takip edilen fon listesi: kullanıcıya özel (bkz. config.py'deki
    save_ticker_lists ile aynı GitHub + yerel dosya yedekleme deseni).
  - KAP'tan çekilen "en büyük 6 yatırım aracı" önbelleği: fon koduna göre,
    TÜM kullanıcılar arasında PAYLAŞILAN tek bir dosya (tefas_fonlari_cache.json
    gibi - veri kullanıcıya değil foruna ait). Her fon için gelen yeni rapor,
    eskisini silmeden "reports" listesine eklenir (geçmiş tarihli raporlar
    da tabloda görünsün diye) - bkz. render katmanındaki kullanım.
"""
import json
import os

import streamlit as st

from config import GITHUB_REPO
from github_config import read_json_from_github, write_json_to_github

PORTFOLIO_CACHE_FILE = "kap_portfoy_cache.json"


def _tracked_file(username: str) -> str:
    return f"takip_fonlari_{username}.json"


def load_tracked_funds(username: str) -> list[dict]:
    """[{"code": "THF", "name": "TERA PORTFÖY ..."}, ...] döner, hiç
    kaydedilmemişse boş liste."""
    tracked_file = _tracked_file(username)
    data = None
    token = st.secrets.get("GITHUB_TOKEN")
    if token:
        try:
            data = read_json_from_github(GITHUB_REPO, token, tracked_file, None)
        except Exception:
            data = None

    if data is None and os.path.exists(tracked_file):
        try:
            with open(tracked_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = None

    return data or []


def save_tracked_funds(funds: list[dict], username: str) -> None:
    tracked_file = _tracked_file(username)
    token = st.secrets.get("GITHUB_TOKEN")
    if token:
        try:
            write_json_to_github(GITHUB_REPO, token, tracked_file, funds, f"Update takip fonları ({username})")
        except Exception as e:
            st.warning(f"⚠️ Takip listesi GitHub'a kalıcı olarak kaydedilemedi (sadece bu oturumda geçerli olacak): {e}")

    with open(tracked_file, "w", encoding="utf-8") as f:
        json.dump(funds, f, ensure_ascii=False, indent=2)


def load_portfolio_cache() -> dict:
    """{fund_code: {"fund_name": ..., "reports": [{"report_date",
    "report_date_sort", "period_label", "disclosure_index", "holdings":
    [[kod, yüzde], ...]}, ...]}} - tüm kullanıcılar arasında paylaşılır."""
    data = None
    token = st.secrets.get("GITHUB_TOKEN")
    if token:
        try:
            data = read_json_from_github(GITHUB_REPO, token, PORTFOLIO_CACHE_FILE, None)
        except Exception:
            data = None

    if data is None and os.path.exists(PORTFOLIO_CACHE_FILE):
        try:
            with open(PORTFOLIO_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = None

    return data or {}


def save_portfolio_cache(cache: dict) -> None:
    token = st.secrets.get("GITHUB_TOKEN")
    if token:
        try:
            write_json_to_github(GITHUB_REPO, token, PORTFOLIO_CACHE_FILE, cache, "Update KAP portföy dağılım önbelleği")
        except Exception as e:
            st.warning(f"⚠️ KAP önbelleği GitHub'a kalıcı olarak kaydedilemedi (sadece bu oturumda geçerli olacak): {e}")

    with open(PORTFOLIO_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def latest_report_date_sort(cache: dict, fund_code: str) -> str | None:
    reports = (cache.get(fund_code) or {}).get("reports") or []
    if not reports:
        return None
    return max(r["report_date_sort"] for r in reports)


def add_report(cache: dict, fund_code: str, fund_name: str, report: dict) -> bool:
    """`report` (kap_client.get_latest_top_holdings'in döndürdüğü sözlük)
    zaten önbellekte yoksa geçmişe ekler. Yeni bir şey eklenmişse True döner."""
    entry = cache.setdefault(fund_code, {"fund_name": fund_name, "reports": []})
    entry["fund_name"] = fund_name
    if any(r["report_date_sort"] == report["report_date_sort"] for r in entry["reports"]):
        return False
    entry["reports"].append({
        "report_date": report["report_date"],
        "report_date_sort": report["report_date_sort"],
        "period_label": report["period_label"],
        "disclosure_index": report["disclosure_index"],
        "holdings": [list(h) for h in report["holdings"]],
    })
    entry["reports"].sort(key=lambda r: r["report_date_sort"], reverse=True)
    return True
