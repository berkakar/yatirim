import unittest

from risk_sizing import (
    load_risk_settings, position_risk, remaining_portfolio_risk, risk_based_qty, top_up_qty_cap,
)


class RiskBasedQtyTest(unittest.TestCase):
    def test_msft_example_from_analysis(self):
        # 100k$ x %0.5 = 500$ risk, 2 x ATR(10.10) = 20.20$ -> 24 adet (~11.9k$)
        r = risk_based_qty(100_000, 494.44, 494.44 - 20.20, 0.5, 20.0)
        self.assertEqual(r.qty, 24)
        self.assertLessEqual(r.risk_dollars, 500)

    def test_volatile_stock_gets_smaller_position(self):
        calm = risk_based_qty(100_000, 100, 96, 0.5, 20.0)      # 1R = 4
        wild = risk_based_qty(100_000, 100, 90, 0.5, 20.0)      # 1R = 10
        self.assertEqual(calm.qty, 125)
        self.assertEqual(wild.qty, 50)

    def test_position_cap(self):
        r = risk_based_qty(100_000, 100, 99.9, 0.5, 20.0)  # çok dar stop -> 5000 adet, tavan 200
        self.assertEqual(r.qty, 200)
        self.assertIn("tavan", r.explanation)

    def test_portfolio_heat_limits_risk(self):
        r = risk_based_qty(100_000, 100, 96, 0.5, 20.0, remaining_portfolio_risk=200)
        self.assertEqual(r.qty, 50)

    def test_invalid_stop_returns_zero(self):
        self.assertEqual(risk_based_qty(100_000, 100, 101, 0.5, 20.0).qty, 0)


class HeatAndTopUpTest(unittest.TestCase):
    def test_position_risk(self):
        self.assertEqual(position_risk(100, 10, 95), 50)
        self.assertEqual(position_risk(100, 10, 101), 0)       # breakeven üstü
        self.assertAlmostEqual(position_risk(100, 10, None), 30)  # bilinmeyen stop %3

    def test_remaining(self):
        self.assertEqual(remaining_portfolio_risk(100_000, 5, 4_000), 1_000)
        self.assertEqual(remaining_portfolio_risk(100_000, 5, 6_000), 0)

    def test_top_up_cap(self):
        # 500$ risk; mevcut risk (100-96)*50=200 -> 300$ yer, eklenen hisse başına 106-96=10 -> 30
        self.assertEqual(top_up_qty_cap(100_000, 0.5, 100, 50, 96, 106), 30)
        self.assertEqual(top_up_qty_cap(100_000, 0.5, 100, 50, 96, 106, remaining_risk=100), 10)
        self.assertEqual(top_up_qty_cap(100_000, 0.5, 100, 50, 96, 95), 0)

    def test_settings_defaults_and_override(self):
        self.assertEqual(load_risk_settings({})["risk_per_trade_pct"], 0.5)
        self.assertEqual(load_risk_settings({"risk_sizing": {"risk_per_trade_pct": 1.0}})["risk_per_trade_pct"], 1.0)


if __name__ == "__main__":
    unittest.main()
