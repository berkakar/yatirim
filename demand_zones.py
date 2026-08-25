"""
Demand-zone ("premium buy point") detection, approximating JeaFx's
"When Supply & Demand Fails" method:

  - A demand zone is the last candle before a strong bullish impulse leg -
    its [low, high] range is the discounted price traders are willing to
    buy from on a pullback.
  - A zone stays valid while price hasn't closed below it (a "demand
    fail" invalidates it) and hasn't been retested too many times - each
    retest weakens the zone ("every tap ... this wall is getting
    weaker", per the video).
  - The buy point is the top of the most recently formed still-valid
    zone: the first, shallowest pullback level into it.

This is a mechanical approximation of a discretionary, visually-judged
method (impulse strength, consolidation vs. indecision candles) - it
won't draw zones identically to a human, but follows the same rules:
impulse -> zone -> fail/tap invalidation.
"""

from dataclasses import dataclass

from structure import Bar


@dataclass(frozen=True)
class DemandZone:
    index: int
    top: float
    bottom: float
    formed_at: str
    tap_count: int


def find_demand_zones(
    bars: list[Bar], impulse_pct: float = 0.03, impulse_bars: int = 3, max_taps: int = 2
) -> list[DemandZone]:
    """All still-valid demand zones, in chronological order."""
    zones = []
    n = len(bars)

    for i in range(n - impulse_bars):
        bar_range = bars[i].h - bars[i].l
        body = bars[i].c - bars[i].o
        if bar_range > 0 and body > 0 and (body / bar_range) >= 0.6:
            continue  # bar i is itself a strong bullish candle - it's the impulse, not "the last candle before" one

        window_highs = [bars[j].h for j in range(i + 1, i + 1 + impulse_bars)]
        move = (max(window_highs) - bars[i].c) / bars[i].c
        if move < impulse_pct:
            continue

        zone_top, zone_bottom = bars[i].h, bars[i].l

        invalidated = False
        tap_count = 0
        in_zone = False
        for j in range(i + 1, n):
            if bars[j].c < zone_bottom:
                invalidated = True
                break
            touched = bars[j].l <= zone_top
            if touched and not in_zone:
                tap_count += 1
            in_zone = touched

        if invalidated or tap_count > max_taps:
            continue

        zones.append(DemandZone(index=i, top=zone_top, bottom=zone_bottom, formed_at=bars[i].t, tap_count=tap_count))

    return zones


def find_buy_point(
    bars: list[Bar], impulse_pct: float = 0.03, impulse_bars: int = 3, max_taps: int = 2
) -> DemandZone | None:
    """The most recently formed still-valid demand zone, or None."""
    zones = find_demand_zones(bars, impulse_pct, impulse_bars, max_taps)
    return zones[-1] if zones else None
