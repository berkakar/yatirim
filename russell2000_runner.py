"""Russell 2000 hisse listesini (config.DEFAULT_RUSSELL_2000, en fazla ~80
sembollük elle derlenmiş bir başlangıç seti - bkz. o sabitin üstündeki not)
IWM (iShares Russell 2000 ETF, endeksi birebir izler) bileşenlerinin
GERÇEK ~2000 sembolüyle değiştirip custom_tickers_<kullanıcı>.json'a kalıcı
olarak yazan, ayda 1 (+ istenirse elle) çalışan GitHub Actions script'i.

Bu script Alpaca'ya HİÇ bağlanmaz (kimlik doğrulama gerekmez) - sadece
config.py'nin zaten kullandığı yerel dosya + (varsa) GitHub API yazma
yolunu (config.save_ticker_lists) kullanır. Diğer runner script'lerinin
(relative_strength_runner.py vb.) aksine tek kullanıcıya özel bir alım/satım
pipeline'ı değil, TÜM kullanıcıların paylaştığı (config.GITHUB_REPO'daki)
ortak bir referans listeyi günceller - o yüzden USERNAME burada "hangi
hesap için işlem yapılıyor" değil, "bu listeyi hangi custom_tickers_*.json
dosyasına yazacağız" anlamına gelir (bkz. app.py'de her kullanıcının kendi
custom_tickers_<username>.json'u olması).

VERİ KAYNAĞI - bu iki denemeden sonra Playwright'e geçildi:
  1. iShares'in .ajax CSV export'una düz `requests` ile gidildi - link
     tarayıcıda JavaScript ile kuruluyor, statik HTML'de hiç bulunamadı.
  2. stockanalysis.com'un dahili __data.json'u denendi - format çalıştı
     ama ücretsiz görünüm sadece ilk ~25 holdings'i veriyor (1989'un tamamı
     ücretli katmanda).
Playwright GERÇEK bir Chromium açıp iShares sayfasını JS'iyle birlikte
render ediyor, sonra sayfanın (artık JS tarafından doldurulmuş) HTML'inde
CSV indirme linkini arıyor - bulamazsa indirme tetikleyen bir elemana
tıklamayı dener. requirements.txt'e EKLENMEDİ (Playwright + Chromium
binary'si sadece bu workflow'a özel, ana Streamlit uygulamasının/diğer
workflow'ların pip install'unu gereksiz yere ağırlaştırmasın diye) - bkz.
.github/workflows/update_russell2000.yml'nin kendi pip install adımları."""

import csv
import html
import re
import sys
from datetime import datetime

from playwright.sync_api import sync_playwright

from config import load_ticker_lists, save_ticker_lists

USERNAME = "berkakar"

IWM_PRODUCT_PAGE_URL = "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf"
_CSV_LINK_PATTERN = re.compile(r'(/us/products/239710/[\w-]+/\d+\.ajax\?fileType=csv[^"\'\\\s]*)', re.IGNORECASE)

