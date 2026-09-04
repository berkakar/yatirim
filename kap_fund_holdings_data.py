"""Kullanıcının "sahip olduğum fonlar" listesinin ve bu fonlar için KAP'tan
parse edilmiş en yüksek 6 hisse pozisyonunun kalıcı saklanması.

Her fon için fund_oid ilk kontrolde çözülüp (kap_client.resolve_fund_oid)
kaydedilir - sonraki kontroller doğrudan bu OID'i kullanır, sayfa
scrape'ini tekrarlamaz. last_disclosure_index, en son parse edilen
"Portföy Dağılım Raporu" bildiriminin KAP disclosureIndex'i - "Kontrol
Et" bu değeri KAP'taki en güncel bildirimle karşılaştırıp farklıysa
yeniden parse eder.

backtest_data.py'deki desenle aynı: GitHub Contents API üzerinden kalıcı
(Streamlit Cloud restart'larında kaybolmayan) kopyayı önceliklendirir, yoksa
yerel dosyaya düşer, her ikisine de yazar.
"""
import json
import os
from datetime import datetime, timezone

import streamlit as st

from github_config import read_json_from_github, write_json_to_github
from kap_client import download_portfolio_pdf, find_latest_portfolio_report, resolve_fund_oid
from kap_holdings_parser import parse_top_holdings

GITHUB_REPO = "berkakar/yatirim"


def _data_file(username: str) -> str:
    return f"kap_fund_holdings_{username}.json"


def load_data(username: str) -> dict:
    save_file = _data_file(username)
    token = st.secrets.get("GITHUB_TOKEN")
    if token:
        try:
            return read_json_from_github(GITHUB_REPO, token, save_file, {"funds": {}})
        except Exception:
            pass

    if os.path.exists(save_file):
        try:
            with open(save_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"funds": {}}
    return {"funds": {}}


def save_data(username: str, data: dict) -> None:
    save_file = _data_file(username)
    token = st.secrets.get("GITHUB_TOKEN")
    if token:
        try:
            write_json_to_github(GITHUB_REPO, token, save_file, data, f"Update KAP fund holdings ({username})")
        except Exception as e:
            st.warning(f"⚠️ Fon listesi GitHub'a kalıcı olarak kaydedilemedi (sadece bu oturumda geçerli olacak): {e}")

    with open(save_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_fund(username: str, code: str, name: str) -> dict:
    data = load_data(username)
    data.setdefault("funds", {})
    if code not in data["funds"]:
        data["funds"][code] = {"fund_name": name}
        save_data(username, data)
    return data


def remove_fund(username: str, code: str) -> dict:
    data = load_data(username)
    data.get("funds", {}).pop(code, None)
    save_data(username, data)
    return data


def refresh_fund(username: str, code: str) -> dict:
    """KAP'ta bu fon için en güncel "Portföy Dağılım Raporu"nu arar. Daha
    önce parse edilenden farklı (yeni) bir bildirimse indirip parse eder ve
    sonucu kaydeder; aynıysa sadece "son kontrol" zamanı güncellenir."""
    data = load_data(username)
    entry = data.get("funds", {}).get(code)
    if entry is None:
        raise ValueError(f"'{code}' kayıtlı fonlar arasında yok.")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry["last_checked"] = now

    try:
        fund_oid = entry.get("fund_oid")
        if not fund_oid:
            fund_oid = resolve_fund_oid(code, entry.get("fund_name", ""))
            entry["fund_oid"] = fund_oid
        latest = find_latest_portfolio_report(fund_oid)
    except Exception as e:
        entry["last_error"] = str(e)
        save_data(username, data)
        return entry

    if latest is None:
        entry["last_error"] = "KAP'ta bu fon için 'Portföy Dağılım Raporu' bulunamadı."
        save_data(username, data)
        return entry

    entry["last_error"] = None
    if latest["disclosure_index"] == entry.get("last_disclosure_index"):
        save_data(username, data)  # sadece last_checked güncellendi
        return entry

    try:
        pdf_bytes = download_portfolio_pdf(latest["disclosure_index"])
        holdings = parse_top_holdings(pdf_bytes)
    except Exception as e:
        entry["last_error"] = f"Rapor indirilemedi/parse edilemedi: {e}"
        save_data(username, data)
        return entry

    entry["last_disclosure_index"] = latest["disclosure_index"]
    entry["report_title"] = latest["title"]
    entry["report_publish_date"] = latest["publish_date"]
    entry["last_parsed"] = now
    entry["holdings"] = holdings
    if not holdings:
        entry["last_error"] = "Rapor indirildi ama hisse tablosu ayrıştırılamadı."

    save_data(username, data)
    return entry
