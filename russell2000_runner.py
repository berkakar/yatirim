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
custom_tickers_<username>.json'u olması)."""

import sys
from datetime import datetime

import requests

from config import load_ticker_lists, save_ticker_lists

USERNAME = "berkakar"

# İlk yaklaşım (iShares'in .ajax CSV export'u) çalışmadı: o endpoint artık
# CSV yerine ürün sayfasının kendisini döndürüyor - indirme linki tarayıcıda
# JavaScript ile kuruluyor, düz bir HTTP isteğiyle statik HTML'de bulunamıyor
# (bkz. bu dosyanın git geçmişindeki önceki denemeler).
#
# Bunun yerine stockanalysis.com'un ETF holdings sayfalarının kullandığı
# SvelteKit "__data.json" endpoint'i kullanılıyor - bu, sayfa JS'inin
# kendisinin veri çekmek için kullandığı, düz JSON dönen dahili bir uç nokta
# (tarayıcı gerektirmez). IWM (iShares Russell 2000 ETF) yine referans -
# Russell 2000 endeksini birebir izlediği için bu ETF'in bileşen listesi
# endeksin kendisi yerine kullanılıyor (endeksin resmi listesi FTSE
# Russell'da ücretli). Format "devalue" (SvelteKit'in JSON.stringify
# yerine kullandığı serileştirme biçimi) - deref() bunu çözüyor.
HOLDINGS_DATA_JSON_URL = "https://stockanalysis.com/etf/iwm/holdings/__data.json?x-sveltekit-trailing-slash=1"

# Gerçek Russell 2000 endeksi ~1950-2050 arası bileşenden oluşur (yıl içinde
# küçük dalgalanmalarla). Parse hatalı/eksik/bozuk giderse (ör. stockanalysis.
# com sayfa formatını değiştirirse) bu aralığın çok dışında bir sayı üretir -
# böyle bir durumda var olan listeyi SESSİZCE bozuk bir veriyle EZMEMEK için
# işlem durdurulur (bkz. aşağısı).
MIN_EXPECTED_COUNT = 1500
MAX_EXPECTED_COUNT = 2300


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def deref(idx, data: list, _depth: int = 0):
    """SvelteKit'in "devalue" serileştirme biçimini çözer: `data` düz bir
    dizi, her eleman ya bir sözlük (değerleri KENDİ İÇİNDE `data`'ya birer
    indeks olan bir şablon), ya bir liste (yine `data`'ya indeksler), ya da
    doğrudan bir ilkel (str/int/float/bool/None) - -1 evrensel null'u temsil
    eder. Bkz. https://github.com/sveltejs/devalue (SvelteKit'in sayfa
    verisini JSON.stringify yerine bununla kodluyor)."""
    if _depth > 30 or not isinstance(idx, int):
        return idx
    if idx < 0 or idx >= len(data):
        return None
    v = data[idx]
    if isinstance(v, dict):
        return {k: deref(vi, data, _depth + 1) for k, vi in v.items()}
    if isinstance(v, list):
        return [deref(i, data, _depth + 1) for i in v]
    return v


def fetch_holdings_json() -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
    resp = requests.get(HOLDINGS_DATA_JSON_URL, headers=headers, timeout=30)
    log(f"HTTP {resp.status_code}, Content-Type: {resp.headers.get('Content-Type')}, {len(resp.content)} bytes")
    resp.raise_for_status()
    return resp.json()


def parse_equity_tickers(payload: dict) -> list[str]:
    """SvelteKit yanıtı birkaç "node" içerir (oturum/kullanıcı bilgisi,
    hisse/ETF meta verisi, sayfaya özel veri) - "holdings" alanını taşıyan
    node'u arıyoruz, sayfanın node sırasını/sayısını değiştirmesine karşı
    dayanıklı olsun diye pozisyona göre değil İÇERİĞE göre buluyoruz. Ticker
    alanı ("s") "$AAPL" gibi baştan $ işaretli geliyor."""
    holdings = None
    for node in payload.get("nodes", []):
        if node.get("type") == "skip":
            continue
        node_data = node.get("data") or []
        if not node_data:
            continue
        parsed = deref(0, node_data)
        if isinstance(parsed, dict) and isinstance(parsed.get("holdings"), list):
            holdings = parsed["holdings"]
            break

    if holdings is None:
        raise RuntimeError(
            "Yanıttaki node'ların hiçbirinde 'holdings' alanı bulunamadı - "
            "stockanalysis.com'un sayfa/veri formatı değişmiş olabilir."
        )

    tickers = set()
    for row in holdings:
        raw = (row.get("s") or "").strip().lstrip("$")
        if not raw or raw == "-" or " " in raw or len(raw) > 6:
            continue  # boş/nakit satırı vb.
        tickers.add(raw.upper())
    return sorted(tickers)


def run_once() -> None:
    log("stockanalysis.com'dan IWM (Russell 2000) holdings verisi çekiliyor...")
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
