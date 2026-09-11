"""KAP (Kamuyu Aydınlatma Platformu, kap.org.tr) istemcisi - bir fonun en
büyük yatırım araçlarını (holdings), fonun periyodik "Portföy Dağılım
Raporu" bildirimine ekli PDF'ten okur.

kap.org.tr, sayfalarını Next.js ile sunar; bildirim listesi ve dosya eki
bilgisi sayfa HTML'ine JS string'i olarak (React Server Component akışı)
gömülü gelir, bu yüzden bazı adımlarda düz `requests.get` + regex ile HTML
içinden veri çekmek gerekiyor - gerçek bir tarayıcıda ağ trafiği izlenerek
(bkz. commit geçmişi/konuşma) tespit edilen üç adım:

  1. Fon kodu+unvanından KAP'ın kullandığı URL slug'ı türetilir (KAP'ın
     kendi slug'ı ile birebir aynı algoritma - Türkçe karakterleri ASCII'ye
     çevirip küçük harfe indirger, alfanümerik olmayanları tire yapar).
  2. `fon-bildirimleri/{slug}` sayfası çekilip fonun dahili kimliği
     (mkkMemberOid) sayfaya gömülü JSON'dan okunur.
  3. `api/disclosure/filter/FILTERYFBF/{mkkMemberOid}/{konu_id}/{gün}`
     (gerçek bir JSON REST API - tarayıcının kendisinin çağırdığı) ile son
     `gün` içindeki "Portföy Dağılım Raporu" bildirimleri listelenir, en
     güncel olanının disclosureIndex'i alınır.
  4. `Bildirim/{disclosureIndex}` sayfasından ekli PDF'in indirme linki
     okunur, PDF indirilip pdfplumber ile "III-FON PORTFÖY DEĞERİ TABLOSU"
     ayrıştırılır.
"""
import re
from datetime import datetime

import pdfplumber
import requests
from io import BytesIO

BASE_URL = "https://www.kap.org.tr"
PORTFOY_DAGILIM_SUBJECT_ID = "8aca490d502e34b801502e380044002b"  # KAP'ın "Portföy Dağılım Raporu" bildirim konusu sabit kimliği
FUND_FILTER_KEY = "FILTERYFBF"  # fon bildirimleri filtre API'sinin sabit ilk parçası

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.kap.org.tr/tr/YatirimFonlari/ALL",
}

_TR_TRANSLIT = str.maketrans({
    "İ": "i", "I": "i", "ı": "i",
    "Ğ": "g", "ğ": "g",
    "Ü": "u", "ü": "u",
    "Ş": "s", "ş": "s",
    "Ö": "o", "ö": "o",
    "Ç": "c", "ç": "c",
})

_TR_MONTHS = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık",
}


class KapFetchError(Exception):
    """KAP'tan veri çekilirken/ayrıştırılırken oluşan, kullanıcıya
    gösterilebilecek açıklayıcı hata."""


def _slugify(text: str) -> str:
    text = (text or "").translate(_TR_TRANSLIT).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def build_fund_slug(fund_code: str, fund_name: str) -> str:
    """KAP'ın fon sayfalarında kullandığı URL slug'ını türetir, ör.
    ('THF', 'TERA PORTFÖY HİSSE SENEDİ (TL) FONU (HİSSE SENEDİ YOĞUN FON)')
    -> 'thf-tera-portfoy-hisse-senedi-tl-fonu-hisse-senedi-yogun-fon'."""
    return f"{_slugify(fund_code)}-{_slugify(fund_name)}"


def _get(url: str, **kwargs) -> requests.Response:
    try:
        r = requests.get(url, headers=HEADERS, timeout=30, **kwargs)
    except requests.RequestException as exc:
        raise KapFetchError(f"KAP'a bağlanılamadı ({url}): {exc}") from exc
    if r.status_code == 404:
        raise KapFetchError(f"KAP sayfası bulunamadı (404): {url}")
    try:
        r.raise_for_status()
    except requests.HTTPError as exc:
        raise KapFetchError(f"KAP'tan hata yanıtı geldi ({r.status_code}): {url}") from exc
    return r


