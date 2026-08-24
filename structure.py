"""
Swing-structure detection and break-of-structure (BOS) trailing-stop logic,
following the method described in JeaFx's "Stop Loss Trailing" video:

  - Find swing highs/lows (fractal pivots) in a bar series.
  - For an uptrend: a new swing high that exceeds the previous reference
    swing high is a bullish break of structure. It validates the swing low
    formed between the two highs as the new trailing-stop reference.
  - For a downtrend: mirror this using swing lows breaking below the
    previous reference swing low, validating the swing high between them.

Only closed bars are ever used, so a pivot needs `order` bars on both
sides before it can be confirmed - this mirrors "don't chase every tick,
wait for structure to be validated."
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    t: str
    o: float
    h: float
    l: float
    c: float
    v: float


@dataclass(frozen=True)
class Pivot:
    index: int
    kind: str  # "high" or "low"
    price: float
    t: str


def find_pivots(bars: list[Bar], order: int = 2) -> list[Pivot]:
    pivots = []
    for i in range(order, len(bars) - order):
        window = bars[i - order : i + order + 1]
        highs = [b.h for b in window]
        lows = [b.l for b in window]
        if bars[i].h == max(highs) and highs.count(bars[i].h) == 1:
            pivots.append(Pivot(i, "high", bars[i].h, bars[i].t))
        if bars[i].l == min(lows) and lows.count(bars[i].l) == 1:
            pivots.append(Pivot(i, "low", bars[i].l, bars[i].t))
    pivots.sort(key=lambda p: p.index)
    return pivots


def validated_trailing_level(bars: list[Bar], side: str, order: int = 2) -> Pivot | None:
    """Return the most recently validated structural pivot to trail the stop
    behind, or None if structure hasn't validated a point yet."""
    pivots = find_pivots(bars, order)

    if side == "long":
        reference_high = None
        pending_low = None
        validated_low = None
        for p in pivots:
            if p.kind == "high":
                if reference_high is None:
                    reference_high = p
                elif p.price > reference_high.price:
                    if pending_low is not None:
                        validated_low = pending_low
                    reference_high = p
                    pending_low = None
            else:
                if reference_high is not None:
                    pending_low = p
        return validated_low

    if side == "short":
        reference_low = None
        pending_high = None
        validated_high = None
        for p in pivots:
            if p.kind == "low":
                if reference_low is None:
                    reference_low = p
                elif p.price < reference_low.price:
                    if pending_high is not None:
                        validated_high = pending_high
                    reference_low = p
                    pending_high = None
            else:
                if reference_low is not None:
                    pending_high = p
        return validated_high

    raise ValueError(f"side must be 'long' or 'short', got {side!r}")
