"""alpaca_client.compute_realized_loss'un ("zarar kes" devre kesici -
alpaca_buy_points.py'nin check_symbol'ü) her ~5 dakikalık pass'te, pozisyonu
olmayan HER watchlist sembolü için Alpaca'nın son 90 günlük TÜM order
history'sini yeniden çekip FIFO eşleştirmesini sıfırdan yapması yerine, bu
modül sembol başına son kontrolden bu yana gerçekleşen fill'leri artımlı
çeker ve saklar. FIFO eşleştirme (ucuz, saf Python) her seferinde küçük,
önbelleklenmiş fill listesi üzerinden yeniden yapılır - fill olmadığı sürece
sonuç zaten hep aynı - ama Alpaca'ya giden gereksiz tam-geçmiş API çağrısı
ortadan kalkar."""

import json
import os
from datetime import datetime, timedelta, timezone

from alpaca_client import AlpacaClient, _match_fifo_pnl

CACHE_PATH = "alpaca_realized_pnl_cache_berkakar.json"

_FILL_FIELDS = ("id", "side", "filled_avg_price", "filled_qty", "filled_at")


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _load() -> dict:
    if not os.path.exists(CACHE_PATH):
        return {"symbols": {}}
    with open(CACHE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save(cache: dict) -> None:
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def get_cached_realized_loss(client: AlpacaClient, symbol: str, lookback_days: int = 90) -> float:
    """compute_realized_loss ile aynı sonucu (lookback_days içindeki
    kapanmış işlemlerin toplam gerçekleşen zararı, pozitif bir sayı) döner,
    ama Alpaca'dan sadece son çağrıdan bu yana geçen fill'leri çekerek."""
    cache = _load()
    entry = cache["symbols"].setdefault(symbol, {"last_checked_at": None, "fills": []})

    after = entry["last_checked_at"] or (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    new_fills = client.get_symbol_fills_since(symbol, after)

    by_id = {f["id"]: f for f in entry["fills"]}
    for o in new_fills:
        by_id[o["id"]] = {k: o.get(k) for k in _FILL_FIELDS}

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    fills = [f for f in by_id.values() if f.get("filled_at") and _parse_iso(f["filled_at"]) >= cutoff]
    fills.sort(key=lambda f: _parse_iso(f["filled_at"]))

    entry["fills"] = fills
    entry["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    _save(cache)

    pnl, _, _, _ = _match_fifo_pnl(fills)
    return max(0.0, -pnl)
