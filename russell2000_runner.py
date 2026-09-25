"""Russell 2000 hisse listesini (config.DEFAULT_RUSSELL_2000, en fazla ~80
sembollük elle derlenmiş bir başlangıç seti - bkz. o sabitin üstündeki not)
iShares'in Russell 2000'i birebir izleyen IWM ETF'inin resmi, herkese açık
CSV export'undaki GERÇEK ~2000 bileşenle değiştirip
custom_tickers_<kullanıcı>.json'a kalıcı olarak yazan, ayda 1 (+ istenirse
elle) çalışan GitHub Actions script'i.

Bu script Alpaca'ya HİÇ bağlanmaz (kimlik doğrulama gerekmez) - sadece
config.py'nin zaten kullandığı yerel dosya + (varsa) GitHub API yazma
yolunu (config.save_ticker_lists) kullanır. Diğer runner script'lerinin
(relative_strength_runner.py vb.) aksine tek kullanıcıya özel bir alım/satım
pipeline'ı değil, TÜM kullanıcıların paylaştığı (config.GITHUB_REPO'daki)
ortak bir referans listeyi günceller - o yüzden USERNAME burada "hangi
hesap için işlem yapılıyor" değil, "bu listeyi hangi custom_tickers_*.json
dosyasına yazacağız" anlamına gelir (bkz. app.py'de her kullanıcının kendi
custom_tickers_<username>.json'u olması)."""

import csv
import re
import sys
from datetime import datetime

import requests

from config import load_ticker_lists, save_ticker_lists

USERNAME = "berkakar"

# iShares'in herkese açık, kimlik doğrulama gerektirmeyen fon-bileşenleri CSV
# export'u - IWM (iShares Russell 2000 ETF) Russell 2000 endeksini birebir
# izlediği için endeksin kendisi yerine bu ETF'in güncel bileşen listesi
# kullanılıyor (endeksin resmi bileşen listesi FTSE Russell'da ücretli).
#
# Bu URL'nin ".ajax" öncesindeki sayısal kısmı iShares'in CMS'inde bir
# içerik kimliği - sabit değil, onlar sayfayı yeniden yayınladığında
# değişebiliyor (ilk gerçek koşuda tam olarak bu oldu: sabit kodlanmış eski
# kimlik artık CSV yerine ürün sayfasının kendisini döndürüyordu). Bu yüzden
# HER ÇALIŞTIRMADA önce ürün sayfasının HTML'i taranıp güncel indirme linki
# bulunuyor (bkz. fetch_iwm_holdings_csv) - bu sabit sadece o keşif
# başarısız olursa son çare (fallback) olarak kullanılıyor.
IWM_PRODUCT_PAGE_URL = "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf"
IWM_HOLDINGS_CSV_URL = (
    f"{IWM_PRODUCT_PAGE_URL}/1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund"
)
_CSV_LINK_PATTERN = re.compile(r'(/us/products/239710/[\w-]+/\d+\.ajax\?fileType=csv[^"\'\\\s]*)', re.IGNORECASE)

# Gerçek Russell 2000 endeksi ~1950-2050 arası bileşenden oluşur (yıl içinde
# küçük dalgalanmalarla). Parse hatalı/eksik/bozuk giderse (ör. iShares CSV
# formatını değiştirirse) bu aralığın çok dışında bir sayı üretir - böyle bir
# durumda var olan listeyi SESSİZCE bozuk bir veriyle EZMEMEK için işlem
# durdurulur (bkz. aşağısı).
MIN_EXPECTED_COUNT = 1500
MAX_EXPECTED_COUNT = 2300


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def fetch_iwm_holdings_csv() -> str:
    # İlk denemede sade bir `requests.get` + User-Agent, sunucudan HTTP 200
    # ve (yanlışlıkla) "Content-Type: text/csv" başlığıyla birlikte GERÇEK
    # bir HTML sayfası döndürdü (CSV değil) - muhtemelen ürün sayfasını önce
    # ziyaret etmeden doğrudan AJAX endpoint'ine gidildiğinde çerez/Referer
    # eksikliğinden kaynaklı bir fallback. Bu yüzden gerçek bir tarayıcı gibi
    # önce ürün sayfasını ziyaret edip çerezleri alıyoruz, sonra CSV'yi O
    # sayfayı Referer göstererek istiyoruz.
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    })
    page_resp = session.get(IWM_PRODUCT_PAGE_URL, timeout=30)
    log(f"Ürün sayfası: HTTP {page_resp.status_code}, {len(page_resp.content)} bytes, {len(session.cookies)} çerez alındı.")

    link_match = _CSV_LINK_PATTERN.search(page_resp.text)
    if link_match:
        csv_url = "https://www.ishares.com" + link_match.group(1)
        log(f"Ürün sayfasından güncel CSV linki bulundu: {csv_url}")
    else:
        csv_url = IWM_HOLDINGS_CSV_URL
        log(f"Ürün sayfasında CSV linki bulunamadı, sabit kodlanmış son çare URL kullanılıyor: {csv_url}")

    resp = session.get(
        csv_url, timeout=30,
        headers={"Accept": "text/csv,*/*", "Referer": IWM_PRODUCT_PAGE_URL},
    )
    log(f"CSV: HTTP {resp.status_code}, final URL: {resp.url}, Content-Type: {resp.headers.get('Content-Type')}, {len(resp.content)} bytes")
    resp.raise_for_status()

    if resp.text.lstrip().startswith("<"):
        title_match = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else "(başlık bulunamadı)"
        log(f"Yanıt CSV değil, HTML - <title>: {title!r}. İlk 1500 karakter: {resp.text[:1500]!r}")

    return resp.text


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
    log("iShares IWM holdings CSV çekiliyor...")
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
