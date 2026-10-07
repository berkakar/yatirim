"""Oynaklık (ATR) Stop: günlük ATR'de aykırı gün (kazanç boşluğu) ayıklaması -
MDB 28.09 vakası. Gün içi barlarda ayıklama yapılmaz."""

import inspect
import unittest
from datetime import datetime, timedelta, timezone

from indicators import atr
from stop_algorithms import (
    STOP_ALGORITHMS, StopContext, atr_volatility_initial_stop, atr_volatility_trail, resolve_kwargs, robust_atr,
)
from structure import Bar


def _bars(ranges, step):
    t0 = datetime(2026, 9, 1, 4, tzinfo=timezone.utc)
    bars = [Bar(t=t0.isoformat().replace("+00:00", "Z"), o=100, h=100.5, l=99.5, c=100, v=1)]
    for i, r in enumerate(ranges, start=1):
        t = (t0 + step * i).isoformat().replace("+00:00", "Z")
        bars.append(Bar(t=t, o=100, h=100 + r / 2, l=100 - r / 2, c=100, v=1))
    return bars


NORMAL = [4.0] * 13
DAILY = timedelta(days=1)
HALF_HOUR = timedelta(minutes=30)


class RobustAtrTest(unittest.TestCase):
    def test_outlier_day_is_excluded_on_daily_bars(self):
        bars = _bars(NORMAL + [40.0], DAILY)
        self.assertAlmostEqual(atr(bars, 14), (13 * 4 + 40) / 14)
        self.assertAlmostEqual(robust_atr(bars, 14, 3.0), 4.0)

    def test_normal_volatility_unchanged(self):
        bars = _bars([3, 4, 5, 6, 4, 3, 5, 4, 6, 5, 4, 3, 5, 9], DAILY)  # 9 < 3 x medyan
        self.assertAlmostEqual(robust_atr(bars, 14, 3.0), atr(bars, 14))

    def test_intraday_bars_are_not_filtered(self):
        bars = _bars(NORMAL + [40.0], HALF_HOUR)
        self.assertAlmostEqual(robust_atr(bars, 14, 3.0), atr(bars, 14))

    def test_disabled(self):
        bars = _bars(NORMAL + [40.0], DAILY)
        self.assertAlmostEqual(robust_atr(bars, 14, 0.0), atr(bars, 14))

    def test_too_few_bars(self):
        self.assertIsNone(robust_atr(_bars([4.0] * 5, DAILY), 14, 3.0))


class AtrVolatilityUsesRobustAtrTest(unittest.TestCase):
    def test_initial_stop_ignores_gap_day(self):
        bars = _bars(NORMAL + [40.0], DAILY)
        self.assertAlmostEqual(atr_volatility_initial_stop(100.0, "long", bars=bars), 100.0 - 1.5 * 4.0)

    def test_default_is_on_and_settable(self):
        algo = STOP_ALGORITHMS["atr_volatility"]
        for fn in (algo.initial_stop, algo.trail):
            self.assertEqual(inspect.signature(fn).parameters["atr_outlier_mult"].default, 3.0)
            self.assertEqual(resolve_kwargs(fn, {"atr_outlier_mult": 0}, {})["atr_outlier_mult"], 0.0)

    def test_chandelier_distance_uses_robust_atr(self):
        hist = _bars(NORMAL + [40.0], DAILY)
        pos = [Bar(t="2026-09-16T04:00:00Z", o=112, h=113, l=111, c=112, v=1)] * 2
        ctx = StopContext(side="long", entry_price=100.0, current_stop_price=94.0, bars=pos,
                          initial_stop_price=94.0, history_bars=hist)
        decision = atr_volatility_trail(ctx)
        self.assertIn("chandelier", decision.reason)
        self.assertAlmostEqual(decision.price, 113 - 2 * 4.0)  # günlük: 2xATR

    def test_tiered_tightening(self):
        hist = _bars(NORMAL + [4.0], DAILY)
        pos = [Bar(t="2026-09-16T04:00:00Z", o=124, h=124, l=123, c=124, v=1)] * 2  # 4R kâr (1R=6)
        ctx = StopContext(side="long", entry_price=100.0, current_stop_price=94.0, bars=pos,
                          initial_stop_price=94.0, history_bars=hist)
        legacy = {"daily_trail_start_r": 2.0, "daily_trail_atr_mult": 3.0}
        loose = atr_volatility_trail(ctx, **legacy)
        tight = atr_volatility_trail(ctx, trail_tighten_per_r=0.25, **legacy)
        self.assertAlmostEqual(loose.price, 124 - 3 * 4.0)
        self.assertAlmostEqual(tight.price, 124 - 2.5 * 4.0)


class DailyTrailSettingsTest(unittest.TestCase):
    """[2026-10-07] Günlük barlarda chandelier 1.5R'de başlar, 2xATR geriden
    gelir; gün içi barlarda trail_start_r / trail_atr_mult (2R, 3x) geçerli."""

    def _ctx(self, step, high):
        hist = _bars([4.0] * 14, step)
        last = hist[-1].t
        pos = [Bar(t=last, o=high - 1, h=high, l=high - 2, c=high - 1, v=1)] * 2
        return StopContext(side="long", entry_price=100.0, current_stop_price=94.0, bars=pos,
                           initial_stop_price=94.0, history_bars=hist)  # 1R = 6, ATR = 4

    def test_daily_starts_at_1_5r_with_2x(self):
        decision = atr_volatility_trail(self._ctx(DAILY, 110.0))  # 1.67R
        self.assertIn("chandelier (2xATR)", decision.reason)
        self.assertAlmostEqual(decision.price, 110 - 2 * 4.0)

    def test_intraday_keeps_2r_and_3x(self):
        decision = atr_volatility_trail(self._ctx(HALF_HOUR, 110.0))  # 1.67R < 2R: yalnız breakeven
        self.assertIn("breakeven", decision.reason)
        decision = atr_volatility_trail(self._ctx(HALF_HOUR, 115.0))  # 2.5R
        self.assertAlmostEqual(decision.price, 115 - 3 * 4.0)

    def test_saved_trail_settings_do_not_override_daily(self):
        algo = STOP_ALGORITHMS["atr_volatility"]
        kwargs = resolve_kwargs(algo.trail, {"trail_start_r": 2.0, "trail_atr_mult": 3.0}, {})
        decision = atr_volatility_trail(self._ctx(DAILY, 110.0), **kwargs)
        self.assertAlmostEqual(decision.price, 110 - 2 * 4.0)


if __name__ == "__main__":
    unittest.main()
