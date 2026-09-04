"""KAP (Kamuyu Aydınlatma Platformu) API istemcisi - kullanıcının sahip
olduğu fonların "Portföy Dağılım Raporu" bildirimini bulup gerçek hisse
dağılım tablosunu içeren PDF ekini indirir.

Resmi/dokümante bir API değil - kap.org.tr'nin kendi sitesinin kullandığı
uçlar, canlı olarak (gerçek fon verileriyle, iki farklı fon/yönetici
üzerinde) doğrulandı:

  1) Fon kodu + adı -> "<kod>-<transliterasyonlu-isim>" biçiminde bir
     slug (slugify()) -> kap.org.tr/tr/fon-bilgileri/genel/<slug> sayfası
     -> sayfa HTML'inde gömülü /tr/api/batch-news/file-by-year/<fundOid>/...
     örüntüsünden fonun kendi OID'i çıkarılıyor. (Fonun kısa kodunu doğrudan
     bir arama/filtre uç noktasına vermenin bir yolu yok - KAP'ın "Fon
     Arama" arayüzü sunucu tarafında ayrı bir mekanizma kullanıyor; bu
     slug + gömülü OID yaklaşımı, tarayıcı ile keşfedilen gerçek akışın
     düz HTTP isteğiyle yeniden üretilebilir hali.)
  2) GET /tr/api/disclosure/filter/FILTERYFBF/<fundOid>/<PDR tip OID>/<gün>
     -> bu fonun "Portföy Dağılım Raporu" bildirimlerinin listesi, en
     yeniden eskiye sıralı. PDR tip OID'i (PORTFOLIO_REPORT_TYPE_OID)
     platform genelinde sabit - iki farklı fon için de aynı değerle
     doğrulandı (KAP'ın fon-bildirimleri sayfasındaki "Bildirim Tipi"
     açılır menüsünden tespit edildi).
  3) En yeni disclosureIndex -> GET /tr/api/notification/attachment-detail/
     <disclosureIndex> -> attachments[0].objId.
  4) GET /tr/api/file/download/<objId> -> Java-serialized bir byte[]
     (ham PDF değil) - gerçek PDF bu sarmalayıcının içinde.

ÖNEMLİ: /en/api/BildirimPdf/<id> uç noktası SADECE bir kapak/özet sayfası
döner (gerçek hisse tablosu YOK) - bu yüzden 3-4. adımlardaki gerçek ek
kullanılıyor, ilk denemede kullanılan bu daha basit ama YANLIŞ uç nokta
değil.
"""
import re
import struct

import requests

TR_TRANSLITERATE = str.maketrans({
    "İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ğ": "g", "ğ": "g",
    "Ü": "u", "ü": "u", "Ö": "o", "ö": "o", "Ç": "c", "ç": "c",
})

FUND_PAGE_URL = "https://www.kap.org.tr/tr/fon-bilgileri/genel/{slug}"
FILTER_URL = "https://www.kap.org.tr/tr/api/disclosure/filter/FILTERYFBF/{fund_oid}/{type_oid}/{days}"
DETAIL_URL = "https://www.kap.org.tr/tr/api/notification/attachment-detail/{idx}"
FILE_DOWNLOAD_URL = "https://www.kap.org.tr/tr/api/file/download/{obj_id}"

# "Portföy Dağılım Raporu" bildirim tipinin KAP genelinde sabit OID'i.
PORTFOLIO_REPORT_TYPE_OID = "8aca490d502e34b801502e380044002b"
SEARCH_DAYS = 365

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; yatirim-app/1.0)"}


def slugify(code: str, fund_name: str) -> str:
    """KAP'ın fon sayfası URL biçimi: <kod>-<transliterasyonlu-isim>."""
    name = (fund_name or "").translate(TR_TRANSLITERATE).lower()
    name = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    return f"{code.lower()}-{name}"


def resolve_fund_oid(code: str, fund_name: str) -> str:
    """Fonun kendi kap.org.tr sayfasından fundOid'i çıkarır - bu OID
    disclosure/filter uç noktasında kullanılıyor."""
    slug = slugify(code, fund_name)
    r = requests.get(FUND_PAGE_URL.format(slug=slug), headers=HEADERS, timeout=20)
    r.raise_for_status()
    m = re.search(r"/tr/api/batch-news/file-by-year/([0-9A-Fa-f]{32})/", r.text)
    if not m:
        raise ValueError(f"'{code}' için KAP fon sayfasında fundOid bulunamadı (slug: {slug}).")
    return m.group(1)


def find_latest_portfolio_report(fund_oid: str) -> dict | None:
    """Bu fon için en güncel "Portföy Dağılım Raporu" bildirimini döner:
    {"disclosure_index", "publish_date", "title"} ya da bulunamazsa None."""
    url = FILTER_URL.format(fund_oid=fund_oid, type_oid=PORTFOLIO_REPORT_TYPE_OID, days=SEARCH_DAYS)
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    items = r.json()
    if not items:
        return None
    basic = items[0]["disclosureBasic"]
    return {
        "disclosure_index": basic["disclosureIndex"],
        "publish_date": basic.get("publishDate"),
        "title": basic.get("summary") or basic.get("title"),
    }


def _unwrap_java_bytes(raw: bytes) -> bytes:
    """/tr/api/file/download/<objId> yanıtı Java-serialized bir byte[] -
    gerçek PDF bu sarmalayıcının içinde."""
    if raw[:4] == b"%PDF":
        return raw
    idx = raw.index(b"\x78\x70", 10)
    arr_len = struct.unpack(">I", raw[idx + 2:idx + 6])[0]
    return raw[idx + 6:idx + 6 + arr_len]


def download_portfolio_pdf(disclosure_index: int) -> bytes:
    """Bildirimin gerçek ekini (asıl hisse dağılım tablosunu içeren PDF)
    indirir."""
    r = requests.get(DETAIL_URL.format(idx=disclosure_index), headers=HEADERS, timeout=20)
    r.raise_for_status()
    detail = r.json()
    if not (isinstance(detail, list) and detail):
        raise ValueError(f"Bildirim {disclosure_index} için detay alınamadı.")
    attachments = detail[0].get("attachments") or []
    if not attachments:
        raise ValueError(f"Bildirim {disclosure_index} için ek bulunamadı.")
    obj_id = attachments[0]["objId"]

    file_r = requests.get(FILE_DOWNLOAD_URL.format(obj_id=obj_id), headers=HEADERS, timeout=30)
    file_r.raise_for_status()
    return _unwrap_java_bytes(file_r.content)
