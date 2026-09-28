"""[Öneri 4] Açılış öncesi limit iptali ve ilk 15 dakikada alım yapılmaması."""

import unittest

import alpaca_buy_points as abp
from tests.fake_client import FakeClient


def _limit(symbol, ext=False, tag="algo-demand_zone-1Day-X-1"):
    return {"id": f"l-{symbol}-{ext}", "symbol": symbol, "qty": "10", "side": "buy", "type": "limit",
            "status": "new", "limit_price": "100.00", "client_order_id": tag, "extended_hours": ext}


class EntryTimingTest(unittest.TestCase):
    def test_after_hours_cancels_only_regular_session_limits(self):
        client = FakeClient(orders=[_limit("AAA"), _limit("BBB", ext=True), _limit("CCC", tag="manual-1")])
        canceled = abp.cancel_pullback_limit_buys(client, include_extended_hours_orders=False)
        self.assertEqual(canceled, ["AAA"])

    def test_pre_market_cancels_all_system_limits(self):
        client = FakeClient(orders=[_limit("AAA"), _limit("BBB", ext=True), _limit("CCC", tag="manual-1")])
        canceled = abp.cancel_pullback_limit_buys(client, include_extended_hours_orders=True)
        self.assertEqual(sorted(canceled), ["AAA", "BBB"])

    def test_check_symbol_in_guard_cancels_and_buys_nothing(self):
        client = FakeClient(orders=[_limit("AAA")])
        spent = abp.check_symbol(client, "AAA", 10, 100_000, "demand_zone", "1Day", in_entry_guard=True)
        self.assertEqual(spent, 0.0)
        self.assertIsNone(client.get_open_limit_buy_order("AAA"))

    def test_entry_timing_defaults(self):
        self.assertEqual(abp.load_entry_timing({}), {"pre_open_cancel_enabled": True, "entry_guard_minutes": 15})


class RiskContextTest(unittest.TestCase):
    def test_open_risk_counts_positions_and_pending(self):
        pos = {"symbol": "MSFT", "qty": "24", "avg_entry_price": "494.44", "asset_class": "us_equity"}
        stop = {"id": "s", "symbol": "MSFT", "qty": "24", "side": "sell", "type": "stop", "status": "new",
                "stop_price": "474.24", "client_order_id": None}
        client = FakeClient(positions=[pos], orders=[stop, _limit("AAA")], equity=100_000)
        risk = abp.build_risk_context(client, {})
        # MSFT (494.44-474.24)*24 = 484.8 + bekleyen limit 500 -> kalan 5000 - 984.8
        self.assertAlmostEqual(risk["remaining"], 5000 - 484.8 - 500, places=1)


if __name__ == "__main__":
    unittest.main()
