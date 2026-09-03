"""KAP (Kamuyu Aydınlatma Platformu) API istemcisi - kullanıcının sahip olduğu
fonların "Portföy Dağılım Raporu" bildirimini bulup PDF'ini indirir.

Resmi/dokümante bir API değil - KAP'ın kendi web sitesinin kullandığı, kimlik
doğrulama gerektirmeyen uçlar (açık kaynak enciyo/kap-tr-sdk projesinin
kaynak kodundan doğrulandı):
  - GET  /tr/api/member/filter/<kod>        -> fon/şirket kodunu KAP üye
    OID'sine çözer (fon kısa kodu da TEFAS koduyla aynı, bir KAP üye kodu).
  - POST /tr/api/disclosure/list/main       -> bir üyenin bildirimlerini
    tarih aralığına göre listeler.
  - GET  /en/api/BildirimPdf/<disclosureId> -> bir bildirimin PDF'i.

"Portföy Dağılım Raporu" ayrı bir disclosureType koduyla filtrelenemiyor -
KAP bunu genel bildirim akışı içinde, başlıkla ayırt edilecek şekilde
yayınlıyor - bu yüzden bildirim listesi çekilip başlıkta bu ifade aranarak
süzülüyor.
"""
import sys
from datetime import date, timedelta

if sys.platform == "win32":
    import truststore
    truststore.inject_into_ssl()

import requests

MEMBER_FILTER_URL = "https://www.kap.org.tr/tr/api/member/filter/{code}"
DISCLOSURE_LIST_URL = "https://www.kap.org.tr/tr/api/disclosure/list/main"
PDF_URL = "https://www.kap.org.tr/en/api/BildirimPdf/{disclosure_id}"

REPORT_TITLE_MARKER = "portföy dağılım raporu"
# Raporlar aylık, ayın ilk haftasında bir önceki ay için yayınlanır (SPK
# düzenlemesi) - gecikme/tatil payı bırakan geniş bir arama penceresi.
SEARCH_WINDOW_DAYS = 130

# KAP'ın kendi arama filtresindeki tüm fon/üye tipleri - mkkMemberOid zaten
# tek bir üyeye özel olduğu için bunlar sonucu daraltmasın diye tam liste
# gönderiliyor.
_ALL_FUND_TYPES = ["BYF", "YF", "EYF", "OKS", "YYF", "VFF", "KFF", "GMF", "GSF", "PFF"]
_ALL_MEMBER_TYPES = ["IGS", "YK", "PYS", "DDK", "DG"]


def _tr_lower(s: str) -> str:
    return (s or "").replace("İ", "i").replace("I", "ı").lower()


def resolve_member_oid(fund_code: str) -> str | None:
    r = requests.get(MEMBER_FILTER_URL.format(code=fund_code), timeout=15)
    r.raise_for_status()
    results = r.json()
    if not results:
        return None
    return results[0].get("mkkMemberOid")


def fetch_disclosures(oid: str, from_date: date, to_date: date) -> list[dict]:
    payload = {
        "fromDate": from_date.strftime("%d.%m.%Y"),
        "toDate": to_date.strftime("%d.%m.%Y"),
        "disclosureType": None,
        "fundTypes": _ALL_FUND_TYPES,
        "memberTypes": _ALL_MEMBER_TYPES,
        "mkkMemberOid": oid,
    }
    r = requests.post(DISCLOSURE_LIST_URL, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()


def find_latest_portfolio_report(fund_code: str) -> dict | None:
    """Fon kodu için en güncel "Portföy Dağılım Raporu" bildirimini bulur.
    Dönüş: {"disclosure_id", "title", "publish_date"} ya da bulunamazsa None."""
    oid = resolve_member_oid(fund_code)
    if not oid:
        raise ValueError(f"KAP'ta '{fund_code}' koduna ait bir üye bulunamadı.")

    today = date.today()
    disclosures = fetch_disclosures(oid, today - timedelta(days=SEARCH_WINDOW_DAYS), today)

    candidates = []
    for item in disclosures:
        basic = item.get("disclosureBasic") or {}
        title = basic.get("title") or ""
        if REPORT_TITLE_MARKER not in _tr_lower(title):
            continue
        disclosure_id = basic.get("disclosureId")
        if not disclosure_id:
            continue
        candidates.append({
            "disclosure_id": str(disclosure_id),
            "title": title,
            "publish_date": basic.get("publishDate"),
        })

    if not candidates:
        return None

    # disclosureId, KAP genelinde monoton artan bir sayı - en yüksek olan en
    # güncel bildirimdir (publishDate'in kesin biçimi doğrulanamadığından
    # sıralama için kullanılmıyor, sadece gösterim amaçlı saklanıyor).
    candidates.sort(key=lambda c: int(c["disclosure_id"]) if c["disclosure_id"].isdigit() else -1)
    return candidates[-1]


def download_pdf(disclosure_id: str) -> bytes:
    r = requests.get(PDF_URL.format(disclosure_id=disclosure_id), timeout=30)
    r.raise_for_status()
    return r.content
