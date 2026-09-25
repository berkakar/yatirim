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
from collections import Counter
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


def _iter_holdings_click_candidates(page):
    """"Holdings" sekmesini bulmak için önce en kesin adaylardan (ARIA tab/
    link/button rolü + TAM "Holdings" adı) başlar, bulunamazsa daha gevşek
    bir metin eşleşmesine düşer. DOM'da "holdings" geçen İLK eleman her
    zaman doğru tıklanabilir sekme OLMUYOR (bkz. bu dosyanın git geçmişi -
    get_by_text(...).first bir koşuda işe yaradı, bir sonrakinde 20 saniye
    boyunca hiçbir isteği tetiklemedi) - bu yüzden artık BİRDEN FAZLA aday
    sırayla denenir (bkz. fetch_holdings_json)."""
    name_re = re.compile(r"^\s*holdings\s*$", re.IGNORECASE)
    for role in ("tab", "link", "button"):
        loc = page.get_by_role(role, name=name_re)
        for i in range(loc.count()):
            yield loc.nth(i)
    loose = page.get_by_text(re.compile("holdings", re.IGNORECASE))
    for i in range(min(loose.count(), 8)):  # sonsuz denemeye karşı üst sınır
        yield loose.nth(i)


def fetch_holdings_json() -> dict:
    """iShares'in Holdings sekmesi tıklandığında kendi JS'inin çağırdığı resmi
    get-product-data API'sinin yanıtını döner. URL'nin tam sorgu parametreleri
    (ör. asOfDate) sabit kodlanmıyor - sayfanın KENDİSİ doğru değerlerle
    isteği atıyor, biz sadece sayfa yüklenirken/dolaşılırken atılan istekleri
    yakalayıp içinden bu isteği buluyoruz."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ))
            seen_requests: list[str] = []
            page.on("request", lambda req: seen_requests.append(req.url))

            page.goto(IWM_PRODUCT_PAGE_URL, wait_until="load", timeout=60000)
            page.wait_for_timeout(2000)

            def find_captured_url() -> str | None:
                for u in seen_requests:
                    if "get-product-data" in u and "holdings" in u.lower():
                        return u
                return None

            api_url = find_captured_url()
            if api_url is None:
                candidates = list(_iter_holdings_click_candidates(page))
                log(f"{len(candidates)} olası 'Holdings' tıklama adayı bulundu, sırayla deneniyor...")
                for i, candidate in enumerate(candidates):
                    try:
                        candidate.click(timeout=5000)
                    except Exception as e:
                        log(f"Aday {i + 1}/{len(candidates)}: tıklanamadı ({e}), sıradaki deneniyor...")
                        continue
                    page.wait_for_timeout(2500)
                    api_url = find_captured_url()
                    if api_url:
                        log(f"Aday {i + 1}/{len(candidates)} tıklaması get-product-data isteğini tetikledi: {api_url}")
                        break
                    log(f"Aday {i + 1}/{len(candidates)} tıklandı ama get-product-data isteği görülmedi, sıradaki deneniyor...")

            if api_url is None:
                interesting = [
                    u for u in seen_requests
                    if re.search(r"csv|xls|download|export|holdings|product-data", u, re.IGNORECASE)
                ]
                raise RuntimeError(
                    f"get-product-data isteği hiçbir denemede yakalanamadı. {len(seen_requests)} istek görüldü, "
                    f"ilgili görünenler: {interesting[:20]!r}"
                )

            resp = page.request.get(api_url)
            log(f"get-product-data yanıtı: HTTP {resp.status}, Content-Type: {resp.headers.get('content-type')}, {len(resp.body())} bytes")
            if resp.status != 200:
                raise RuntimeError(f"get-product-data isteği HTTP {resp.status} döndü.")
            return resp.json()
        finally:
            browser.close()


def _find_holdings_table(obj) -> dict[str, list] | None:
    """iShares'in JSON şemasını tam bilmediğimiz için (dokümante değil, sayfa
    JS'inin kendi iç veri modeli) belirli bir path yerine İÇERİĞE göre
    arıyoruz - VE şeklin SATIR-bazlı (dict listesi, [{"ticker": "A", ...},
    {"ticker": "B", ...}]) mı yoksa SÜTUN-bazlı (her alan kendi değer
    listesi, {"ticker": ["A", "B", ...], "weight": [...]}) mı olduğunu da
    bilmiyoruz - ikisini de {sütun_adı: [değerler]} biçimine normalize edip
    en çok satırlı olanı döner (get-product-data yanıtı zaten sadece
    holdings'e - component=holdings.all - odaklı olduğu için bu neredeyse
    kesin asıl tablodur)."""
    best: dict[str, list] | None = None
    best_len = 0

    def consider(table: dict[str, list], length: int):
        nonlocal best, best_len
        if length >= 10 and length > best_len:
            best, best_len = table, length

    def walk(o):
        if isinstance(o, list):
            if o and all(isinstance(e, dict) for e in o):
                keys = {k for e in o for k in e.keys()}
                consider({k: [e.get(k) for e in o] for k in keys}, len(o))
            for e in o:
                walk(e)
        elif isinstance(o, dict):
            list_cols = {k: v for k, v in o.items() if isinstance(v, list)}
            if len(list_cols) >= 2:
                # TÜM sütunların TAM AYNI uzunlukta olmasını şart koşmuyoruz -
                # iShares'te bazı yardımcı/opsiyonel alanlar farklı uzunlukta
                # olabiliyor (ör. tek bir metadata listesi), bu tek bir farklı
                # uzunluk tüm tabloyu elememeli. Çoğunluk uzunluğa (en az 2
                # sütun paylaşıyorsa) sahip sütunları tabloya alıyoruz.
                length_counts = Counter(len(v) for v in list_cols.values())
                modal_length, modal_count = length_counts.most_common(1)[0]
                if modal_count >= 2 and modal_length >= 10:
                    consider({k: v for k, v in list_cols.items() if len(v) == modal_length}, modal_length)
            for v in o.values():
                walk(v)

    walk(obj)
    return best


def _unwrap_cell(v):
    """iShares bazı sütun değerlerini ham skaler yerine {"value": ...,
    "formattedValue": ...} şeklinde bir "hücre" nesnesiyle sarmalıyor (bkz.
    bu dosyanın git geçmişi - gerçek koşuda tam olarak bu şekilde bir sütun
    bulundu) - ticker eşleştirmesinden önce bunu ham değere indirger."""
    if isinstance(v, dict) and ("value" in v or "formattedValue" in v):
        raw = v.get("value")
        return raw if raw not in (None, "") else v.get("formattedValue")
    return v


_TICKER_VALUE_RE = re.compile(r"^[A-Z0-9]{1,5}([.\-][A-Z0-9]{1,3})?$")


def _guess_ticker_column(table: dict[str, list]) -> str | None:
    """Sütun adı bilinmediği (ör. holdings tablosu bulundu ama 'ticker'/
    'symbol' gibi tanıdık bir anahtarı yoksa) durumda DEĞERE göre tahmin
    eder: her sütun için örneklem değerlerinin ne kadarının "ticker gibi
    göründüğüne" (1-5 harf/rakam, büyük harf) VE ne kadar BENZERSİZ
    olduğuna bakar - ör. 'sector' gibi tekrarlayan bir sütunun yanlışlıkla
    seçilmesini böyle eleriz."""
    best_key = None
    best_score = 0.0
    for key, col in table.items():
        sample = [_unwrap_cell(v) for v in col[:200]]
        values = [str(v).strip().upper() for v in sample if v not in (None, "")]
        if len(values) < len(sample) * 0.5:
            continue  # çoğu satırda boşsa muhtemelen ticker sütunu değil
        match_ratio = sum(1 for v in values if _TICKER_VALUE_RE.match(v)) / len(values)
        uniqueness = len(set(values)) / len(values)
        if match_ratio > 0.8 and uniqueness > 0.8 and match_ratio * uniqueness > best_score:
            best_score = match_ratio * uniqueness
            best_key = key
    return best_key


def _summarize_structure(obj, depth: int = 0, max_depth: int = 3) -> str:
    """Hiçbir tablo/ticker sütunu bulunamadığında teşhis için yanıtın genel
    şeklini (dict anahtarları, liste uzunlukları) özetler - bir sonraki
    adımda tahmine değil veriye dayanılabilsin diye."""
    indent = "  " * depth
    if depth >= max_depth:
        return f"{indent}..."
    if isinstance(obj, dict):
        lines = [f"{indent}dict({len(obj)} anahtar): {sorted(obj.keys())[:15]!r}"]
        for k, v in list(obj.items())[:8]:
            lines.append(f"{indent}  .{k} -> {_summarize_structure(v, depth + 1, max_depth)}")
        return "\n".join(lines)
    if isinstance(obj, list):
        sample_type = type(obj[0]).__name__ if obj else "?"
        return f"list(len={len(obj)}, örnek eleman tipi={sample_type})"
    return repr(obj)[:80]


def parse_equity_tickers(payload: dict) -> list[str]:
    table = _find_holdings_table(payload)
    if table is None:
        raise RuntimeError(
            "Yanıtta ne satır-bazlı ne sütun-bazlı, en az 10 satırlı bir holdings tablosu "
            f"bulunamadı. Yanıt yapısı:\n{_summarize_structure(payload)}"
        )
    row_count = len(next(iter(table.values())))
    log(f"Aday holdings tablosu bulundu: {row_count} satır, sütunlar: {sorted(table.keys())!r}")

    ticker_col = None
    for key in table:
        if key.lower() in _TICKER_KEYS:
            ticker_col = key
            break
    if ticker_col is None:
        # Tanıdık bir sütun adı yoksa (bkz. bu dosyanın git geçmişi - iShares
        # "ticker"/"symbol" kullanmıyor) DEĞERE göre tahmin ediyoruz.
        ticker_col = _guess_ticker_column(table)
        if ticker_col is None:
            sample = {k: [_unwrap_cell(v) for v in vals[:5]] for k, vals in list(table.items())[:20]}
            raise RuntimeError(f"Ticker gibi görünen bir sütun bulunamadı. Örnek değerler: {sample!r}")
        log(f"Tanıdık bir sütun adı yok, değer-tabanlı tahminle '{ticker_col}' seçildi.")

    tickers = set()
    for raw in table[ticker_col]:
        raw = _unwrap_cell(raw)
        if not raw:
            continue
        raw = str(raw).strip().lstrip("$").upper()
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
