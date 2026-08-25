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

If the current reference point goes unbroken for too long (a real trend
change, not just a pullback), `max_reference_age_days` re-anchors the
search to only the recent window instead of waiting forever for price to
reclaim a level from a fundamentally different market phase.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta


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


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _walk(pivots: list[Pivot], side: str) -> tuple[Pivot | None, Pivot | None]:
    """One pass of the reference/pending/validated walk. Returns
    (current reference pivot, most recently validated pivot)."""
    if side == "long":
        ref_kind, better = "high", (lambda new, old: new.price > old.price)
    else:
        ref_kind, better = "low", (lambda new, old: new.price < old.price)

    reference: Pivot | None = None
    pending: Pivot | None = None
    validated: Pivot | None = None
    for p in pivots:
        if p.kind == ref_kind:
            if reference is None:
                reference = p
            elif better(p, reference):
                if pending is not None:
                    validated = pending
                reference = p
                pending = None
        else:
            if reference is not None:
                pending = p
    return reference, validated


def validated_trailing_level(
    bars: list[Bar], side: str, order: int = 2, max_reference_age_days: float | None = None
) -> Pivot | None:
    """Return the most recently validated structural pivot to trail the stop
    behind, or None if structure hasn't validated a point yet.

    If the standing reference point (the swing high/low price must still
    break to validate anything) is older than `max_reference_age_days`
    relative to the last bar, structure is re-evaluated using only bars
    from that window - the old reference is a stale echo of a market phase
    that's already over, not something still worth waiting on.
    """
    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")
    if not bars:
        return None

    pivots = find_pivots(bars, order)
    reference, validated = _walk(pivots, side)

    if max_reference_age_days is not None and reference is not None:
        last_ts = _parse(bars[-1].t)
        if (last_ts - _parse(reference.t)) >= timedelta(days=max_reference_age_days):
            cutoff = last_ts - timedelta(days=max_reference_age_days)
            recent_bars = [b for b in bars if _parse(b.t) >= cutoff]
            recent_pivots = find_pivots(recent_bars, order)
            _, validated = _walk(recent_pivots, side)

    return validated
