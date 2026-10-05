import math
import unittest
from datetime import datetime, timezone

from backtest_engine import max_drawdown_pct, trade_stats
from structure import Bar


def _bar(day: int, c: float) -> Bar:
    return Bar(t=f"2026-01-{day:02d}T00:00:00Z", o=c, h=c, l=c, c=c, v=1000)


def _trade(side: str, day: int, price: float, qty: float = 10) -> dict:
    return {"side": side, "time": f"2026-01-{day:02d}T00:00:00Z", "price": price, "qty": qty, "reason": ""}


class TradeStatsTest(unittest.TestCase):
    def test_win_rate_and_profit_factor(self):
        trades = [
            _trade("buy", 1, 100), _trade("sell", 2, 110),   # +100
            _trade("buy", 3, 100), _trade("sell", 4, 95),    # -50
            _trade("buy", 5, 100), _trade("sell", 6, 120),   # +200
        ]
        stats = trade_stats(trades)
        self.assertEqual(stats["closed"], 3)
        self.assertAlmostEqual(stats["win_rate"], 66.67)
        self.assertAlmostEqual(stats["profit_factor"], 6.0)

    def test_no_closed_trades(self):
        self.assertEqual(trade_stats([_trade("buy", 1, 100)]),
                         {"closed": 0, "win_rate": None, "profit_factor": None})

    def test_no_losses_is_infinite(self):
        stats = trade_stats([_trade("buy", 1, 100), _trade("sell", 2, 110)])
        self.assertTrue(math.isinf(stats["profit_factor"]))
        self.assertEqual(stats["win_rate"], 100.0)


class MaxDrawdownTest(unittest.TestCase):
    def test_marks_open_position_to_market(self):
        # 1000$ nakit, 10 adet 100$'dan alım -> 120 (zirve 1200) -> 90 (900) -> 90'dan satış.
        bars = [_bar(1, 100), _bar(2, 120), _bar(3, 90), _bar(4, 95)]
        trades = [_trade("buy", 1, 100), _trade("sell", 3, 90)]
        dd = max_drawdown_pct(trades, bars, 1000.0, datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertAlmostEqual(dd, 25.0)

    def test_flat_cash_has_no_drawdown(self):
        bars = [_bar(1, 100), _bar(2, 50)]
        self.assertEqual(max_drawdown_pct([], bars, 1000.0, datetime(2026, 1, 1, tzinfo=timezone.utc)), 0.0)


if __name__ == "__main__":
    unittest.main()
