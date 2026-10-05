import unittest
from datetime import datetime, timezone

from algo_analiz import (
    apply_stop_scenario, count_by, open_stop_levels, portfolio_snapshot, resolve_stop_algorithm_id, scenario_totals,
)
from trade_journal_analysis import STATUS_CLOSED, STATUS_OPEN, Record, group_summary


def _open(symbol, qty, entry, last, algorithm="PBP: ema_cross", realized=0.0):
    return Record(symbol=symbol, algorithm=algorithm, timeframe="1Day", status=STATUS_OPEN, realized=realized,
                  unrealized=(last - entry) * qty, cost_basis=entry * qty, entry_time=None, qty=qty,
                  entry_price=entry, last_price=last)


def _closed(symbol, pnl, algorithm="ORB", r=None, reason="Stop: İlk stop"):
    t = datetime(2026, 9, 1, 14, tzinfo=timezone.utc)
    return Record(symbol=symbol, algorithm=algorithm, timeframe=None, status=STATUS_CLOSED, realized=pnl,
                  unrealized=0.0, cost_basis=1000.0, entry_time=t, exit_time=t, r_multiple=r, exit_reason=reason,
                  entry_session="Seans içi", exit_session="Açılış (ilk 15 dk)")


class StopLevelsTest(unittest.TestCase):
    def test_only_open_sell_stops_and_shield_real_level(self):
        orders = [
            {"symbol": "AAPL", "type": "stop", "side": "sell", "qty": "10", "filled_qty": "0", "stop_price": "190",
             "client_order_id": "stop-breakeven-AAPL-1"},
            # Açılış kalkanı: borsadaki stop 150 ama gerçek seviye 195.00
            {"symbol": "MSFT", "type": "stop", "side": "sell", "qty": "5", "filled_qty": "0", "stop_price": "150",
             "client_order_id": "shield-MSFT-19500-1"},
            {"symbol": "NVDA", "type": "limit", "side": "sell", "qty": "5", "limit_price": "99"},
            {"symbol": "TSLA", "type": "stop", "side": "buy", "qty": "5", "stop_price": "99"},
            {"symbol": "AMD", "type": "stop_limit", "side": "sell", "qty": "4", "filled_qty": "1", "stop_price": "80"},
        ]
        levels = open_stop_levels(orders)
        self.assertEqual(levels["AAPL"], [(190.0, 10.0)])
        self.assertEqual(levels["MSFT"], [(195.0, 5.0)])
        self.assertEqual(levels["AMD"], [(80.0, 3.0)])
        self.assertNotIn("NVDA", levels)
        self.assertNotIn("TSLA", levels)


class StopScenarioTest(unittest.TestCase):
    def test_full_partial_and_missing_stop(self):
        full = _open("AAPL", 10, 200, 220)       # stop 210 -> +100 (şimdi +200)
        partial = _open("MSFT", 10, 100, 120)    # 4 adet 90'da stop, 6 adet anlık -> -40 + 120 = +80
        none = _open("NVDA", 5, 50, 45)          # stop yok
        over = _open("AMD", 2, 10, 12)           # stop adedi pozisyonu aşıyor -> 2 adetle sınırlı
        closed = _closed("X", 30)
        recs = [full, partial, none, over, closed]
        apply_stop_scenario(recs, {"AAPL": [(210.0, 10.0)], "MSFT": [(90.0, 4.0)], "AMD": [(11.0, 5.0)]})
        self.assertAlmostEqual(full.stop_unrealized, 100.0)
        self.assertAlmostEqual(full.current_stop, 210.0)
        self.assertAlmostEqual(partial.stop_unrealized, 80.0)
        self.assertIsNone(none.stop_unrealized)
        self.assertIsNone(none.current_stop)
        self.assertAlmostEqual(over.stop_unrealized, 2.0)
        self.assertIsNone(closed.stop_unrealized)
        # stop_total: kapalıda gerçekleşen, stopsuz açıkta anlık
        self.assertAlmostEqual(closed.stop_total, 30.0)
        self.assertAlmostEqual(none.stop_total, -25.0)

        t = scenario_totals(recs)
        self.assertEqual(t["open_count"], 4)
        self.assertAlmostEqual(t["unrealized"], 200 + 200 - 25 + 4)
        self.assertAlmostEqual(t["stop_unrealized"], 100 + 80 - 25 + 2)
        self.assertAlmostEqual(t["stop_giveback"], (100 + 80 - 25 + 2) - (200 + 200 - 25 + 4))
        self.assertEqual(t["stopless_symbols"], ["NVDA"])
        self.assertEqual(t["closed_count"], 1)
        self.assertAlmostEqual(t["closed_realized"], 30.0)

    def test_group_summary_has_stop_total(self):
        a = _open("AAPL", 10, 200, 220)
        b = _closed("X", -50, algorithm="PBP: ema_cross")
        apply_stop_scenario([a, b], {"AAPL": [(205.0, 10.0)]})
        rows = group_summary([a, b], lambda r: r.algorithm)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["total"], 150.0)
        self.assertAlmostEqual(rows[0]["stop_total"], 0.0)
        self.assertEqual(rows[0]["stopless"], 0)


class StopAlgorithmResolutionTest(unittest.TestCase):
    MODULE_ALGOS = {"Relative Strength": "atr_volatility", "ORB": "opening_range", "Heikin Ashi Gün İçi": "heikin_ashi_exit"}

    def test_module_label_then_pbp_override_then_default(self):
        pbp = {"stop_algorithm": "wait_then_trail", "symbol_settings": {"MSFT": {"stop_algorithm": "adaptive_dynamic"}}}
        self.assertEqual(resolve_stop_algorithm_id(_closed("A", 1, algorithm="ORB"), pbp, self.MODULE_ALGOS), "opening_range")
        self.assertEqual(resolve_stop_algorithm_id(_closed("A", 1, algorithm="PBP: x"), pbp, self.MODULE_ALGOS),
                         "wait_then_trail")
        msft = _closed("MSFT", 1, algorithm="Alım-Stop-Alım: x")
        self.assertEqual(resolve_stop_algorithm_id(msft, pbp, self.MODULE_ALGOS), "adaptive_dynamic")

    def test_open_position_uses_module_holdings_first(self):
        rec = _open("CRWD", 2, 250, 260, algorithm="Bilinmiyor (giriş emri bulunamadı)")
        got = resolve_stop_algorithm_id(rec, {}, self.MODULE_ALGOS, {"Relative Strength": {"CRWD"}, "ORB": set()})
        self.assertEqual(got, "atr_volatility")


class PortfolioSnapshotTest(unittest.TestCase):
    def test_with_and_without_initial_capital(self):
        acct = {"equity": "11000", "cash": "4000", "long_market_value": "7000", "last_equity": "10000"}
        s = portfolio_snapshot(acct, 10000.0)
        self.assertAlmostEqual(s["pl"], 1000.0)
        self.assertAlmostEqual(s["pl_pct"], 10.0)
        self.assertAlmostEqual(s["day_pl"], 1000.0)
        s2 = portfolio_snapshot(acct, None)
        self.assertIsNone(s2["pl"])
        self.assertIsNone(s2["pl_pct"])

    def test_count_by_skips_empty(self):
        recs = [_closed("A", 1), _closed("B", -1, reason=None)]
        self.assertEqual(count_by(recs, "exit_reason"), {"Stop: İlk stop": 1})


if __name__ == "__main__":
    unittest.main()
