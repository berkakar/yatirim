"""Sahipsiz stop temizliği (alpaca_trailing_stop.cancel_orphan_stops): pozisyonu
olmayan hissede açık kalan stop iptal edilir; normal koruma stopları, dolmamış
bracket bacakları ve yeni kurulmuş stoplar dokunulmadan kalır."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import alpaca_trailing_stop as ts

NOW = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)
OLD = (NOW - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
FRESH = (NOW - timedelta(minutes=3)).isoformat().replace("+00:00", "Z")


def stop(symbol, side="sell", created=OLD, status="new", oid=None, type_="stop"):
    return {"id": oid or f"{symbol}-{status}", "symbol": symbol, "type": type_, "side": side, "status": status,
            "created_at": created, "qty": "2", "stop_price": "258.07", "asset_class": "us_equity"}


class FakeClient:
    def __init__(self, orders, positions):
        self.orders, self.positions, self.canceled = orders, {p["symbol"]: p for p in positions}, []

    def get_open_orders(self):
        return list(self.orders)

    def get_position(self, symbol):
        return self.positions.get(symbol)

    def cancel_order(self, order_id):
        self.canceled.append(order_id)


def pos(symbol, qty="2"):
    return {"symbol": symbol, "qty": qty}


class CancelOrphanStopsTest(unittest.TestCase):
    def _run(self, orders, positions, live_positions=None):
        client = FakeClient(orders, live_positions if live_positions is not None else positions)
        with mock.patch.object(ts, "notify_once_per_day") as notify:
            canceled = ts.cancel_orphan_stops(client, positions, now=NOW)
        return client, canceled, notify

    def test_stop_without_position_is_canceled_and_notified(self):
        client, canceled, notify = self._run([stop("MRVL", oid="orphan")], [])
        self.assertEqual(client.canceled, ["orphan"])
        self.assertEqual(canceled, ["MRVL"])
        self.assertEqual(notify.call_args.args[1], "orphan_stop")

    def test_protective_stop_of_open_position_is_kept(self):
        client, canceled, _ = self._run([stop("MRVL")], [pos("MRVL")])
        self.assertEqual(client.canceled, [])

    def test_held_bracket_leg_is_kept(self):
        # Dolmamış alış limitinin stop bacağı - giriş dolunca devreye girecek.
        client, _, _ = self._run([stop("ACAD", status="held")], [])
        self.assertEqual(client.canceled, [])

    def test_fresh_stop_is_kept(self):
        # Bir modül o an alış yapıp stop kurmuş, pozisyon listesi gecikmiş olabilir.
        client, _, _ = self._run([stop("NUTX", created=FRESH)], [])
        self.assertEqual(client.canceled, [])

    def test_position_opened_meanwhile_is_rechecked(self):
        # İlk listede yok ama iptalden önceki tekrar sorguda pozisyon var.
        client, _, _ = self._run([stop("NUTX")], [], live_positions=[pos("NUTX")])
        self.assertEqual(client.canceled, [])

    def test_sell_stop_on_short_position_is_orphan(self):
        client, _, _ = self._run([stop("XYZ", oid="wrong-side")], [pos("XYZ", qty="-5")])
        self.assertEqual(client.canceled, ["wrong-side"])

    def test_limit_orders_are_ignored(self):
        limit = {**stop("ACAD"), "type": "limit", "side": "buy"}
        client, _, _ = self._run([limit], [])
        self.assertEqual(client.canceled, [])

    def test_stop_limit_without_position_is_canceled(self):
        client, _, _ = self._run([stop("MRVL", type_="stop_limit", oid="sl")], [])
        self.assertEqual(client.canceled, ["sl"])

    def test_order_fetch_failure_does_nothing(self):
        client = mock.Mock()
        client.get_open_orders.side_effect = RuntimeError("down")
        self.assertEqual(ts.cancel_orphan_stops(client, [], now=NOW), [])
        client.cancel_order.assert_not_called()


class RunOnceCallsCleanupTest(unittest.TestCase):
    def test_runs_even_without_positions(self):
        client = mock.Mock()
        client.get_clock.return_value = {"is_open": True}
        client.get_all_positions.return_value = []
        with mock.patch.object(ts, "prune_management_start_cache"), \
                mock.patch.object(ts, "cancel_orphan_stops") as cleanup:
            ts.run_once(client)
        cleanup.assert_called_once_with(client, [])


if __name__ == "__main__":
    unittest.main()