# Gerçek Russell 2000 endeksi ~1950-2050 arası bileşenden oluşur (yıl içinde
# küçük dalgalanmalarla). Parse hatalı/eksik/bozuk giderse (ör. iShares sayfa
# formatını değiştirirse) bu aralığın çok dışında bir sayı üretir - böyle bir
# durumda var olan listeyi SESSİZCE bozuk bir veriyle EZMEMEK için işlem
# durdurulur (bkz. aşağısı).
MIN_EXPECTED_COUNT = 1500
MAX_EXPECTED_COUNT = 2300


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def fetch_iwm_holdings_csv() -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ))
            page.goto(IWM_PRODUCT_PAGE_URL, wait_until="load", timeout=60000)
            page.wait_for_timeout(4000)  # sayfanın JS'i indirme linkini doldursun diye küçük bir tampon
            page_html = page.content()
            log(f"Sayfa yüklendi (JS render sonrası), HTML uzunluğu: {len(page_html)}")

            link_match = _CSV_LINK_PATTERN.search(page_html)
            if link_match:
                # page.content() DOM'dan serileştirilmiş HTML döner - href'teki
                # "&" karakterleri "&amp;" olarak kodlanmış geliyor, unescape
                # edilmezse sorgu parametreleri (fileType=csv&amp;fileName=...)
                # bozuk URL'ye dönüşür.
                csv_url = "https://www.ishares.com" + html.unescape(link_match.group(1))
                log(f"JS render sonrası sayfada CSV linki bulundu: {csv_url}")
                resp = page.request.get(csv_url)
                log(f"CSV isteği: HTTP {resp.status}, Content-Type: {resp.headers.get('content-type')}, {len(resp.body())} bytes")
                if resp.status != 200:
                    raise RuntimeError(f"CSV isteği HTTP {resp.status} döndü.")
                return resp.text()

            log("JS render sonrası sayfada da CSV linki bulunamadı - olası indirme elemanlarına tıklanmaya çalışılıyor.")
            candidates = page.locator(
                'a[href*=".ajax"], a[aria-label*="download" i], a[title*="download" i], '
                'a[class*="icon-xls" i], a[class*="icon-csv" i], button[aria-label*="download" i]'
            )
            count = candidates.count()
            log(f"{count} olası indirme elemanı bulundu.")
            if count == 0:
                raise RuntimeError(
                    "Sayfada ne CSV linki ne de olası bir indirme elemanı bulunamadı - "
                    "iShares sayfa yapısını daha köklü değiştirmiş olabilir."
                )
            with page.expect_download(timeout=30000) as download_info:
                candidates.first.click()
            download = download_info.value
            with open(download.path(), encoding="utf-8") as f:
                csv_text = f.read()
            log(f"İndirme tetiklenerek CSV alındı, {len(csv_text)} karakter.")
            return csv_text
        finally:
            browser.close()


def parse_equity_tickers(csv_text: str) -> list[str]:
    """iShares'in export'u önce birkaç satır fon meta verisi (fon adı, tarih,
    vb.), sonra asıl holdings tablosunun başlık satırı ("Ticker","Name",...),
    sonra veri satırları, en sonda da bir feragatname paragrafı içerir. Asıl
    tabloyu bulmak için "Ticker" alanıyla başlayan satırı arıyoruz - iShares
    metadata satır sayısını değiştirse bile bu sağlam kalır."""
    lines = csv_text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        fields = next(csv.reader([line]), [])
        if fields and fields[0].strip() == "Ticker":
            header_idx = i
            break
    if header_idx is None:
        snippet = csv_text[:500].replace("\n", "\\n")
        raise RuntimeError(
            "CSV içinde 'Ticker' başlık satırı bulunamadı - iShares export formatı değişmiş ya da "
            f"istek engellenmiş olabilir. Yanıtın ilk 500 karakteri: {snippet!r}"
        )

    reader = csv.DictReader(lines[header_idx:])
    tickers = set()
    for row in reader:
        ticker = (row.get("Ticker") or "").strip()
        asset_class = (row.get("Asset Class") or "").strip().lower()
        if not ticker or ticker == "-" or " " in ticker or len(ticker) > 6:
            continue  # boş/nakit satırı ya da tablo sonundaki feragatname metni
        if asset_class and "equity" not in asset_class:
            continue  # nakit/türev satırları (Asset Class "Cash" vb.)
        tickers.add(ticker)
    return sorted(tickers)


def run_once() -> None:
    log("Playwright ile iShares IWM holdings CSV çekiliyor...")
    csv_text = fetch_iwm_holdings_csv()
    tickers = parse_equity_tickers(csv_text)
    log(f"{len(tickers)} equity sembolü ayrıştırıldı.")

    if not (MIN_EXPECTED_COUNT <= len(tickers) <= MAX_EXPECTED_COUNT):
        raise RuntimeError(
            f"Ayrıştırılan sembol sayısı ({len(tickers)}) beklenen aralığın "
            f"({MIN_EXPECTED_COUNT}-{MAX_EXPECTED_COUNT}) dışında - olası bozuk/eksik "
            "parse, mevcut liste korunuyor (üzerine YAZILMADI)."
        )

    ticker_lists = load_ticker_lists(USERNAME)
    ticker_lists["Russell 2000"] = tickers
    save_ticker_lists(ticker_lists, USERNAME)
    log(f"custom_tickers_{USERNAME}.json güncellendi: Russell 2000 = {len(tickers)} sembol.")


if __name__ == "__main__":
    try:
        run_once()
    except Exception as e:
        log(f"HATA: {e}")
        sys.exit(1)
