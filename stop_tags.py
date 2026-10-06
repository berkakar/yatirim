"""Stop emirlerinin client_order_id etiketleri - [2026-09-28 · Öneri 3 ve 6].

Neden: 2026-09-28 emir analizinde bir stopun HANGİ sebeple tetiklendiği
(ilk stop mu, breakeven mi, yapısal trail mi, seans dışı guard mı) hiçbir yerde
kayıtlı değildi; "neden kârla çıkamıyoruz" sorusu tahminle cevaplanmak
zorunda kaldı. Ayrıca açılış kalkanı (Öneri 3) seans dışında stopu geniş bir
"felaket" seviyesine çekerken, 09:45'te geri döneceği GERÇEK seviyeyi bir yerde
saklamalı. İkisi de Alpaca'nın kendi emir geçmişinde, emrin client_order_id
alanında taşınır - ayrı bir state dosyası (ve onun commit/çakışma derdi) yok.

Biçim (hepsi "-" ile ayrılır, sembol ortada, sonda epoch saniye - Alpaca
client_order_id'nin hesap içinde benzersiz olmasını istiyor):
    stop-<kod>-<SEMBOL>-<epoch>                 ör. stop-breakeven-MSFT-1759059000
    shield-<SEMBOL>-<gerçek stop cent>-<epoch>  ör. shield-MSFT-49444-1759059000
    shieldexit-<SEMBOL>-<epoch>                 kalkan sonrası market çıkışı
Semboller "-" içermediğinden (Alpaca "BRK.B" biçimini kullanır) sağdan
ayrıştırmak güvenli.
"""

import time

SHIELD_PREFIX = "shield-"
SHIELD_EXIT_PREFIX = "shieldexit-"
STOP_PREFIX = "stop-"

# StopDecision.reason (serbest metin) -> kısa etiket kodu. İlk eşleşen kazanır.
_REASON_CODES = (
    ("breakeven", "breakeven"),
    ("structure", "structure"),
    ("chandelier", "chandelier"),
    ("kâr kilidi", "profitlock"),
    ("HA çıkış", "haexit"),
    ("top-up", "topup"),
)

# Algo Analiz'de gösterilen Türkçe açıklamalar.
REASON_LABELS = {
    "initial": "İlk stop",
    "breakeven": "Breakeven",
    "structure": "Yapısal trail",
    "chandelier": "Chandelier (ATR) trail",
    "profitlock": "Kâr kilidi",
    "haexit": "Heikin Ashi çıkışı",
    "topup": "İlave alım sonrası stop",
    "restore": "Kalkan sonrası gerçek stop",
    "trail": "Trail (diğer)",
    "shield": "Açılış kalkanı (felaket stopu)",
    "shieldexit": "Açılış kalkanı sonrası çıkış",
    "extguard": "Seans dışı acil çıkış (limit)",
}

# [2026-10-06] Seans dışı guard'ın acil limit-sell emrinin etiket kodu
# (stop_tag(EXT_GUARD_CODE, sembol)) - sahipsiz emir temizliği bu emri
# tanıyıp pozisyonu kalmamışsa iptal edebilsin diye.
EXT_GUARD_CODE = "extguard"


def reason_code(reason: str) -> str:
    """StopDecision.reason metnini kısa bir etiket koduna çevirir."""
    for needle, code in _REASON_CODES:
        if needle in reason:
            return code
    return "trail"


def stop_tag(code: str, symbol: str, now: float | None = None) -> str:
    return f"{STOP_PREFIX}{code}-{symbol}-{int(now if now is not None else time.time())}"


def shield_tag(symbol: str, real_stop_price: float, now: float | None = None) -> str:
    cents = int(round(real_stop_price * 100))
    return f"{SHIELD_PREFIX}{symbol}-{cents}-{int(now if now is not None else time.time())}"


def shield_exit_tag(symbol: str, now: float | None = None) -> str:
    return f"{SHIELD_EXIT_PREFIX}{symbol}-{int(now if now is not None else time.time())}"


def parse_shield_real_stop(client_order_id: str | None) -> float | None:
    """shield etiketinden gerçek stop fiyatını çıkarır, etiket değilse None."""
    if not client_order_id or not client_order_id.startswith(SHIELD_PREFIX):
        return None
    parts = client_order_id.split("-")
    if len(parts) < 4:
        return None
    try:
        return int(parts[-2]) / 100
    except ValueError:
        return None


def tag_kind(client_order_id: str | None) -> str | None:
    """Bir emrin etiketinden kısa kod: "breakeven", "shield", "shieldexit"...
    Bu sistemin koymadığı (etiketsiz) emirler için None."""
    if not client_order_id:
        return None
    if client_order_id.startswith(SHIELD_EXIT_PREFIX):
        return "shieldexit"
    if client_order_id.startswith(SHIELD_PREFIX):
        return "shield"
    if client_order_id.startswith(STOP_PREFIX):
        return client_order_id[len(STOP_PREFIX):].split("-", 1)[0]
    return None
