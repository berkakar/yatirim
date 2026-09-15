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


def ema_series(values: list[float], period: int) -> list[float | None]:
    """ema()'nın tek değer döndüren haliyle aynı formül, ama her indeks için
    o ana kadarki EMA değerini döner (ilk `period - 1` indeks None) - kesişim
    (örn. EMA50/EMA200 golden cross) tespiti için tam seriye ihtiyaç var."""
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    k = 2 / (period + 1)
    ema_value = sum(values[:period]) / period
    result[period - 1] = ema_value
    for i in range(period, len(values)):
        ema_value = values[i] * k + ema_value * (1 - k)
        result[i] = ema_value
    return result


def rsi_series(values: list[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI, her indeks için o ana kadarki değeri döner (ilk `period`
    indeks None). RSI14/RSI21 kesişimi gibi sinyaller tam seriye ihtiyaç duyar."""
    result: list[float | None] = [None] * len(values)
    if len(values) < period + 1:
        return result

    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rsi(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    result[period] = _rsi(avg_gain, avg_loss)
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        result[i + 1] = _rsi(avg_gain, avg_loss)
    return result
