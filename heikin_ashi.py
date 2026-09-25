"""Heikin Ashi + SMA50 + Stokastik (14, 3, 3) long stratejisinin saf-python
hesaplamaları (pandas bağımlılığı yok - indicators.py ile aynı gerekçe,
GitHub Action job'ları hafif kalsın).

Alım (buy_algorithms.heikin_ashi_stoch_signal):
  - Trend: kapanış SMA(50) üzerinde.
  - Momentum: Stokastik %K < 30 VE %K > %D (aşırı satım bölgesinde yukarı dönüş).
  - Mum: önceki HA mumu kırmızı, güncel HA mumu yeşil VE alt fitili yok
    (|HA_Low - HA_Open|, HA mumunun toplam boyunun en fazla %5'i).

Çıkış (stop_algorithms "heikin_ashi_exit"):
  - İlk kırmızı HA mumu, YA DA Stokastik %K > 80 VE %K < %D (aşırı alımda
    aşağı kesişim).
"""

from dataclasses import dataclass

from structure import Bar

SMA_PERIOD = 50
STOCH_K_PERIOD = 14
STOCH_D_PERIOD = 3
STOCH_OVERSOLD = 30.0
STOCH_OVERBOUGHT = 80.0
MAX_LOWER_WICK_RATIO = 0.05


@dataclass(frozen=True)
class HABar:
    o: float
    h: float
    l: float
    c: float

    @property
    def green(self) -> bool:
        return self.c > self.o

    @property
    def red(self) -> bool:
        return self.c < self.o


def heikin_ashi(bars: list[Bar]) -> list[HABar]:
    """Standart OHLC barlarından Heikin Ashi mumları. HA_Open özyinelemeli
    olduğu için değerler, verilen bar penceresinin başlangıcına bağlıdır -
    birkaç düzine bardan sonra etkisi pratikte kaybolur."""
    result: list[HABar] = []
    ha_open = None
    prev_close = None
    for b in bars:
        ha_close = (b.o + b.h + b.l + b.c) / 4
        ha_open = (b.o + b.c) / 2 if ha_open is None else (ha_open + prev_close) / 2
        result.append(HABar(
            o=ha_open, h=max(b.h, ha_open, ha_close), l=min(b.l, ha_open, ha_close), c=ha_close,
        ))
        prev_close = ha_close
    return result


def stochastic_series(
    bars: list[Bar], k_period: int = STOCH_K_PERIOD, d_period: int = STOCH_D_PERIOD,
) -> tuple[list[float | None], list[float | None]]:
    """Stokastik %K (k_period) ve %D (%K'nın d_period'luk SMA'sı) serileri.
    Pencere yeterli değilse ya da en yüksek = en düşükse ilgili indeks None."""
    k: list[float | None] = [None] * len(bars)
    for i in range(k_period - 1, len(bars)):
        window = bars[i - k_period + 1:i + 1]
        low = min(b.l for b in window)
        high = max(b.h for b in window)
        if high > low:
            k[i] = 100 * (bars[i].c - low) / (high - low)

    d: list[float | None] = [None] * len(bars)
    for i in range(d_period - 1, len(bars)):
        window = k[i - d_period + 1:i + 1]
        if all(v is not None for v in window):
            d[i] = sum(window) / d_period
    return k, d


def _last_stoch(bars: list[Bar], k_period: int, d_period: int) -> tuple[float, float] | None:
    # Son %D için yalnızca son (k_period + d_period - 1) bar gerekiyor.
    k, d = stochastic_series(bars[-(k_period + d_period - 1):], k_period, d_period)
    if k[-1] is None or d[-1] is None:
        return None
    return k[-1], d[-1]


def long_entry(
    bars: list[Bar], sma_period: int = SMA_PERIOD, k_period: int = STOCH_K_PERIOD,
    d_period: int = STOCH_D_PERIOD, oversold: float = STOCH_OVERSOLD,
    max_lower_wick_ratio: float = MAX_LOWER_WICK_RATIO,
) -> tuple[float, float, float] | None:
    """Son bar alım koşullarının hepsini sağlıyorsa (sma, %K, %D), yoksa None."""
    if len(bars) < max(sma_period, k_period + d_period - 1, 2):
        return None
    last = bars[-1]
    sma = sum(b.c for b in bars[-sma_period:]) / sma_period
    if last.c <= sma:
        return None

    stoch = _last_stoch(bars, k_period, d_period)
    if stoch is None:
        return None
    k, d = stoch
    if not (k < oversold and k > d):
        return None

    ha = heikin_ashi(bars)
    prev, cur = ha[-2], ha[-1]
    if not (prev.red and cur.green):
        return None
    if abs(cur.l - cur.o) > (cur.h - cur.l) * max_lower_wick_ratio:
        return None
    return sma, k, d


def long_exit_reason(
    bars: list[Bar], k_period: int = STOCH_K_PERIOD, d_period: int = STOCH_D_PERIOD,
    overbought: float = STOCH_OVERBOUGHT,
) -> str | None:
    """Son bar bir çıkış sinyali üretiyorsa sebebi, yoksa None."""
    if not bars:
        return None
    if heikin_ashi(bars)[-1].red:
        return "HA kırmızı mum"
    stoch = _last_stoch(bars, k_period, d_period) if len(bars) >= k_period + d_period - 1 else None
    if stoch is not None:
        k, d = stoch
        if k > overbought and k < d:
            return f"Stokastik aşırı alım kesişimi (%K {k:.0f} < %D {d:.0f})"
    return None
