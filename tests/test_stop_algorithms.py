import unittest

from stop_algorithms import (
    STOP_ALGORITHMS, StopContext, atr_volatility_initial_stop, atr_volatility_trail,
    breakeven_atr_structure_trail, resolve_kwargs,
)
from indicators import atr
from tests.helpers import make_bars


class AtrVolatilityInitialStopTest(unittest.TestCase):
    def test_stop_is_two_atr_below_entry(self):
        bars = make_bars([100.0] * 20, spread=2.0)
        self.assertAlmostEqual(atr(bars, 14), 2.0)
        self.assertAlmostEqual(atr_volatility_initial_stop(100.0, "long", bars=bars), 96.0)
        self.assertAlmostEqual(atr_volatility_initial_stop(100.0, "short", bars=bars), 104.0)

    def test_distance_is_capped_by_max_stop_pct(self):
        bars = make_bars([100.0] * 20, spread=10.0)  # 2xATR = 20 > %12
        self.assertAlmostEqual(atr_volatility_initial_stop(100.0, "long", bars=bars), 88.0)

    def test_fallback_when_atr_unavailable(self):
        self.assertAlmostEqual(atr_volatility_initial_stop(100.0, "long", bars=None), 97.0)
        self.assertAlmostEqual(atr_volatility_initial_stop(100.0, "long", bars=make_bars([100.0] * 5)), 97.0)

    def test_registered_and_settings_resolve(self):
        algo = STOP_ALGORITHMS["atr_volatility"]
        kwargs = resolve_kwargs(algo.initial_stop, {"initial_atr_mult": 1.5, "max_stop_pct": 8.0}, {"atr_period": 10})
        self.assertEqual(kwargs, {"initial_atr_mult": 1.5, "atr_period": 10, "max_stop_pct": 0.08})


class AtrVolatilityTrailTest(unittest.TestCase):
    def setUp(self):
        self.history = make_bars([100.0] * 20, spread=2.0)  # ATR = 2 -> 1R = 4

    def _ctx(self, closes, current_stop=96.0, highs=None):
        bars = make_bars(closes, spread=2.0, highs=highs)
        return StopContext(side="long", entry_price=100.0, current_stop_price=current_stop, bars=bars,
                           initial_stop_price=96.0, history_bars=self.history)

    def test_no_breakeven_before_one_r_close(self):
        # Kapanış +3 (0.75R) - intrabar tepe +6 olsa bile breakeven yok.
        self.assertIsNone(atr_volatility_trail(self._ctx([101, 103, 103], highs=[102, 106, 104])))

    def test_breakeven_with_atr_buffer_after_one_r_close(self):
        # Son iki bar kapanmış sayılır (eski zaman damgaları) - kapanış 104.5 >= 104.
        decision = atr_volatility_trail(self._ctx([101, 104.5, 105]))
        self.assertIsNotNone(decision)
        self.assertAlmostEqual(decision.price, 100.2)  # giriş + 0.1 x ATR
        self.assertIn("breakeven", decision.reason)

    def test_chandelier_after_two_r(self):
        decision = atr_volatility_trail(self._ctx([102, 106, 109, 110], current_stop=100.2))
        self.assertIsNotNone(decision)
        # en yüksek fiyat 111 (110 + spread/2), ATR history_bars'tan = 2 -> 111 - 3 x 2
        self.assertIn("chandelier", decision.reason)
        self.assertAlmostEqual(decision.price, 105.0)

    def test_never_loosens(self):
        self.assertIsNone(atr_volatility_trail(self._ctx([101, 104.5, 105], current_stop=103.0)))

    def test_uses_initial_stop_for_one_r(self):
        # İlk stop 90 -> 1R = 10: +4.5 kapanış breakeven için yetmez.
        bars = make_bars([101, 104.5, 105], spread=2.0)
        ctx = StopContext(side="long", entry_price=100.0, current_stop_price=90.0, bars=bars,
                          initial_stop_price=90.0, history_bars=self.history)
        self.assertIsNone(atr_volatility_trail(ctx))


class BreakevenBufferTest(unittest.TestCase):
    """[Öneri 2] Sabit-% algoritmada breakeven %1.5'te ve girişin %0.2 üstünde."""

    def _ctx(self, last_close):
        bars = make_bars([100.0, last_close], spread=0.2)
        return StopContext(side="long", entry_price=100.0, current_stop_price=98.5, bars=bars, daily_closes=[])

    def test_one_percent_no_longer_triggers(self):
        self.assertIsNone(breakeven_atr_structure_trail(self._ctx(101.0), trend_ema_period=0))

    def test_one_and_half_percent_triggers_with_buffer(self):
        decision = breakeven_atr_structure_trail(self._ctx(101.6), trend_ema_period=0)
        self.assertIsNotNone(decision)
        self.assertAlmostEqual(decision.price, 100.2)
        self.assertEqual(decision.reason, "breakeven")

    def test_buffer_can_be_disabled(self):
        decision = breakeven_atr_structure_trail(self._ctx(101.6), trend_ema_period=0, breakeven_buffer_pct=0.0)
        self.assertAlmostEqual(decision.price, 100.0)


if __name__ == "__main__":
    unittest.main()
