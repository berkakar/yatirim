"""Takip edilen her fonun KAP'taki EN GÜNCEL "Portföy Dağılım Raporu"nu
yeniden çekip önbelleği (kap_portfoy_cache.json) günceller - aynı rapor
tarihi için zaten bir kayıt varsa (ör. top_n varsayılanı 6'dan 10'a
çıkarılmadan önce çekilmişse) onu SİLİP YERİNE YENİSİNİ koyar, farklı bir
kullanıcının/fonun geçmiş rapor tarihlerine dokunmaz.

fon_hisse_uyari.py gibi GitHub Actions'ta çalışır, bu yüzden
turk_fonlari_takip_data.py'nin Streamlit'e bağlı (st.secrets) katmanını
kullanmaz - takip_fonlari_*.json ve kap_portfoy_cache.json dosyalarını
doğrudan repo checkout'undan okur/yazar.

Normalde otomatik tetiklenmez (sadece workflow_dispatch) - top_n
değiştiğinde veya PDF ayrıştırma mantığı düzeltildiğinde önbellekteki
eski raporları elle yenilemek için kullanılır.

Run with --once (GitHub Actions workflow'u tarafından kullanılır).
"""
import argparse
import glob
import json
import os
import re
from datetime import datetime

from kap_client import KapFetchError, get_latest_top_holdings

CACHE_FILE = "kap_portfoy_cache.json"
TOP_N = 10

_USER_RE = re.compile(r"^takip_fonlari_(.+)\.json$")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _discover_funds() -> dict[str, str]:
    """Tüm kullanıcıların takip listelerinden benzersiz {kod: unvan} döner."""
    funds: dict[str, str] = {}
    for path in glob.glob("takip_fonlari_*.json"):
        if not _USER_RE.match(os.path.basename(path)):
            continue
        for fund in _load_json(path, []):
            funds[fund["code"]] = fund["name"]
    return funds


def _replace_report(cache: dict, fund_code: str, fund_name: str, report: dict) -> None:
    """add_report'un aksine, aynı report_date_sort için zaten bir kayıt
    varsa onu YENİSİYLE DEĞİŞTİRİR (silmez, diğer tarihlere dokunmaz)."""
    entry = cache.setdefault(fund_code, {"fund_name": fund_name, "reports": []})
    entry["fund_name"] = fund_name
    entry["reports"] = [r for r in entry["reports"] if r["report_date_sort"] != report["report_date_sort"]]
    entry["reports"].append({
        "report_date": report["report_date"],
        "report_date_sort": report["report_date_sort"],
        "period_label": report["period_label"],
        "disclosure_index": report["disclosure_index"],
        "holdings": [list(h) for h in report["holdings"]],
    })
    entry["reports"].sort(key=lambda r: r["report_date_sort"], reverse=True)


def run_once() -> None:
    funds = _discover_funds()
    if not funds:
        log("Takip edilen fon yok, çıkılıyor.")
        return

    cache = _load_json(CACHE_FILE, {})
    changed = False

    for code, name in sorted(funds.items()):
        try:
            report = get_latest_top_holdings(code, name, top_n=TOP_N)
        except KapFetchError as e:
            log(f"{code}: KAP'tan çekilemedi - {e}")
            continue

        _replace_report(cache, code, name, report)
        changed = True
        log(f"{code}: {report['report_date']} raporu {len(report['holdings'])} yatırım aracıyla güncellendi.")

    if changed:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        log("Önbellek kaydedildi.")
    else:
        log("Hiçbir fon güncellenemedi, önbellek değiştirilmedi.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Tek seferlik çalıştırma (GitHub Actions).")
    args = parser.parse_args()

    run_once()
