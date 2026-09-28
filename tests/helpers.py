"""Testler için ortak yardımcılar - sabit aralıklı sentetik barlar."""

from datetime import datetime, timedelta, timezone

from structure import Bar

BASE_TIME = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)


def make_bars(closes, spread=1.0, step=timedelta(days=1), start=BASE_TIME, highs=None):
    """Her bar: o=önceki kapanış, h=c+spread/2 (ya da highs[i]), l=c-spread/2.
    Gerçek aralık (true range) sabit spread olur -> ATR = spread."""
    bars = []
    prev = closes[0]
    for i, c in enumerate(closes):
        h = highs[i] if highs is not None else max(prev, c) + spread / 2
        low = min(prev, c) - spread / 2
        bars.append(Bar(t=(start + step * i).isoformat().replace("+00:00", "Z"), o=prev, h=h, l=low, c=c, v=1000))
        prev = c
    return bars
