"""Thin client for TEFAS's public fund-info API (no auth required).

Endpoint, request body shape and field mapping reverse-engineered from
mirzazad/pytefas (MIT-licensed, https://github.com/mirzazad/pytefas) -
written standalone here rather than depending on that package because we
need every "YAT" fund's data to classify by title client-side (see
tefas_fonlari_data.classify_fund), which pytefas's fetch() doesn't expose
a way to do in bulk beyond its own "kind" (fund vehicle type) filter.

TEFAS enforces a 6 requests/minute limit; _post_with_retry backs off on 429
and transient network errors, and fetch_fund_info paces successive chunk
requests to stay under it.
"""
import time
from datetime import date, timedelta

import requests

INFO_URL = "https://www.tefas.gov.tr/api/funds/fonGnlBlgSiraliGetir"
MAX_DAYS_PER_REQUEST = 28  # TEFAS caps a single request to ~1 month
CHUNK_PAUSE_SECONDS = 11  # keeps multi-chunk backfills under 6 req/min

HEADERS = {
    "Accept": "*/*",
    "Content-Type": "application/json",
    "Origin": "https://www.tefas.gov.tr",
    "Referer": "https://www.tefas.gov.tr/tr/fon-verileri",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
}

# TEFAS'ın kısa alan adları -> okunabilir alan adları (fonGnlBlgSiraliGetir)
INFO_FIELDS = {
    "fonKodu": "fund_code",
    "fonUnvan": "fund_name",
    "tarih": "date",
    "fiyat": "price",
    "tedPaySayisi": "shares_outstanding",
    "kisiSayisi": "investor_count",
    "portfoyBuyukluk": "portfolio_size",
}

# TEFAS tatil/hafta sonu için veri yoksa bu ifadeleri içeren bir hata mesajı
# dönebilir (ör. "Index 0 out of bounds for length 0") - gerçek hata değil.
_EMPTY_MARKERS = ("out of bounds", "veri bulunamadı")


def _post_with_retry(session: requests.Session, body: dict, max_retry: int = 5) -> dict:
    last_exc = None
    for attempt in range(max_retry):
        try:
            r = session.post(INFO_URL, headers=HEADERS, json=body, timeout=60)
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as exc:
            last_exc = exc
            time.sleep(min(2 ** attempt, 30))
            continue

        if r.status_code == 429:
            reset = r.headers.get("ratelimit-reset")
            time.sleep(int(reset) + 1 if reset and reset.isdigit() else 30)
            continue

        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            time.sleep(15)
            continue

    raise RuntimeError(f"TEFAS API {max_retry} denemeden sonra yanıt vermedi") from last_exc


def _fetch_chunk(session: requests.Session, start: date, end: date, kind: str) -> list[dict]:
    body = {
        "fonTipi": kind,
        "fonKodu": None,
        "aramaMetni": None,
        "fonTurKod": None,
        "fonGrubu": None,
        "sfonTurKod": None,
        "fonTurAciklama": None,
        "kurucuKod": None,
        "basTarih": start.strftime("%Y%m%d"),
        "bitTarih": end.strftime("%Y%m%d"),
        "basSira": 1,
        "bitSira": 100000,
        "dil": "TR",
        "sFonTurKod": "",
        "fonKod": "",
        "fonGrup": "",
        "fonUnvanTip": "",
    }
    data = _post_with_retry(session, body)

    err_code = data.get("errorCode")
    err_msg = (data.get("errorMessage") or "")
    is_empty_marker = any(m in err_msg.lower() for m in _EMPTY_MARKERS)
    if (err_code or err_msg) and not is_empty_marker:
        raise RuntimeError(f"TEFAS API hatası: {err_msg} (kod: {err_code})")

    rows = data.get("resultList") or []
    parsed = []
    for row in rows:
        parsed.append({name: row.get(short) for short, name in INFO_FIELDS.items()})
    return parsed


