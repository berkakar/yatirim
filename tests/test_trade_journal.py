import unittest

from trade_journal import SESSION_EXTENDED, SESSION_OPENING, SESSION_REGULAR, build_round_trips, summarize


def _o(**kw):
    base = {"status": "filled", "filled_qty": "10", "created_at": kw.get("filled_at")}
    return {**base, **kw}


class TradeJournalTest(unittest.TestCase):
    def test_oto_leg_exit_r_and_sessions(self):
        orders = [
            _o(id="b1", symbol="MSFT", side="buy", type="limit", filled_avg_price="100", created_at="2026-09-17T15:00:00Z",
               filled_at="2026-09-18T13:33:00Z", client_order_id="algo-demand_zone-1Day-MSFT-1",
               legs=[{"id": "s1", "side": "sell", "type": "stop", "status": "filled", "stop_price": "96",
                      "filled_avg_price": "95.5", "filled_qty": "10", "created_at": "2026-09-17T15:00:00Z",
                      "filled_at": "2026-09-21T13:32:00Z", "client_order_id": None}]),
        ]
        trips = build_round_trips(orders)
        self.assertEqual(len(trips), 1)
        t = trips[0]
        self.assertAlmostEqual(t.initial_stop, 96)
        self.assertAlmostEqual(t.r_multiple, -1.125)
        self.assertEqual(t.entry_session, SESSION_OPENING)
        self.assertEqual(t.exit_session, SESSION_OPENING)
        self.assertEqual(t.entry_source, "PBP: demand_zone (1Day)")
        self.assertIn("etiketsiz", t.exit_reason)

    def test_tagged_breakeven_exit_and_top_up(self):
        orders = [
            _o(id="b1", symbol="AMZN", side="buy", type="market", filled_avg_price="100", filled_at="2026-09-18T15:00:00Z"),
            _o(id="st", symbol="AMZN", side="sell", type="stop", status="replaced", stop_price="97",
               filled_avg_price=None, filled_at=None, created_at="2026-09-18T15:00:01Z", client_order_id="stop-initial-AMZN-1"),
            _o(id="b2", symbol="AMZN", side="buy", type="market", filled_avg_price="110", filled_at="2026-09-19T15:00:00Z"),
            _o(id="s1", symbol="AMZN", side="sell", type="stop", filled_qty="20", filled_avg_price="105.2",
               filled_at="2026-09-22T21:00:00Z", client_order_id="stop-breakeven-AMZN-2"),
        ]
        t = build_round_trips(orders)[0]
        self.assertAlmostEqual(t.entry_price, 105)
        self.assertEqual(t.qty, 20)
        self.assertEqual(t.exit_reason, "Stop: Breakeven")
        self.assertEqual(t.exit_session, SESSION_EXTENDED)
        self.assertEqual(t.initial_stop, 97)

    def test_orphan_sell_is_skipped_and_summary(self):
        orders = [
            _o(id="x", symbol="MU", side="sell", type="market", filled_avg_price="1066", filled_at="2026-09-24T17:54:00Z"),
            _o(id="b", symbol="NOW", side="buy", type="market", filled_avg_price="135", filled_at="2026-09-08T14:00:00Z",
               client_order_id="orb-buy-NOW-1"),
            _o(id="s", symbol="NOW", side="sell", type="market", filled_avg_price="140", filled_at="2026-09-08T18:00:00Z",
               client_order_id="orb-exit-NOW-2"),
        ]
        trips = build_round_trips(orders)
        self.assertEqual([t.symbol for t in trips], ["NOW"])
        self.assertEqual(trips[0].exit_session, SESSION_REGULAR)
        self.assertEqual(trips[0].entry_source, "ORB")
        s = summarize(trips)
        self.assertEqual(s["trades"], 1)
        self.assertAlmostEqual(s["total_pnl"], 50)
        self.assertIsNone(s["expectancy_r"])


if __name__ == "__main__":
    unittest.main()
