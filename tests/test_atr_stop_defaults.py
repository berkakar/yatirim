import unittest
from unittest import mock

from tests.helpers import make_bars
from stop_algorithms import ATR_VOL_INITIAL_ATR_MULT, STOP_ALGORITHMS


class AtrStopDefaultsTest(unittest.TestCase):
    def test_default_multiplier_is_1_5(self):
        self.assertEqual(ATR_VOL_INITIAL_ATR_MULT, 1.5)
        bars = make_bars([100.0] * 30, spread=2.0)  # ATR = 2
        stop = STOP_ALGORITHMS["atr_volatility"].initial_stop(100.0, "long", bars=bars)
        self.assertAlmostEqual(stop, 97.0)

    def test_user_multiplier_override(self):
        bars = make_bars([100.0] * 30, spread=2.0)
        stop = STOP_ALGORITHMS["atr_volatility"].initial_stop(100.0, "long", bars=bars, initial_atr_mult=2.5)
        self.assertAlmostEqual(stop, 95.0)


class RelativeStrengthStopBarsTest(unittest.TestCase):
    def test_daily_bars_only_for_atr_algorithm(self):
        import relative_strength_core as rs

        bars = make_bars([100.0] * 30, spread=2.0)
        with mock.patch("alpaca_trailing_stop.get_bars_for_timeframe", return_value=bars) as fetch:
            self.assertIs(rs._stop_bars(object(), "TEM", "atr_volatility"), bars)
            self.assertEqual(fetch.call_args.args[2], "1Day")
            self.assertIsNone(rs._stop_bars(object(), "TEM", "breakeven_atr_structure"))
            self.assertEqual(fetch.call_count, 1)

    def test_fetch_error_falls_back(self):
        import relative_strength_core as rs

        with mock.patch("alpaca_trailing_stop.get_bars_for_timeframe", side_effect=RuntimeError("x")):
            self.assertIsNone(rs._stop_bars(object(), "TEM", "atr_volatility"))
        # bars yoksa atr_volatility yedek yüzdeye (%3) düşer
        self.assertAlmostEqual(STOP_ALGORITHMS["atr_volatility"].initial_stop(100.0, "long", bars=None), 97.0)


if __name__ == "__main__":
    unittest.main()
