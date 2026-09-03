"""Walk-forward backtest engine for the premium buy-point algorithms
(buy_algorithms.py) paired with the structure-based trailing stop
(alpaca_trailing_stop.py / structure.py), replayed against historical bars
instead of live Alpaca orders.

Reuses the exact same decision functions the live system uses
(buy_algorithms.ALGORITHMS, reject_if_marketable, structure.
validated_trailing_level, indicators.atr/ema) and the same tunable
constants (alpaca_trailing_stop.INITIAL_STOP_PCT, ATR_PERIOD, etc.), so a
backtest result reflects what the live bot would actually have done, not a
separate approximation of it.

No look-ahead: at simulated bar i, only bars[:i+1] are visible, and the
daily closes used for the higher-timeframe trend filter / trend_pullback's
SMA200 gate are trimmed to strictly before that bar's calendar date (or
up to and including it, for the "1Day" timeframe itself, since a daily
bar's own close is exactly the information available at its close).

Order simulation mirrors the live system:
  - No position, no resting order, a valid signal appears -> a resting
    limit buy is "placed" (not filled the same bar).
  - No position, a resting order exists -> filled if the bar's low
    touches its price; otherwise re-priced or canceled the same way
    alpaca_buy_points.check_symbol() would (signal moved / went invalid).
  - Position open -> stopped out if the bar's low touches the stop
    (filled at the stop price, or the bar's open if it gapped through);
    otherwise the same breakeven/structure-trail candidates as
    alpaca_trailing_stop.manage_position() are evaluated to (only) tighten
    the stop for subsequent bars.
  - A position still open when the fetched window ends is closed at the
    last bar's close ("test_end_close") so P&L is always well-defined.
"""
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from alpaca_trailing_stop import (
    ATR_MULTIPLIER,
    ATR_PERIOD,
    BREAKEVEN_TRIGGER_PCT,
    FALLBACK_BUFFER_PCT,
    INITIAL_STOP_PCT,
    STALE_REFERENCE_DAYS,
    TREND_EMA_PERIOD,
)
from alpaca_trailing_stop import LOOKBACK_DAYS as TRAIL_LOOKBACK_DAYS
from buy_algorithms import ALGORITHMS, reject_if_marketable
from indicators import atr, ema
from structure import Bar, validated_trailing_level

SIGNAL_LOOKBACK_DAYS = 60  # matches alpaca_buy_points.py's BUY_LOOKBACK_DAYS default
SWING_ORDER = 2


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _window(bars: list[Bar], end_idx: int, days: float, floor_idx: int = 0) -> list[Bar]:
    """bars[floor_idx:end_idx+1] trimmed to the last `days` calendar days
    ending at bars[end_idx] - mirrors get_management_start / the
    BUY_LOOKBACK_DAYS windowing the live scripts use, so the backtest only
    ever sees what production would have fetched."""
    cutoff = _parse(bars[end_idx].t) - timedelta(days=days)
    lo = floor_idx
    while lo < end_idx and _parse(bars[lo].t) < cutoff:
        lo += 1
    return bars[lo:end_idx + 1]


def _daily_closes_upto(daily_pairs: list[tuple], bar_date, inclusive: bool) -> list[float]:
    if inclusive:
        return [c for d, c in daily_pairs if d <= bar_date]
    return [c for d, c in daily_pairs if d < bar_date]


@dataclass
class Trade:
    side: str  # "buy" | "sell"
    time: str
    price: float
    qty: float
    reason: str


@dataclass
class BacktestResult:
    symbol: str
    algorithm: str
    timeframe: str
    days_of_data: int
    days_before_trading: int
    starting_budget: float
    trades: list = field(default_factory=list)
    final_value: float = 0.0

    @property
    def pnl(self) -> float:
        return round(self.final_value - self.starting_budget, 2)

    @property
    def pnl_pct(self) -> float:
        return round(self.pnl / self.starting_budget * 100, 2) if self.starting_budget else 0.0


