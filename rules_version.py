"""Kural sürümü (kural dondurma) - [2026-09-28 · Öneri 6].

Neden: 2026-09-28 emir analizinde bir ayda portfolio_config 24 kez değişti,
bütçe 10k -> 100k -> 77k -> ... -> 81k gitti, genel algoritma dört kez
değişti ve strateji koduna 45 commit girdi. 17 işlem hiçbir kural setini
ölçmeye yetmedi - sonucun stratejiden mi yoksa değişikliklerden mi geldiği
anlaşılamadı.

Nasıl: Premium Buy Point portföyü her kaydedildiğinde, sonucu etkileyen
kural alanlarından bir parmak izi hesaplanır. Parmak izi değiştiyse
"rules_version_since" o ana güncellenir; değişmediyse (ör. sadece bütçe ya da
hisse ağırlığı değiştiyse) eski tarih korunur. Algo Analiz sayfası o
tarihten sonra kapanan işlemleri sayar ve MIN_TRADES_FOR_EVALUATION'a
ulaşılmadan kuralların değiştirilmemesi için uyarır.

Bütçe ve ağırlıklar bilerek parmak izine dahil değil: risk bazlı büyüklükle
(Öneri 5) artık sadece TAVAN görevi görüyorlar, işlem başına risk değişmiyor.
"""

import hashlib
import json
from datetime import datetime, timezone

MIN_TRADES_FOR_EVALUATION = 30

RULE_KEYS = (
    "algorithm", "stop_algorithm", "symbol_settings", "risk_sizing", "entry_timing",
    "stop_timeframe_mode", "stop_loss_enabled", "max_loss_pct", "top_up_stop_mode",
    "buy_stop_rebuy_enabled", "buy_stop_rebuy_window_hours",
)


def rules_fingerprint(config: dict) -> str:
    payload = {k: config.get(k) for k in RULE_KEYS}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def stamp_rules_version(new_config: dict, old_config: dict, now: datetime | None = None) -> dict:
    """new_config'e rules_fingerprint + rules_version_since ekler (kopyası döner)."""
    fingerprint = rules_fingerprint(new_config)
    since = old_config.get("rules_version_since")
    if old_config.get("rules_fingerprint") != fingerprint or not since:
        since = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return {**new_config, "rules_fingerprint": fingerprint, "rules_version_since": since}