def _fetch_fund_rows(code: str, lookback_days: int) -> list[dict]:
    """Tek bir fon kodu için son `lookback_days` içindeki günlük satırları
    (tarihe göre eskiden yeniye sıralı) döner - fetch_fund_by_code ve
    fetch_fund_daily_change_pct'nin paylaştığı istek mantığı."""
    session = requests.Session()
    end = date.today()
    start = end - timedelta(days=lookback_days)
    body = {
        "fonTipi": "YAT",
        "fonKodu": code,
        "aramaMetni": None,
        "fonTurKod": None,
        "fonGrubu": None,
        "sfonTurKod": None,
        "fonTurAciklama": None,
        "kurucuKod": None,
        "basTarih": start.strftime("%Y%m%d"),
        "bitTarih": end.strftime("%Y%m%d"),
        "basSira": 1,
        "bitSira": 100,
        "dil": "TR",
        "sFonTurKod": "",
        "fonKod": code,
        "fonGrup": "",
        "fonUnvanTip": "",
    }
    data = _post_with_retry(session, body)

    err_code = data.get("errorCode")
    err_msg = (data.get("errorMessage") or "")
    is_empty_marker = any(m in err_msg.lower() for m in _EMPTY_MARKERS)
    if (err_code or err_msg) and not is_empty_marker:
        raise RuntimeError(f"TEFAS API hatası: {err_msg} (kod: {err_code})")

    rows = data.get("resultList") or []
    parsed = [{name: row.get(short) for short, name in INFO_FIELDS.items()} for row in rows]
    parsed.sort(key=lambda r: str(r.get("date") or ""))
    return parsed


def fetch_fund_by_code(code: str, lookback_days: int = 10) -> dict | None:
    """Verilen fon kodunun en güncel fiyat/unvan bilgisini döner (fon yoksa None).
    Hafta sonu/resmi tatil günlerini atlayabilmek için son `lookback_days` günü
    tarar, en güncel tarihli satırı döner - aşağıdaki toplu fetch_fund_info'nun
    aksine (tüm YAT fonları) tek bir fonu (fonKodu filtresiyle) hedefler."""
    rows = _fetch_fund_rows(code, lookback_days)
    return rows[-1] if rows else None


def fetch_fund_daily_change_pct(code: str, lookback_days: int = 10) -> float | None:
    """Verilen TEFAS fon kodunun günlük (bir önceki güne göre) fiyat değişim
    yüzdesini döner - fon bulunamazsa veya en az 2 günlük fiyat verisi yoksa
    None döner. fon_hisse_uyari.py'de, bir fonun portföyündeki "en büyük
    yatırım aracı" aslında BIST hissesi değil de başka bir TEFAS fonuysa
    (ör. bir fon-içinde-fon pozisyonu) yfinance'e yedek olarak kullanılır."""
    rows = _fetch_fund_rows(code, lookback_days)
    if len(rows) < 2:
        return None
    prev_price, last_price = rows[-2].get("price"), rows[-1].get("price")
    if not prev_price or not last_price:
        return None
    return round((float(last_price) - float(prev_price)) / float(prev_price) * 100, 2)


def fetch_fund_info(start: date, end: date, kind: str = "YAT") -> list[dict]:
    """start..end (dahil) arasındaki tüm fonların günlük fiyat/büyüklük
    verisini döner - her eleman bir (fon, tarih) çiftini temsil eder.

    TEFAS'ın tek istekte ~1 aylık sınırı burada otomatik parçalara bölünür,
    parçalar arası kısa bir bekleme ile (6 istek/dk limitini aşmamak için).
    """
    session = requests.Session()
    chunks = []
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=MAX_DAYS_PER_REQUEST - 1), end)
        chunks.append((cur, chunk_end))
        cur = chunk_end + timedelta(days=1)

    all_rows = []
    for i, (c_start, c_end) in enumerate(chunks):
        if i > 0:
            time.sleep(CHUNK_PAUSE_SECONDS)
        all_rows.extend(_fetch_chunk(session, c_start, c_end, kind))
    return all_rows
