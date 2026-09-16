"""Otomatik Alım/Satım modülünün "ek algoritma" ile hisse kümesini daraltma
adımı: son bir haftalık pencerede RSI14/RSI21 kesişimi VE EMA50/EMA200
"golden cross" olayının BİRLİKTE (aynı pencerede) gerçekleştiği hisseleri
işaretler - iki bağımsız momentum sinyalinin teyidi (confluence), literatürde
tekil bir göstergeye göre daha az yalancı sinyal üreten bir yaklaşım.

Bu kasıtlı olarak sıkı bir filtre: günlük EMA50/EMA200 kesişimi seyrek bir
olay olduğundan bazı taramalarda 0 aday çıkması beklenir - `select_top_
candidates` en fazla `max_candidates` döner, garanti bir sayı değil."""

from dataclasses import dataclass

from indicators import ema_series, rsi_series

RSI_FAST_PERIOD = 14
RSI_SLOW_PERIOD = 21
EMA_FAST_PERIOD = 50
EMA_SLOW_PERIOD = 200


@dataclass(frozen=True)
class MomentumSignal:
    rsi_cross_days_ago: int  # RSI14'ün RSI21'i son kestiği gün (0 = bugün)
    ema_cross_days_ago: int  # EMA50'nin EMA200'ü son kestiği gün (0 = bugün)


def _bullish_cross_days_ago(fast: list[float | None], slow: list[float | None], lookback_days: int) -> int | None:
    """fast'ın slow'u en son AŞAĞIDAN YUKARIYA kestiği günün "kaç gün önce"
    olduğunu döner (son bar = 0). Son `lookback_days` bar içinde böyle bir
    kesişim yoksa None."""
    n = len(fast)
    for i in range(n - 1, max(n - 1 - lookback_days, 0), -1):
        f0, f1, s0, s1 = fast[i - 1], fast[i], slow[i - 1], slow[i]
        if f0 is None or f1 is None or s0 is None or s1 is None:
            continue
        if f0 <= s0 and f1 > s1:
            return (n - 1) - i
    return None


def momentum_confirmation(daily_closes: list[float], lookback_days: int = 5) -> MomentumSignal | None:
    """`daily_closes` (kronolojik sırada, en az EMA200 için ~200+ gün) üzerinde
    RSI14/RSI21 ve EMA50/EMA200 serilerini hesaplar; son `lookback_days` işlem
    günü içinde HER İKİ kesişim de (RSI14>RSI21'e geçiş VE EMA50>EMA200'e
    geçiş - "golden cross") gerçekleşmişse bir MomentumSignal döner, aksi
    halde None."""
    if len(daily_closes) < EMA_SLOW_PERIOD + lookback_days:
        return None

    rsi_fast = rsi_series(daily_closes, RSI_FAST_PERIOD)
    rsi_slow = rsi_series(daily_closes, RSI_SLOW_PERIOD)
    ema_fast = ema_series(daily_closes, EMA_FAST_PERIOD)
    ema_slow = ema_series(daily_closes, EMA_SLOW_PERIOD)

    rsi_cross = _bullish_cross_days_ago(rsi_fast, rsi_slow, lookback_days)
    ema_cross = _bullish_cross_days_ago(ema_fast, ema_slow, lookback_days)
    if rsi_cross is None or ema_cross is None:
        return None
    return MomentumSignal(rsi_cross_days_ago=rsi_cross, ema_cross_days_ago=ema_cross)


def select_top_candidates(
    scored: list[tuple[dict, MomentumSignal]], max_candidates: int = 10,
) -> list[dict]:
    """`scored`: [(signal_row, MomentumSignal), ...] - signal_row aynı hissede
    birden fazla satır varsa (farklı algoritma/mum periyodu) hepsi ayrı ayrı
    değerlendirilmiş olabilir; burada hisse başına en güncel (kesişimleri en
    yakın) tek satır tutulur, sonra en güncelden başlayarak en fazla
    `max_candidates` satır döner."""
    best_per_symbol: dict[str, tuple[dict, MomentumSignal]] = {}
    for row, sig in scored:
        symbol = row["Hisse"]
        recency = sig.rsi_cross_days_ago + sig.ema_cross_days_ago
        current = best_per_symbol.get(symbol)
        if current is None or recency < (current[1].rsi_cross_days_ago + current[1].ema_cross_days_ago):
            best_per_symbol[symbol] = (row, sig)

    ordered = sorted(
        best_per_symbol.values(),
        key=lambda pair: pair[1].rsi_cross_days_ago + pair[1].ema_cross_days_ago,
    )
    return [row for row, _ in ordered[:max_candidates]]