def fetch_fund_member_oid(slug: str) -> str:
    """`fon-bildirimleri/{slug}` sayfasını çekip fonun dahili KAP kimliğini
    (mkkMemberOid) sayfaya gömülü JSON'dan okur."""
    html = _get(f"{BASE_URL}/tr/fon-bildirimleri/{slug}").text
    normalized = html.replace('\\"', '"')
    m = re.search(r'"filterOptions"\s*:\s*\{[^}]*"mkkMemberOid"\s*:\s*"([0-9a-f]+)"', normalized)
    if not m:
        raise KapFetchError(
            f"Fonun KAP kimliği (mkkMemberOid) bulunamadı - slug yanlış olabilir: {slug}"
        )
    return m.group(1)


def fetch_portfolio_reports(member_oid: str, days: int = 365) -> list[dict]:
    """Son `days` gün içindeki "Portföy Dağılım Raporu" bildirimlerini,
    en güncelden en eskiye sıralı olarak döner - her eleman disclosureBasic
    alanlarını (disclosureIndex, publishDate, year, donem, ...) içerir."""
    url = f"{BASE_URL}/tr/api/disclosure/filter/{FUND_FILTER_KEY}/{member_oid}/{PORTFOY_DAGILIM_SUBJECT_ID}/{days}"
    try:
        data = _get(url).json()
    except ValueError as exc:
        raise KapFetchError(f"KAP bildirim listesi API'si geçersiz yanıt döndürdü: {url}") from exc

    reports = []
    for item in data or []:
        basic = item.get("disclosureBasic") or {}
        if not basic.get("disclosureIndex"):
            continue
        reports.append(basic)

    def _sort_key(b):
        try:
            return datetime.strptime(b["publishDate"], "%d.%m.%Y %H:%M:%S")
        except (KeyError, ValueError):
            return datetime.min

    reports.sort(key=_sort_key, reverse=True)
    return reports


def fetch_attachment_link(disclosure_index: int) -> tuple[str, str]:
    """`Bildirim/{disclosure_index}` sayfasından ekli dosyanın indirme
    linkini ve dosya adını okur (ilk eşleşen ek - genelde tek ek olur)."""
    html = _get(f"{BASE_URL}/tr/Bildirim/{disclosure_index}").text
    m = re.search(
        r'href="(https://www\.kap\.org\.tr/tr/api/file/download/[0-9a-f]+)"[^>]*>([^<]+)</a>',
        html,
    )
    if not m:
        raise KapFetchError(f"Bildirim #{disclosure_index} içinde ek dosya linki bulunamadı.")
    return m.group(1), m.group(2).strip()


def _normalize_cell(cell: str | None) -> str:
    return re.sub(r"\s+", " ", (cell or "")).strip()


def _parse_tr_number(text: str) -> float | None:
    text = _normalize_cell(text).replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


_TICKER_RE = re.compile(r"^[A-ZÇĞİÖŞÜ0-9]{2,10}$")
_CCY_RE = re.compile(r"^[A-Z]{2,3}$")

# "III-FON PORTFÖY DEĞERİ TABLOSU"ndaki satır/grup toplamlarının ilk
# sütununda çıkıp, ticker regex'ine de uyabilen (ör. hepsi büyük harf)
# ama gerçek bir menkul kıymet OLMAYAN etiketler.
_NON_TICKER_LABELS = {
    "GRUP", "VIOP", "FON", "TOPLAM", "GENEL", "NAKİT", "NAKIT", "REPO",
    "TERS", "VADELİ", "MEVDUAT", "HİSSE", "TÜREV", "DİĞER", "BPP", "TPP",
}


