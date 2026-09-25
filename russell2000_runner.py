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

VERİ KAYNAĞI - üç denemeden sonra bulundu:
  1. iShares'in .ajax CSV export'una düz `requests` ile gidildi - link
     tarayıcıda JavaScript ile kuruluyor, statik HTML'de hiç bulunamadı.
  2. stockanalysis.com'un dahili __data.json'u denendi - format çalıştı
     ama ücretsiz görünüm sadece ilk ~25 holdings'i veriyor (1989'un tamamı
     ücretli katmanda).
  3. Playwright ile gerçek bir Chromium açılıp sayfa JS'iyle render edildi,
     ama CSV linki DOM'da/ağ trafiğinde hâlâ yoktu - "Holdings" sekmesine
     TIKLANDIĞINDA ise sayfanın kendisinin kullandığı resmi bir JSON API'ye
     (varnish-api/.../get-product-data?...component=holdings.all...)
     istek atıldığı görüldü. Bu script artık O isteği (page.expect_response
     ile) bekleyip yanıtını doğrudan parse ediyor - CSV/HTML export'una hiç
     gerek yok.
Playwright + Chromium binary'si requirements.txt'e EKLENMEDİ (sadece bu
workflow'a özel, ana Streamlit uygulamasının/diğer workflow'ların pip
install'unu gereksiz yere ağırlaştırmasın diye) - bkz. .github/workflows/
update_russell2000.yml'nin kendi pip install adımları."""

import re
import sys
from datetime import datetime

from playwright.sync_api import sync_playwright

from config import load_ticker_lists, save_ticker_lists

USERNAME = "berkakar"

IWM_PRODUCT_PAGE_URL = "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf"
_TICKER_KEYS = ("ticker", "symbol", "secticker")

# Gerçek Russell 2000 endeksi ~1950-2050 arası bileşenden oluşur (yıl içinde
# küçük dalgalanmalarla). Parse hatalı/eksik/bozuk giderse (ör. iShares JSON
# şemasını değiştirirse) bu aralığın çok dışında bir sayı üretir - böyle bir
# durumda var olan listeyi SESSİZCE bozuk bir veriyle EZMEMEK için işlem
# durdurulur (bkz. aşağısı).
MIN_EXPECTED_COUNT = 1500
MAX_EXPECTED_COUNT = 2300


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def fetch_holdings_json() -> dict:
    """iShares'in Holdings sekmesi tıklandığında kendi JS'inin çağırdığı resmi
    get-product-data API'sinin yanıtını döner. URL'nin tam sorgu parametreleri
    (ör. asOfDate) sabit kodlanmıyor - sayfanın KENDİSİ doğru değerlerle
    isteği atıyor, biz sadece o isteğin yanıtını bekleyip yakalıyoruz."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ))
            page.goto(IWM_PRODUCT_PAGE_URL, wait_until="load", timeout=60000)
            page.wait_for_timeout(2000)

            holdings_tab = page.get_by_text(re.compile("holdings", re.IGNORECASE)).first
            if holdings_tab.count() == 0:
                raise RuntimeError("'Holdings' metinli bir sekme/eleman bulunamadı - sayfa yapısı değişmiş olabilir.")

            log("'Holdings' sekmesine tıklanıyor, get-product-data (holdings) yanıtı bekleniyor...")
            with page.expect_response(
                lambda r: "get-product-data" in r.url and "holdings" in r.url.lower(), timeout=20000,
            ) as response_info:
                holdings_tab.click(timeout=5000)
            resp = response_info.value
            log(f"get-product-data yanıtı: HTTP {resp.status}, Content-Type: {resp.headers.get('content-type')}, URL: {resp.url}")
            if resp.status != 200:
                raise RuntimeError(f"get-product-data isteği HTTP {resp.status} döndü.")
            return resp.json()
        finally:
            browser.close()


def _find_holdings_list(obj) -> list | None:
    """iShares'in JSON şemasını tam bilmediğimiz için (dokümante değil, sayfa
    JS'inin kendi iç veri modeli) belirli bir path yerine İÇERİĞE göre
    arıyoruz: her elemanı 'ticker'/'symbol' benzeri bir alan taşıyan dict
    olan, en UZUN listeyi buluyoruz - bu neredeyse kesin asıl holdings
    tablosudur (diğer küçük listeler filtre/konfigürasyon meta verisidir)."""
    best: list | None = None

    def walk(o):
        nonlocal best
        if isinstance(o, list):
            if o and all(isinstance(e, dict) for e in o):
                sample_keys = {k.lower() for e in o[:5] for k in e.keys()}
                if any(k in sample_keys for k in _TICKER_KEYS) and (best is None or len(o) > len(best)):
                    best = o
            for e in o:
                walk(e)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)

    walk(obj)
    return best


def parse_equity_tickers(payload: dict) -> list[str]:
    holdings = _find_holdings_list(payload)
    if holdings is None:
        raise RuntimeError(
            "Yanıtta ticker/symbol alanı taşıyan bir liste bulunamadı - "
            "iShares'in get-product-data JSON şeması değişmiş olabilir."
        )
    log(f"Ticker alanlı en uzun liste bulundu: {len(holdings)} satır, örnek anahtarlar: {sorted(holdings[0].keys())!r}")

    tickers = set()
    for row in holdings:
        raw = None
        for key in row:
            if key.lower() in _TICKER_KEYS and row[key]:
                raw = str(row[key]).strip()
                break
        if not raw:
            continue
        raw = raw.lstrip("$").upper()
        if not raw or raw == "-" or " " in raw or len(raw) > 6:
            continue  # boş/nakit satırı vb.
        tickers.add(raw)
    return sorted(tickers)


def run_once() -> None:
    log("Playwright ile iShares IWM holdings (get-product-data API) çekiliyor...")
    payload = fetch_holdings_json()
    tickers = parse_equity_tickers(payload)
    log(f"{len(tickers)} sembol ayrıştırıldı.")

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
