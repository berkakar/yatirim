import unittest
from datetime import datetime, timezone

from trade_journal import entry_source, entry_source_parts, walk_fills
from trade_journal_analysis import (
    STATUS_CLOSED, STATUS_OPEN, UNKNOWN_SOURCE, closed_records, cumulative_realized, filter_records,
    group_summary, open_r_multiple, open_records, pnl_matrix,
)


def _o(**kw):
    base = {"status": "filled", "filled_qty": "10", "created_at": kw.get("filled_at"), "type": "market"}
    return {**base, **kw}


ORDERS = [
    # NVDA: ORB, kapandı +50
    _o(id="1", symbol="NVDA", side="buy", filled_avg_price="100", filled_at="2026-09-01T14:00:00Z",
       client_order_id="orb-buy-NVDA-1"),
    _o(id="2", symbol="NVDA", side="sell", filled_avg_price="105", filled_at="2026-09-01T18:00:00Z",
       client_order_id="orb-exit-NVDA-2"),
    # NVDA: PBP ema 1Day, kapandı -30, ilk stop 97 -> R = -1
    _o(id="3", symbol="NVDA", side="buy", filled_avg_price="100", filled_at="2026-09-05T14:00:00Z",
       client_order_id="algo-ema_cross-1Day-NVDA-3"),
    _o(id="3s", symbol="NVDA", side="sell", type="stop", status="replaced", stop_price="97", filled_avg_price=None,
       filled_at=None, created_at="2026-09-05T14:00:01Z", client_order_id="stop-initial-NVDA-3"),
    _o(id="4", symbol="NVDA", side="sell", type="stop", filled_avg_price="97", filled_at="2026-09-06T14:00:00Z",
       client_order_id="stop-initial-NVDA-4"),
    # AAPL: PBP ema 1Hour, açık; 10 al, 4 sat (+40 gerçekleşmiş), 6 açık
    _o(id="5", symbol="AAPL", side="buy", filled_avg_price="200", filled_at="2026-09-10T14:00:00Z",
       client_order_id="algo-ema_cross-1Hour-AAPL-5"),
    _o(id="5s", symbol="AAPL", side="sell", type="stop", status="new", stop_price="190", filled_avg_price=None,
       filled_at=None, created_at="2026-09-10T14:00:01Z", client_order_id="stop-initial-AAPL-5"),
    _o(id="6", symbol="AAPL", side="sell", filled_qty="4", filled_avg_price="210", filled_at="2026-09-11T14:00:00Z"),
    # TSLA: rebuy
    _o(id="7", symbol="TSLA", side="buy", filled_avg_price="50", filled_at="2026-09-12T14:00:00Z",
       client_order_id="rebuy-demand_zone-1Day-TSLA-7"),
]

POSITIONS = [
    {"symbol": "AAPL", "qty": "6", "avg_entry_price": "200", "cost_basis": "1200", "current_price": "220",
     "unrealized_pl": "120"},
    {"symbol": "TSLA", "qty": "10", "avg_entry_price": "50", "cost_basis": "500", "current_price": "45",
     "unrealized_pl": "-50"},
    {"symbol": "OLD", "qty": "1", "avg_entry_price": "10", "cost_basis": "10", "current_price": "11",
     "unrealized_pl": "1"},
]

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