def run_backtest(
    symbol: str, algorithm: str, timeframe: str, bars: list[Bar], daily_pairs: list[tuple],
    days_of_data: int, days_before_trading: int, starting_budget: float,
) -> BacktestResult:
    """daily_pairs: [(date, close), ...] sorted ascending, spanning at least
    from (bars[0] - ~400 days) to bars[-1] so SMA200-style daily gates have
    enough history at every simulated point."""
    result = BacktestResult(symbol, algorithm, timeframe, days_of_data, days_before_trading, starting_budget)
    result.final_value = starting_budget
    if not bars:
        return result

    _, algo_fn = ALGORITHMS[algorithm]
    is_daily_tf = timeframe == "1Day"

    trading_start = _parse(bars[0].t) + timedelta(days=days_before_trading)
    start_idx = 0
    while start_idx < len(bars) and _parse(bars[start_idx].t) < trading_start:
        start_idx += 1

    cash = starting_budget
    position = None  # {"entry_price", "qty", "stop_price", "entry_idx"}
    resting = None   # {"price", "qty", "stop_price", "reason"}

    for i in range(start_idx, len(bars)):
        bar = bars[i]
        bar_date = _parse(bar.t).date()

        if position is not None:
            if bar.l <= position["stop_price"]:
                fill = bar.o if bar.o < position["stop_price"] else position["stop_price"]
                cash += position["qty"] * fill
                result.trades.append(Trade("sell", bar.t, round(fill, 4), position["qty"], "stop"))
                position = None
                continue

            struct_bars = _window(bars, i, TRAIL_LOOKBACK_DAYS, floor_idx=position["entry_idx"])
            candidates = []
            entry_price, last_price = position["entry_price"], bar.c
            gain_pct = (last_price - entry_price) / entry_price
            if (gain_pct >= BREAKEVEN_TRIGGER_PCT and entry_price > position["stop_price"]
                    and entry_price < last_price):
                candidates.append((entry_price, "breakeven"))

            trend_ok = True
            if TREND_EMA_PERIOD > 0:
                trend_closes = _daily_closes_upto(daily_pairs, bar_date, inclusive=is_daily_tf)
                trend_val = ema(trend_closes, TREND_EMA_PERIOD)
                if trend_val is not None:
                    trend_ok = trend_closes[-1] > trend_val

            if trend_ok:
                pivot = validated_trailing_level(struct_bars, "long", SWING_ORDER, STALE_REFERENCE_DAYS)
                if pivot is not None:
                    atr_value = atr(struct_bars, ATR_PERIOD)
                    buffer_amount = atr_value * ATR_MULTIPLIER if atr_value is not None else pivot.price * FALLBACK_BUFFER_PCT
                    candidate = pivot.price - buffer_amount
                    if candidate < last_price:
                        candidates.append((candidate, f"structure@{pivot.price:.2f}"))

            if candidates:
                best_price, _reason = max(candidates, key=lambda c: c[0])
                if best_price > position["stop_price"]:
                    position["stop_price"] = best_price
            continue

        # No open position - check the resting entry order (if any) for a fill first,
        # using the price it already had going into this bar.
        if resting is not None and bar.l <= resting["price"]:
            qty = resting["qty"]
            cash -= qty * resting["price"]
            position = {"entry_price": resting["price"], "qty": qty,
                        "stop_price": resting["stop_price"], "entry_idx": i}
            result.trades.append(Trade("buy", bar.t, resting["price"], qty, resting["reason"]))
            resting = None
            continue

        sig_bars = _window(bars, i, SIGNAL_LOOKBACK_DAYS)
        daily_closes = _daily_closes_upto(daily_pairs, bar_date, inclusive=is_daily_tf) if algorithm == "trend_pullback" else None
        signal = algo_fn(sig_bars, daily_closes)
        if signal is not None:
            signal = reject_if_marketable(signal, bar.c)

        if resting is not None:
            if signal is None:
                resting = None
            elif abs(signal.price - resting["price"]) >= 0.01:
                qty = math.floor(cash / signal.price) if signal.price > 0 else 0
                resting = ({"price": round(signal.price, 2), "qty": qty,
                            "stop_price": round(signal.price * (1 - INITIAL_STOP_PCT), 2), "reason": signal.reason}
                           if qty > 0 else None)
        elif signal is not None:
            qty = math.floor(cash / signal.price) if signal.price > 0 else 0
            if qty > 0:
                resting = {"price": round(signal.price, 2), "qty": qty,
                           "stop_price": round(signal.price * (1 - INITIAL_STOP_PCT), 2), "reason": signal.reason}

    if position is not None:
        last_bar = bars[-1]
        cash += position["qty"] * last_bar.c
        result.trades.append(Trade("sell", last_bar.t, round(last_bar.c, 4), position["qty"], "test_end_close"))

    result.final_value = round(cash, 2)
    return result
