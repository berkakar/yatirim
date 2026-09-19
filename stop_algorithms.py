"""Stop-loss hesaplaması için birden fazla, birbirinden bağımsız algoritma.
buy_algorithms.py'deki desenle aynı: her algoritma ortak bir arayüzde
tanımlanır ve STOP_ALGORITHMS sözlüğünde toplanır, böylece canlı sistem
(alpaca_trailing_stop.py) ve backtest (backtest_engine.py) aynı seçili
algoritmayı çalıştırabilir.

Bir stop-loss algoritması iki farklı anda karar verir, bu yüzden her biri
iki fonksiyondan oluşuyor (buy_algorithms'daki tek `fn(bars) -> BuySignal`
yerine):
  - initial_stop: pozisyon yeni açıldığında (henüz resting bir stop yokken)
    ilk stop seviyesini belirler.
  - trail: resting bir stop zaten varken, onu SIKILAŞTIRMAK için bir aday
    üretir. "Asla gevşetme" kuralı algoritmanın kendisinde değil, çağıran
    tarafta (mevcut current_stop_price ile karşılaştırılarak) uygulanır -
    her algoritma bunu tekrar yazmak zorunda kalmasın diye.
"""

from dataclasses import dataclass
from typing import Callable

from indicators import atr, ema
from structure import Bar, validated_trailing_level


@dataclass(frozen=True)
class StopContext:
    """trail() algoritmalarının ihtiyaç duyduğu ortak girdiler - hem canlı
    sistem hem backtest tarafından aynı şekilde doldurulur."""
    side: str  # "long" or "short"
    entry_price: float
    current_stop_price: float
    bars: list[Bar]  # pozisyon yönetim başlangıcından bu yana barlar, sonuncusu güncel bar
    daily_closes: list[float] | None = None  # trend filtresi için, artan sırada kapanışlar
    topped_up: bool = False
    top_up_stop_mode: str = "keep"


@dataclass(frozen=True)
class StopDecision:
    price: float
    reason: str


@dataclass(frozen=True)
class StopAlgorithm:
    label: str
    initial_stop: Callable[..., float]
    trail: Callable[..., "StopDecision | None"]


# ---- Varsayılan algoritma: sabit-% ilk stop + breakeven floor + günlük EMA
# trend filtresiyle gate'lenen ATR-buffered break-of-structure trail. Bu,
# alpaca_trailing_stop.manage_position() ve backtest_engine.run_backtest()
# içine daha önce hardcoded olan TEK mantıkla birebir aynı davranışı taşır -
# bkz. alpaca_trailing_stop.py modül docstring'i (adım 2-5).

INITIAL_STOP_PCT = 0.015
ATR_PERIOD = 14
ATR_MULTIPLIER = 0.25
BREAKEVEN_TRIGGER_PCT = 0.01
STALE_REFERENCE_DAYS = 10.0
TREND_EMA_PERIOD = 50
SWING_ORDER = 2
FALLBACK_BUFFER_PCT = 0.001  # only used if ATR can't be computed yet (too few bars)


def _trend_ok(daily_closes: list[float] | None, side: str, trend_ema_period: int) -> bool:
    """Günlük EMA'ya göre trend filtresi - yeterli günlük veri yoksa ya da
    filtre kapalıysa (period<=0) trail'i engellemez (True döner)."""
    if trend_ema_period <= 0 or not daily_closes:
        return True
    trend_ema = ema(daily_closes, trend_ema_period)
    if trend_ema is None:
        return True
    last_close = daily_closes[-1]
    return last_close > trend_ema if side == "long" else last_close < trend_ema


def breakeven_atr_structure_initial_stop(
    entry_price: float, side: str, bars: list[Bar] | None = None,
    initial_stop_pct: float = INITIAL_STOP_PCT,
) -> float:
    return entry_price * (1 - initial_stop_pct) if side == "long" else entry_price * (1 + initial_stop_pct)


def breakeven_atr_structure_trail(
    ctx: StopContext,
    initial_stop_pct: float = INITIAL_STOP_PCT,
    atr_period: int = ATR_PERIOD,
    atr_multiplier: float = ATR_MULTIPLIER,
    breakeven_trigger_pct: float = BREAKEVEN_TRIGGER_PCT,
    stale_reference_days: float = STALE_REFERENCE_DAYS,
    trend_ema_period: int = TREND_EMA_PERIOD,
    swing_order: int = SWING_ORDER,
    fallback_buffer_pct: float = FALLBACK_BUFFER_PCT,
) -> StopDecision | None:
    """Breakeven floor + (top-up sonrası "tighten_to_new_entry" seçiliyse)
    yeni ortalamaya göre nefes payı adayı + günlük EMA trend filtresiyle
    gate'lenen ATR-buffered break-of-structure trail. Adaylardan sadece en
    çok sıkılaştıran, mevcut fiyatın doğru tarafında kalan seçilir."""
    if not ctx.bars:
        return None
    side = ctx.side
    last_price = ctx.bars[-1].c
    candidates: list[tuple[float, str]] = []

    gain_pct = ((last_price - ctx.entry_price) / ctx.entry_price if side == "long"
                else (ctx.entry_price - last_price) / ctx.entry_price)
    if gain_pct >= breakeven_trigger_pct:
        if side == "long" and ctx.entry_price > ctx.current_stop_price and ctx.entry_price < last_price:
            candidates.append((ctx.entry_price, "breakeven"))
        elif side == "short" and ctx.entry_price < ctx.current_stop_price and ctx.entry_price > last_price:
            candidates.append((ctx.entry_price, "breakeven"))

    if ctx.topped_up and ctx.top_up_stop_mode == "tighten_to_new_entry":
        if side == "long":
            top_up_candidate = ctx.entry_price * (1 - initial_stop_pct)
            if top_up_candidate < last_price:
                candidates.append((top_up_candidate, "top-up nefes payı"))
        else:
            top_up_candidate = ctx.entry_price * (1 + initial_stop_pct)
            if top_up_candidate > last_price:
                candidates.append((top_up_candidate, "top-up nefes payı"))

    if _trend_ok(ctx.daily_closes, side, trend_ema_period):
        pivot = validated_trailing_level(ctx.bars, side, swing_order, stale_reference_days)
        if pivot is not None:
            atr_value = atr(ctx.bars, atr_period)
            buffer_amount = atr_value * atr_multiplier if atr_value is not None else pivot.price * fallback_buffer_pct
            if side == "long":
                candidate = pivot.price - buffer_amount
                if candidate < last_price:
                    candidates.append((candidate, f"structure@{pivot.price:.2f}"))
            else:
                candidate = pivot.price + buffer_amount
                if candidate > last_price:
                    candidates.append((candidate, f"structure@{pivot.price:.2f}"))

    if not candidates:
        return None

    if side == "long":
        best_price, reason = max(candidates, key=lambda c: c[0])
        improves = best_price > ctx.current_stop_price
    else:
        best_price, reason = min(candidates, key=lambda c: c[0])
        improves = best_price < ctx.current_stop_price

    if not improves:
        return None
    return StopDecision(price=best_price, reason=reason)


STOP_ALGORITHMS: dict[str, StopAlgorithm] = {
    "breakeven_atr_structure": StopAlgorithm(
        label="Breakeven + Yapısal Trail (ATR tamponlu)",
        initial_stop=breakeven_atr_structure_initial_stop,
        trail=breakeven_atr_structure_trail,
    ),
}
DEFAULT_STOP_ALGORITHM = "breakeven_atr_structure"