class EntrySourceTest(unittest.TestCase):
    def test_parts(self):
        self.assertEqual(entry_source_parts("algo-demand_zone-1Day-MSFT-1"), ("PBP: demand_zone", "1Day"))
        self.assertEqual(entry_source_parts("rebuy-ema-1Hour-X-1"), ("Alım-Stop-Alım: ema", "1Hour"))
        self.assertEqual(entry_source_parts("orb-buy-X-1"), ("ORB", None))
        self.assertEqual(entry_source_parts(None)[0], "Elle / bilinmiyor")
        self.assertEqual(entry_source("algo-demand_zone-1Day-MSFT-1"), "PBP: demand_zone (1Day)")


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        trips, lots = walk_fills(ORDERS, now=NOW)
        self.trips, self.lots = trips, lots
        self.records = closed_records(trips) + open_records(POSITIONS, lots)

    def test_open_lots(self):
        self.assertEqual(set(self.lots), {"AAPL", "TSLA"})
        aapl = self.lots["AAPL"]
        self.assertAlmostEqual(aapl.qty, 6)
        self.assertAlmostEqual(aapl.realized_pnl, 40)
        self.assertAlmostEqual(aapl.initial_stop, 190)

    def test_records(self):
        by = {(r.symbol, r.status, r.algorithm): r for r in self.records}
        aapl = by[("AAPL", STATUS_OPEN, "PBP: ema_cross")]
        self.assertEqual(aapl.timeframe, "1Hour")
        self.assertAlmostEqual(aapl.total, 160)
        self.assertAlmostEqual(aapl.cost_basis, 2000)
        self.assertAlmostEqual(open_r_multiple(aapl), 2.0)
        self.assertIn(("OLD", STATUS_OPEN, UNKNOWN_SOURCE), by)
        self.assertIn(("TSLA", STATUS_OPEN, "Alım-Stop-Alım: demand_zone"), by)
        self.assertAlmostEqual(by[("NVDA", STATUS_CLOSED, "PBP: ema_cross")].r_multiple, -1.0)

    def test_group_by_algorithm(self):
        rows = {row["key"]: row for row in group_summary(self.records, lambda r: r.algo_label(False))}
        ema = rows["PBP: ema_cross"]
        self.assertEqual((ema["closed"], ema["open"]), (1, 1))
        self.assertAlmostEqual(ema["realized"], -30 + 40)
        self.assertAlmostEqual(ema["unrealized"], 120)
        self.assertAlmostEqual(ema["total"], 130)
        self.assertEqual(ema["profit_factor"], 0.0)
        self.assertAlmostEqual(rows["ORB"]["win_rate"], 100)
        self.assertEqual(rows["ORB"]["profit_factor"], float("inf"))
        split = {row["key"] for row in group_summary(self.records, lambda r: r.algo_label(True))}
        self.assertIn("PBP: ema_cross (1Hour)", split)
        self.assertIn("PBP: ema_cross (1Day)", split)

    def test_matrix_filter_and_cumulative(self):
        m = pnl_matrix(self.records, False)
        self.assertAlmostEqual(m[("NVDA", "ORB")], 50)
        self.assertAlmostEqual(m[("NVDA", "PBP: ema_cross")], -30)
        since = datetime(2026, 9, 4, tzinfo=timezone.utc)
        kept = filter_records(self.records, since=since, include_manual=False)
        self.assertNotIn("OLD", {r.symbol for r in kept})
        self.assertNotIn("ORB", {r.algorithm for r in kept})
        self.assertEqual({r.status for r in filter_records(self.records, status=STATUS_OPEN)}, {STATUS_OPEN})
        pts = cumulative_realized(self.records, False)
        self.assertEqual([p[1] for p in pts], ["ORB", "PBP: ema_cross"])


class RecentOrdersPaginationTest(unittest.TestCase):
    def test_pages_until_short_page_and_dedupes(self):
        from datetime import timedelta
        from alpaca_client import AlpacaClient

        base = datetime(2026, 9, 27, tzinfo=timezone.utc)
        # 1200 emir, azalan zaman; iki emir aynı anda (sayfa sınırında) gönderilmiş
        all_orders = [{"id": str(i), "submitted_at": (base - timedelta(minutes=i)).isoformat().replace("+00:00", "Z")}
                      for i in range(1200)]
        all_orders[500]["submitted_at"] = all_orders[499]["submitted_at"]
        calls = []

        class Resp:
            def __init__(self, data):
                self.data = data

            def raise_for_status(self):
                pass

            def json(self):
                return self.data

        def fake_get(path, params=None):
            calls.append(dict(params))
            until = params.get("until")
            rows = all_orders
            if until:
                u = datetime.fromisoformat(until)
                rows = [o for o in rows if datetime.fromisoformat(o["submitted_at"].replace("Z", "+00:00")) < u]
            return Resp(rows[:params["limit"]])

        client = AlpacaClient("k", "s")
        client._get = fake_get
        got = client.get_recent_orders(days=365, limit=500)
        self.assertEqual(len(got), 1200)
        self.assertEqual(len({o["id"] for o in got}), 1200)
        self.assertGreaterEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