def parse_top_holdings_from_pdf(pdf_bytes: bytes, top_n: int = 10) -> list[tuple[str, float]]:
    """"III-FON PORTFÖY DEĞERİ TABLOSU"nu ayrıştırır: her menkul kıymet
    satırının "TOPLAM (FTD GÖRE)" (Toplam Fon Değerine Göre) yüzdesini
    okur, aynı kod altında birden fazla lot varsa toplar, en büyük `top_n`
    tanesini (kod, yüzde) olarak, yüzdesi en büyükten küçüğe sıralı döner.

    KAP'ın rapor PDF'i vektörel tablo çizgileri değil, düz metin
    hizalamasıyla oluşturulmuş - pdfplumber'ın grid tabanlı
    extract_tables()'ı bu yüzden hiçbir satır bulamıyor (gerçek bir KAP
    PDF'i üzerinde doğrulandı). Bunun yerine sayfa metnini satır satır
    okuyup, "KOD PB ... GRUP% FPD% FTD%" biçimindeki menkul kıymet
    satırlarını regex ile tanıyoruz - her satırın ilk iki token'ı
    (kod + para birimi) ve son üç token'ı (yüzdeler) sabit kalıyor,
    aradaki menkul kıymet unvanı/ISIN kaç satıra sarmış olursa olsun bu
    ilk satırın tamamı tek satırda kalıyor."""
    totals: dict[str, float] = {}

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.split("\n"):
                tokens = line.split()
                if len(tokens) < 4:
                    continue
                ticker = tokens[0].upper()
                if not _TICKER_RE.match(ticker) or ticker in _NON_TICKER_LABELS:
                    continue
                if not _CCY_RE.match(tokens[1]):
                    continue

                values = [_parse_tr_number(t) for t in tokens[-3:]]
                if any(v is None for v in values):
                    continue
                ftd_pct = values[-1]  # sıra: GRUP(%), TOPLAM(FPD GÖRE), TOPLAM(FTD GÖRE)
                if abs(ftd_pct) > 100:  # gerçek bir yüzde olamayacak kadar büyükse (ör. tutar sütunu) at
                    continue

                totals[ticker] = totals.get(ticker, 0.0) + ftd_pct

    if not totals:
        raise KapFetchError(
            "PDF içinde 'III-FON PORTFÖY DEĞERİ TABLOSU' satırları okunamadı - rapor formatı değişmiş olabilir."
        )

    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    return [(code, round(pct, 2)) for code, pct in ranked]


def _report_meta(basic: dict) -> dict:
    publish_dt = datetime.strptime(basic["publishDate"], "%d.%m.%Y %H:%M:%S")
    year, donem = basic.get("year"), basic.get("donem")
    period_label = f"{_TR_MONTHS.get(donem, donem)} {year}" if year and donem else publish_dt.strftime("%d.%m.%Y")
    return {
        "report_date": publish_dt.strftime("%d.%m.%Y"),
        "report_date_sort": publish_dt.strftime("%Y-%m-%d"),
        "period_label": period_label,
        "disclosure_index": basic["disclosureIndex"],
    }


def find_latest_report_meta(fund_code: str, fund_name: str, days: int = 365) -> dict:
    """Sadece en güncel Portföy Dağılım Raporu'nun tarih/kimlik bilgisini
    döner (PDF'i indirmeden) - çağıran taraf bu tarihi zaten önbellekte
    olanla karşılaştırıp, gerçekten yeni bir rapor varsa
    fetch_holdings_for_report ile PDF'i indirip ayrıştırabilir."""
    slug = build_fund_slug(fund_code, fund_name)
    member_oid = fetch_fund_member_oid(slug)
    reports = fetch_portfolio_reports(member_oid, days=days)
    if not reports:
        raise KapFetchError(
            f"Son {days} gün içinde '{fund_code}' için Portföy Dağılım Raporu bulunamadı."
        )
    return _report_meta(reports[0])


def fetch_holdings_for_report(disclosure_index: int, top_n: int = 10) -> list[tuple[str, float]]:
    """Verilen bildirimin ekli PDF'ini indirip en büyük `top_n` yatırım
    aracını (kod, yüzde) olarak döner."""
    download_url, _filename = fetch_attachment_link(disclosure_index)
    pdf_bytes = _get(download_url).content
    return parse_top_holdings_from_pdf(pdf_bytes, top_n=top_n)


def get_latest_top_holdings(fund_code: str, fund_name: str, top_n: int = 10, days: int = 365) -> dict:
    """Uçtan uca: fon kodu+unvanından KAP raporunu bulur, indirir, ayrıştırır.

    Döner: {"report_date": "02.09.2026", "report_date_sort": "2026-09-02",
    "period_label": "Ağustos 2026", "disclosure_index": 1657113,
    "holdings": [("AKBNK", 3.99), ...]} - bulunamazsa/ayrıştırılamazsa
    KapFetchError fırlatır.
    """
    meta = find_latest_report_meta(fund_code, fund_name, days=days)
    meta["holdings"] = fetch_holdings_for_report(meta["disclosure_index"], top_n=top_n)
    return meta
