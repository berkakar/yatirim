"""[Öneri 5] Relative Strength ve Heikin Ashi modüllerinin girişlerinde risk tavanı."""

import unittest
from unittest import mock

import alpaca_buy_points
import heikin_ashi_intraday_core as ha
import relative_strength_core as rs
from tests.fake_client import FakeClient
from tests.helpers import make_bars


def _risk(remaining=5_000.0, max_position_pct=100.0):
    return {"equity": 100_000.0, "risk_per_trade_pct": 0.5, "max_position_pct": max_position_pct, "remaining": remaining}


class RelativeStrengthRiskTest(unittest.TestCase):
    CFG = {"enabled": True, "cash_allocation_pct": 50.0, "top_n": 1}

    def _run(self, risk):
        client = FakeClient(last_price=100.0)
        plan = rs.RebalancePlan(1, ["AAA"], [], ["AAA"], [], {"AAA": 0.1})
        with mock.patch.object(rs, "build_universe", return_value=["AAA"]), \
                mock.patch.object(rs, "filter_by_liquidity", side_effect=lambda c, u, m: u), \
                mock.patch.object(rs, "plan_rebalance", return_value=plan), \
                mock.patch.object(rs, "load_holdings_local", return_value={}), \
                mock.patch.object(rs, "save_holdings_local"), \
                mock.patch.object(rs, "compute_available_cash_for_rotation", return_value=50_000.0), \
                mock.patch.object(alpaca_buy_points, "load_module_risk_context", return_value=risk):
            result = rs.rebalance(client, "berkakar", self.CFG, {})
        entry = next(c for c in client.calls if c[0] == "market_entry")
        self.stops = [c for c in client.calls if c[0] == "place_stop"]
        return entry[2], result

    def test_without_risk_context_uses_cash_share(self):
        qty, _ = self._run(None)
        self.assertEqual(qty, 500)  # 100k x %50 / 1 hisse / 100$

    def test_risk_cap_limits_qty(self):
        qty, result = self._run(_risk())
        self.assertEqual(qty, 333)  # 500$ / (100 x %1.5)
        self.assertEqual(len(result["risk_notes"]), 1)

    def test_portfolio_heat_limits_qty(self):
        qty, _ = self._run(_risk(remaining=150.0))
        self.assertEqual(qty, 100)

    def test_stop_is_tagged(self):
        self._run(_risk())
        self.assertTrue(self.stops[0][3].startswith("stop-initial-AAA-"))


class HeikinAshiRiskTest(unittest.TestCase):
    CFG = {"enabled": True, "cash_allocation_pct": 10.0, "max_positions": 5}

    def _run(self, risk):
        client = FakeClient(last_price=100.0, cash=100_000.0)
        client.clock = {"is_open": True, "next_close": "2099-01-01T20:00:00Z"}
        bars = make_bars([98.0, 99.0, 100.0], spread=2.0)  # son bar low = 99
        cand = ha.HaCandidate(symbol="AAA", price=100.0, score=1.0, reason="test")
        with mock.patch.object(ha, "_prune_closed", side_effect=lambda c, h: h), \
                mock.patch.object(ha, "load_holdings_local", return_value={}), \
                mock.patch.object(ha, "save_holdings_local"), \
                mock.patch.object(ha, "build_universe", return_value=["AAA"]), \
                mock.patch.object(ha, "excluded_symbols", return_value=set()), \
                mock.patch.object(ha, "filter_by_liquidity", side_effect=lambda c, u, m: u), \
                mock.patch.object(ha, "scan_candidates", return_value=[cand]), \
                mock.patch.object(ha, "_fetch_bars", return_value=bars), \
                mock.patch.object(alpaca_buy_points, "load_module_risk_context", return_value=risk):
            summary = ha.run_pass(client, "berkakar", self.CFG, {})
        entries = [c for c in client.calls if c[0] == "market_entry"]
        stops = [c for c in client.calls if c[0] == "place_stop"]
        return (entries[0][2] if entries else 0), stops, summary

    def test_cash_share_binds_when_risk_is_loose(self):
        qty, stops, summary = self._run(_risk())
        self.assertEqual(qty, 20)  # 100k x %10 / 5 / 100$; risk tavanı ~417 adet
        self.assertEqual(summary["risk_notes"], [])
        self.assertTrue(stops[0][3].startswith("stop-initial-AAA-"))

    def test_heat_limits_qty(self):
        qty, _, summary = self._run(_risk(remaining=10.0))
        # son bar low = 98 -> stop = 98 x (1 - %0.2) = 97.804 -> 1R = 2.196 -> 10$ / 2.196 = 4
        self.assertEqual(qty, 4)
        self.assertEqual(len(summary["risk_notes"]), 1)

    def test_no_risk_context_keeps_old_behaviour(self):
        qty, _, _ = self._run(None)
        self.assertEqual(qty, 20)


if __name__ == "__main__":
    unittest.main()
