"""Alpaca OHLCV bar önbelleği - alpaca_buy_points.py ve alpaca_trailing_stop.py
(GitHub Actions'ta ~5-10 dakikada bir sıfırdan başlayan, hiçbir işlem
belleği olmayan cron script'leri) tarafından paylaşılır.

Bir bar kendi periyodu kapanana kadar değişmez ve kapandıktan sonra da hiç
değişmez - ama her pass bunu bilmediği için, aynı 60-400 günlük pencereyi
Alpaca'dan sıfırdan yeniden çekiyordu. get_cached_raw_bars bunun yerine, bu
JSON dosyasında biriken önceki sonuçtan devam ederek sadece son
cache'lenen bar'ın kendi zaman damgasından itibaren (o bar son çekildiğinde
hâlâ oluşum halinde olabileceği için inclusive) Alpaca'ya gider.

Aynı sembol+timeframe kombinasyonuna farklı çağıranlar (ör. alpaca_buy_points.py
sabit LOOKBACK_DAYS, alpaca_trailing_stop.py pozisyona özel management_start)
farklı genişlikte pencere isteyebilir - "retain_from" bu ikisinin GÖRÜLMÜŞ EN
GENİŞ (en eskiye giden) isteğini saklar, böylece bir çağıranın dar isteği
diğerinin ihtiyaç duyduğu eski bar'ları önbellekten silmez. Bir çağıranın
istediği pencere önbellekte tutulandan daha eskiye gidiyorsa (ör. yeni açılan
ve LOOKBACK_DAYS'ten daha eski bir pozisyon), eksik geçmiş yeniden çekilir.
Her çağıran, dönen listeyi kendi `window_start`'ına göre süzer (bu modül
sadece önbelleği YÖNETİR, bir üst pencereden fazlasını göstermeyi çağırana
bırakır)."""

import json
import os
from datetime import datetime, timedelta, timezone

from alpaca_client import AlpacaClient

DAILY_BARS_CACHE_PATH = "alpaca_daily_bars_cache_berkakar.json"
INTRADAY_BARS_CACHE_PATH = "alpaca_intraday_bars_cache_berkakar.json"

_BAR_FIELDS = ("o", "h", "l", "c", "v")

# retain_from, görülen en geniş isteğe göre geriye doğru büyüyebiliyor (ör.
# çok uzun süredir açık bir pozisyonun management_start'ı) ama hiçbir çağıran
# tarafından asla küçültülmüyor - pozisyon kapansa bile. Bu üst sınır
# olmadan, tek bir aşırı uzun pozisyon o sembol+timeframe'in cache dosyasını
# süresiz büyütebilirdi. 400 gün, sistemdeki en geniş sabit pencereyle
# (DAILY_LOOKBACK_DAYS) eşleşiyor - bunun ötesi zaten hiçbir algoritma
# tarafından kullanılmıyor.
MAX_RETENTION_DAYS = 400


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _load(cache_file: str) -> dict:
    if not os.path.exists(cache_file):
        return {"series": {}}
    with open(cache_file, encoding="utf-8") as f:
        return json.load(f)


def _save(cache_file: str, cache: dict) -> None:
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def get_cached_raw_bars(
    client: AlpacaClient, cache_file: str, symbol: str, timeframe: str, window_start: datetime,
) -> list[dict]:
    """symbol+timeframe için `window_start`'tan (en azından) bugüne kadarki ham
    bar listesini (Alpaca'nın /bars formatında, dict) döner - cache_file'daki
    önceki sonuçtan devam ederek sadece eksik/oluşum-halindeki kısmı Alpaca'dan
    çeker. cache_file'ı kalıcı olarak günceller (çağıran taraf, GitHub Actions
    workflow'unda commit+push eder)."""
    cache = _load(cache_file)
    key = f"{symbol}:{timeframe}"
    series = cache["series"].setdefault(key, {"bars": {}, "retain_from": None})
    bars_by_ts: dict[str, dict] = series["bars"]

    prior_retain_from = parse_iso(series["retain_from"]) if series.get("retain_from") else None
    retain_from = min(window_start, prior_retain_from) if prior_retain_from else window_start
    retain_from = max(retain_from, datetime.now(timezone.utc) - timedelta(days=MAX_RETENTION_DAYS))

    if not bars_by_ts:
        fetch_start = retain_from
    else:
        earliest_cached = min(bars_by_ts.keys(), key=parse_iso)
        latest_cached = max(bars_by_ts.keys(), key=parse_iso)
        if retain_from < parse_iso(earliest_cached):
            # Önbellekte bugüne kadar hiç istenmemiş kadar geriye gidiliyor
            # (ör. yeni bir pozisyon açıldı ve management_start, şimdiye kadar
            # cache'lenen en eski bar'dan daha eskiye dayanıyor) - eksik
            # geçmişi de çekmek için baştan başla.
            fetch_start = retain_from
        else:
            fetch_start = parse_iso(latest_cached)

    fetched = client.get_raw_bars(symbol, timeframe, fetch_start.isoformat())
    for b in fetched:
        bars_by_ts[b["t"]] = {k: b[k] for k in _BAR_FIELDS}

    for ts in [t for t in bars_by_ts if parse_iso(t) < retain_from]:
        del bars_by_ts[ts]

    series["retain_from"] = retain_from.isoformat()
    cache.setdefault("meta", {})["last_updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save(cache_file, cache)

    return [{"t": t, **bars_by_ts[t]} for t in sorted(bars_by_ts.keys(), key=parse_iso)]
