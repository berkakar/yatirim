"""Small pure-python indicators for the trailing-stop bot (no pandas_ta
dependency needed here - keeps the GitHub Actions job lightweight)."""

from structure import Bar


def atr(bars: list[Bar], period: int = 14) -> float | None:
    """Average True Range (Wilder's smoothing) of the most recent `period` bars."""
    if len(bars) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(bars)):
        high, low, prev_close = bars[i].h, bars[i].l, bars[i - 1].c
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    window = true_ranges[-period:]
    return sum(window) / len(window)


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    result = sum(values[:period]) / period
    for v in values[period:]:
        result = v * k + result * (1 - k)
    return result
