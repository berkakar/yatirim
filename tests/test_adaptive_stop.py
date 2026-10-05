import unittest

from indicators import atr
from stop_algorithms import (
    STOP_ALGORITHMS, StopContext, adaptive_dynamic_initial_stop, adaptive_dynamic_trail, efficiency_ratio,
    resolve_kwargs,
)
from structure import Bar
from tests.helpers import make_bars


def _with_swing_low(low: float) -> list[Bar]:
    """Düz 100 barları, ortasında belirgin bir swing low (low) ile; ATR ~2."""
    bars = make_bars([100.0] * 20, spread=2.0)
    k = 14
    b = bars[k]
    bars[k] = Bar(t=b.t, o=b.o, h=b.h, l=low, c=b.c, v=b.v)
    return bars


class AdaptiveInitialStopTest(unittest.TestCase):
    def test_uses_swing_low_inside_atr_band(self):
        bars = _with_swing_low(97.0)
        a = atr(bars, 14)
        expected = 97.0 - 0.25 * a
        self.assertLess(100.0 - expected, 2.0 * a)
        self.assertGreater(100.0 - expected, 1.0 * a)
        self.assertAlmostEqual(adaptive_dynamic_initial_stop(100.0, "long", bars=bars), expected)

    def test_far_swing_low_is_clamped_to_max_atr(self):
        bars = _with_swing_low(80.0)
        a = atr(bars, 14)
        self.assertAlmostEqual(adaptive_dynamic_initial_stop(100.0, "long", bars=bars), 100.0 - 2.0 * a)

    def test_no_swing_uses_initial_atr_mult(self):
        bars = make_bars([100.0 + i for i in range(20)], spread=2.0)  # tek yönlü, swing low yok
        a = atr(bars, 14)
        self.assertAlmostEqual(adaptive_dynamic_initial_stop(119.0, "long", bars=bars, structure_lookback=20),
                               119.0 - 2.0 * a)

    def test_fallback_without_bars(self):
        self.assertAlmostEqual(adaptive_dynamic_initial_stop(100.0, "long", bars=None), 97.0)
        self.assertAlmostEqual(adaptive_dynamic_initial_stop(100.0, "short", bars=None), 103.0)

    def test_settings_resolve(self):
        algo = STOP_ALGORITHMS["adaptive_dynamic"]
        kwargs = resolve_kwargs(algo.initial_stop, {"max_atr_mult": 3.0, "structure_lookback": 30.0},
                                {"atr_period": 10, "swing_order": 3})
        self.assertEqual(kwargs, {"max_atr_mult": 3.0, "structure_lookback": 30, "swing_order": 3, "atr_period": 10})


class EfficiencyRatioTest(unittest.TestCase):
    def test_straight_line_is_one_and_chop_is_zero(self):
        self.assertAlmostEqual(efficiency_ratio([float(i) for i in range(30)], 20), 1.0)
        self.assertAlmostEqual(efficiency_ratio([100.0, 101.0] * 15, 20), 0.0)
        self.assertIsNone(efficiency_ratio([1.0, 2.0], 20))


class AdaptiveTrailTest(unittest.TestCase):
    def _ctx(self, closes, stop, entry=100.0, initial=96.0):
        bars = make_bars(closes, spread=2.0)
        return StopContext(side="long", entry_price=entry, current_stop_price=stop, bars=bars,
                           initial_stop_price=initial, history_bars=bars)

    def test_no_trail_before_trail_start(self):
        ctx = self._ctx([100.0] * 16 + [104.0], stop=96.0)  # MFE ~1.25R < 1.5R
        self.assertIsNone(adaptive_dynamic_trail(ctx))

    def test_chandelier_tightens_with_profit(self):
        closes = [100.0] * 15 + [100.0 + 2 * i for i in range(1, 11)]  # 120'ye tırmanış
        ctx = self._ctx(closes, stop=96.0)
        loose = adaptive_dynamic_trail(ctx, tighten_per_r=0.0)
        tight = adaptive_dynamic_trail(ctx, tighten_per_r=0.5)
        self.assertIsNotNone(loose)
        self.assertIsNotNone(tight)
        self.assertGreater(tight.price, loose.price)
        self.assertLess(tight.price, closes[-1])
        a = atr(ctx.history_bars, 14)
        extreme = max(b.h for b in ctx.bars)
        self.assertAlmostEqual(loose.price, extreme - 5.0 * a)

    def test_min_trail_mult_floor(self):
        closes = [100.0] * 15 + [100.0 + 4 * i for i in range(1, 11)]
        ctx = self._ctx(closes, stop=96.0)
        decision = adaptive_dynamic_trail(ctx, tighten_per_r=5.0, min_trail_atr_mult=2.0)
        a = atr(ctx.history_bars, 14)
        self.assertAlmostEqual(decision.price, max(b.h for b in ctx.bars) - 2.0 * a)

    def test_never_loosens_and_breakeven_optional(self):
        closes = [100.0] * 16 + [105.0]
        ctx = self._ctx(closes, stop=96.0)
        self.assertIsNone(adaptive_dynamic_trail(ctx))  # varsayılan: breakeven kapalı
        be = adaptive_dynamic_trail(ctx, breakeven_r=1.0)
        self.assertIsNotNone(be)
        self.assertGreater(be.price, 100.0)
        self.assertIsNone(adaptive_dynamic_trail(self._ctx(closes, stop=be.price + 1), breakeven_r=1.0))


if __name__ == "__main__":
    unittest.main()
