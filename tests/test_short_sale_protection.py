"""[2026-10-06] Açığa satış koruması: pozisyon listesi geçişin başında
çekildikten sonra kapanırsa (ör. guard'ın acil limit emri o arada doldu)
eski adetle yeni bir satış emri - stop, acil limit, market çıkışı -
gönderilmez; adet güncel pozisyonu aşmaz. Seans dışı acil limit emri
etiketlidir ve pozisyonu kalmamışsa sahipsiz emir temizliği onu da iptal
eder."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import alpaca_trailing_stop as ts
from stop_tags import EXT_GUARD_CODE, stop_tag, tag_kind
from tests.fake_client import FakeClient

SETTINGS = {"execution": {"opening_shield_enabled": False, "opening_shield_minutes": 15,
                          "shield_disaster_pct": 4.0, "extended_hours_trail_enabled": False}}
POSITION = {"symbol": "MSFT", "qty": "24", "avg_entry_price": "494.44", "asset_class": "us_equity"}


def _stop(price, qty="24"):
    return {"id": "s1", "symbol": "MSFT", "qty": qty, "side": "sell", "type": "stop", "status": "new",
            "stop_price": f"{price:.2f}", "client_order_id": None, "created_at": "2026-09-18T13:36:00Z"}


def _sell_orders(client):
    return [c for c in client.calls if c[0] in ("place_stop", "ext_limit", "market_exit")]


class LiveExitQtyTest(unittest.TestCase):
    def test_none_when_position_gone(self):
        self.assertIsNone(ts.live_exit_qty(FakeClient(), "MSFT", "long", 24))

    def test_none_when_side_flipped(self):
        client = FakeClient(positions=[{**POSITION, "qty": "-5"}])
        self.assertIsNone(ts.live_exit_qty(client, "MSFT", "long", 24))

    def test_qty_capped_to_live_position(self):
        client = FakeClient(positions=[{**POSITION, "qty": "10"}])
        self.assertEqual(ts.live_exit_qty(client, "MSFT", "long", 24), 10)
        self.assertEqual(ts.live_exit_qty(client, "MSFT", "long", 4), 4)


class GuardShortSaleTest(unittest.TestCase):
    def _guard(self, client, pos=POSITION):
        with mock.patch.object(ts.time, "sleep"):
            ts.guard_position(client, pos, None, None, "atr_volatility", SETTINGS)

    def test_breached_stop_sends_tagged_limit(self):
        client = FakeClient(positions=[POSITION], orders=[_stop(474.24)], last_price=470.0)
        self._guard(client)
        limit = next(o for o in client.orders if o["type"] == "limit")
        self.assertEqual(tag_kind(limit["client_order_id"]), EXT_GUARD_CODE)
        self.assertEqual(float(limit["qty"]), 24)

    def test_no_limit_when_position_closed_meanwhile(self):
        # Pozisyon listesi eski (24 adet) ama Alpaca'da pozisyon artık yok.
        client = FakeClient(positions=[], orders=[_stop(474.24)], last_price=470.0)
        self._guard(client)
        self.assertEqual(_sell_orders(client), [])

    def test_limit_qty_capped_to_live_position(self):
        client = FakeClient(positions=[{**POSITION, "qty": "10"}], orders=[_stop(474.24)], last_price=470.0)
        self._guard(client)
        limit = next(o for o in client.orders if o["type"] == "limit")
        self.assertEqual(float(limit["qty"]), 10)

    def test_no_restore_stop_when_position_closed_meanwhile(self):
        # Acil limit az önce doldu: ne stop ne çıkış emri açık, pozisyon da yok.
        client = FakeClient(positions=[], orders=[], last_price=480.0)
        with mock.patch.object(ts, "last_trailed_stop_price", return_value=474.24):
            self._guard(client)
        self.assertEqual(_sell_orders(client), [])

    def test_no_second_limit_when_position_closed_meanwhile(self):
        client = FakeClient(positions=[], orders=[], last_price=470.0)
        with mock.patch.object(ts, "last_trailed_stop_price", return_value=474.24):
            self._guard(client)
        self.assertEqual(_sell_orders(client), [])


class ManagePositionShortSaleTest(unittest.TestCase):
    def test_fallback_stop_skipped_when_position_closed_meanwhile(self):
        client = FakeClient(positions=[], orders=[], last_price=480.0)
        algo = mock.Mock(initial_stop=mock.Mock(return_value=470.0), trail=mock.Mock(return_value=None))
        with mock.patch.dict(ts.STOP_ALGORITHMS, {"test": algo}), \
                mock.patch.object(ts, "_stop_bars_for_timeframe", return_value=([], [])), \
                mock.patch.object(ts, "get_management_start", return_value=None), \
                mock.patch.object(ts, "last_trailed_stop_price", return_value=None), \
                mock.patch.object(ts, "resolve_kwargs", return_value={}):
            ts.manage_position(client, POSITION, stop_algorithm="test", stop_settings={})
        self.assertEqual(_sell_orders(client), [])


class RestoreFromShieldShortSaleTest(unittest.TestCase):
    def test_no_market_exit_when_position_gone(self):
        client = FakeClient(positions=[], orders=[_stop(455.27)], last_price=470.0)
        with mock.patch.object(ts.time, "sleep"):
            result = ts.restore_from_shield(client, "MSFT", "long", 24, client.orders[0], 474.24)
        self.assertIsNone(result)
        self.assertEqual(_sell_orders(client), [])


NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


class OrphanGuardLimitTest(unittest.TestCase):
    def _limit(self, tag, created):
        return {"id": "L1", "symbol": "MSFT", "type": "limit", "side": "sell", "status": "new", "qty": "24",
                "limit_price": "468.00", "client_order_id": tag, "asset_class": "us_equity",
                "created_at": created.isoformat().replace("+00:00", "Z")}

    def _run(self, order, positions):
        client = FakeClient(positions=positions, orders=[order])
        with mock.patch.object(ts, "notify_once_per_day"):
            return ts.cancel_orphan_stops(client, positions, now=NOW), order

    def test_tagged_guard_limit_without_position_is_canceled_even_if_fresh(self):
        canceled, order = self._run(self._limit(stop_tag(EXT_GUARD_CODE, "MSFT"), NOW - timedelta(minutes=1)), [])
        self.assertEqual(canceled, ["MSFT"])
        self.assertEqual(order["status"], "canceled")

    def test_tagged_guard_limit_with_position_is_kept(self):
        canceled, _ = self._run(self._limit(stop_tag(EXT_GUARD_CODE, "MSFT"), NOW - timedelta(hours=1)), [POSITION])
        self.assertEqual(canceled, [])

    def test_untagged_limit_is_ignored(self):
        canceled, _ = self._run(self._limit(None, NOW - timedelta(hours=1)), [])
        self.assertEqual(canceled, [])

    def test_guard_run_cleans_orphans_even_without_positions(self):
        client = FakeClient(positions=[], orders=[self._limit(stop_tag(EXT_GUARD_CODE, "MSFT"), NOW)])
        with mock.patch.object(ts, "extended_hours_session", return_value="pre-market"), \
                mock.patch.object(ts, "notify_once_per_day"):
            ts.run_extended_hours_guard(client)
        self.assertEqual(client.orders[0]["status"], "canceled")


if __name__ == "__main__":
    unittest.main()
