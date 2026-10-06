"""Stoplar mevcut pozisyona göre ayrılıyor mu (alpaca_trailing_stop.
position_opened_at / _belongs_to_position) ve kurulamayan stop sessiz kalmıyor
mu - MDB vakasının (2026-09-28) yeniden oynatılması. Önbellek storage.py
üzerinden okunup yazıldığı için testler hem SQLite açık (YATIRIM_DB_PATH) hem
kapalı (JSON dosyası) modda, geçici bir dizinde çalışır."""

import os
import tempfile
import unittest
from unittest import mock

import alpaca_trailing_stop as ts
import storage

OPENED = "2026-09-28T13:30:31.38586Z"


def _order(oid, stop, created, status, **times):
    return {"id": oid, "type": "stop", "stop_price": str(stop), "created_at": created, "status": status,
            "client_order_id": f"x-{oid}", **times}


# Alpaca'nın MDB için döndürdüğü gerçek sıra (bkz. symbol_order_history çıktısı):
OLD_POSITION_STOP = _order("old", 368.41, "2026-09-17T17:21:22Z", "filled", filled_at="2026-09-18T14:00:00Z")
CANCELED_LEG = _order("leg", 390.09, "2026-09-24T17:38:21.491378Z", "canceled",
                      canceled_at="2026-09-28T13:30:31.390523Z")


class FakeClient:
    def __init__(self, fills, history, last_price, open_stop=None):
        self.fills, self.history, self.last_price, self.open_stop = fills, history, last_price, open_stop
        self.placed = []

    def get_symbol_fills(self, symbol, days):
        return self.fills

    def get_stop_order_history(self, symbol, limit=50):
        return list(self.history)

    def get_latest_trade_price(self, symbol):
        return self.last_price

    def get_open_stop_order(self, symbol):
        return self.open_stop

    def has_open_exit_order(self, symbol, side):
        return False

    def get_position(self, symbol):
        return {"symbol": symbol, "qty": "20"}

    def place_stop_order(self, symbol, qty, side, stop_price, client_order_id=None, reference_price=None):
        self.placed.append(round(stop_price, 2))
        return {"id": "new", "stop_price": str(stop_price), "qty": str(qty), "client_order_id": client_order_id}

    def replace_stop_price(self, *a, **k):
        return {}


MDB_FILLS = [
    {"side": "buy", "filled_qty": "20", "filled_at": "2026-09-17T17:21:20Z"},   # eski pozisyon
    {"side": "sell", "filled_qty": "20", "filled_at": "2026-09-18T14:00:00Z"},
    {"side": "buy", "filled_qty": "20", "filled_at": OPENED},                    # mevcut pozisyon
]


class _Isolated(unittest.TestCase):
    SQLITE = True

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old_cwd = os.getcwd()
        os.chdir(tmp.name)
        self.addCleanup(os.chdir, old_cwd)
        env = {"YATIRIM_DB_PATH": os.path.join(tmp.name, "y.db")} if self.SQLITE else {}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        if not self.SQLITE:
            os.environ.pop("YATIRIM_DB_PATH", None)


class PositionOwnershipTest(_Isolated):
    def test_opened_at_skips_previous_round_trip(self):
        client = FakeClient(MDB_FILLS, [], 325.0)
        opened = ts.position_opened_at(client, "MDB", 20.0)
        self.assertEqual(opened, ts._parse_iso(OPENED))

    def test_top_up_is_not_an_opening(self):
        fills = MDB_FILLS + [{"side": "buy", "filled_qty": "5", "filled_at": "2026-09-29T15:00:00Z"}]
        self.assertEqual(ts.position_opened_at(FakeClient(fills, [], 1.0), "MDB", 25.0), ts._parse_iso(OPENED))

    def test_position_older_than_window_disables_filter(self):
        fills = [{"side": "buy", "filled_qty": "5", "filled_at": "2026-09-29T15:00:00Z"}]
        self.assertIsNone(ts.position_opened_at(FakeClient(fills, [], 1.0), "MDB", 25.0))

    def test_leg_canceled_at_fill_and_old_stops_do_not_belong(self):
        opened = ts._parse_iso(OPENED)
        self.assertFalse(ts._belongs_to_position(OLD_POSITION_STOP, opened))
        self.assertFalse(ts._belongs_to_position(CANCELED_LEG, opened))

    def test_bracket_leg_activated_by_fill_belongs(self):
        live_leg = _order("live", 300.0, "2026-09-24T17:38:21Z", "new")
        replaced_leg = _order("rep", 300.0, "2026-09-24T17:38:21Z", "replaced", replaced_at="2026-09-29T18:00:00Z")
        opened = ts._parse_iso(OPENED)
        self.assertTrue(ts._belongs_to_position(live_leg, opened))
        self.assertTrue(ts._belongs_to_position(replaced_leg, opened))

    def test_initial_stop_and_restore_ignore_foreign_orders(self):
        after = _order("after", 271.76, "2026-09-29T18:22:45Z", "new")
        client = FakeClient(MDB_FILLS, [OLD_POSITION_STOP, CANCELED_LEG, after], 325.0)
        self.assertEqual(ts.get_initial_stop_price(client, "MDB", 20.0), 271.76)
        with mock.patch.object(ts, "datetime", wraps=ts.datetime) as dt:
            dt.now.return_value = ts._parse_iso("2026-09-30T00:00:00Z")
            self.assertEqual(ts.last_trailed_stop_price(client, "MDB", 20.0), 271.76)

    def test_stale_cached_fields_are_recomputed(self):
        # Eski kuralla yazılmış kayıt (gerçek MDB önbelleği): opened_at yok.
        ts._save_management_start_cache({"MDB": {"earliest_stop_at": "2026-09-17T17:21:22+00:00",
                                                 "initial_stop_price": 368.41}})
        after = _order("after", 271.76, "2026-09-29T18:22:45Z", "new")
        client = FakeClient(MDB_FILLS, [OLD_POSITION_STOP, CANCELED_LEG, after], 340.0)
        self.assertEqual(ts.get_initial_stop_price(client, "MDB", 20.0), 271.76)
        start = ts.get_management_start(client, "MDB", 3650, 20.0)
        self.assertEqual(start, ts._parse_iso(OPENED))


class PositionOwnershipJsonModeTest(PositionOwnershipTest):
    SQLITE = False


class MdbReplayTest(_Isolated):
    """28 Eylül 19:54 UTC: MDB 20 adet @ 308.82, resting stop yok, fiyat 337.75."""

    def _run(self, last_price, naive=271.76):
        client = FakeClient(MDB_FILLS, [OLD_POSITION_STOP, CANCELED_LEG], last_price)
        algo = mock.Mock(initial_stop=mock.Mock(return_value=naive), trail=mock.Mock(return_value=None))
        pos = {"symbol": "MDB", "qty": "20", "avg_entry_price": "308.82"}
        with mock.patch.dict(ts.STOP_ALGORITHMS, {"test": algo}), \
                mock.patch.object(ts, "_stop_bars_for_timeframe", return_value=([], [])), \
                mock.patch.object(ts, "resolve_kwargs", return_value={}), \
                mock.patch.object(ts, "notify_once_per_day") as notify, \
                mock.patch.object(ts, "datetime", wraps=ts.datetime) as dt:
            dt.now.return_value = ts._parse_iso("2026-09-28T19:54:00Z")
            ts.manage_position(client, pos, stop_algorithm="test", stop_settings={})
        return client, notify

    def test_canceled_leg_is_not_restored(self):
        # Eski kod 390.09'u (fiyatın üstünde) göndermeye çalışıp 422 alıyordu.
        client, notify = self._run(337.75)
        self.assertEqual(client.placed, [271.76])
        notify.assert_not_called()

    def test_price_below_naive_stop_places_protective_stop_and_alerts(self):
        client, notify = self._run(260.0)
        self.assertEqual(client.placed, [round(260.0 - (308.82 - 271.76), 2)])
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[1], "stop_breached")


class NotifyOncePerDayTest(_Isolated):
    def test_second_alert_same_day_is_suppressed_and_state_lives_in_storage(self):
        sent = []
        with mock.patch.object(ts, "load_telegram_settings", return_value=("tok", "chat")), \
                mock.patch.object(ts, "send_telegram_message", side_effect=lambda *a: sent.append(a)):
            ts.notify_once_per_day("MDB", "manage_failed", "a")
            ts.notify_once_per_day("MDB", "manage_failed", "b")
            ts.notify_once_per_day("MDB", "stop_breached", "c")
        self.assertEqual([a[2] for a in sent], ["a", "c"])
        self.assertIn("manage_failed", ts._load_management_start_cache()["MDB"]["alerts"])
        self.assertTrue(storage.enabled())
        self.assertEqual(os.listdir("."), ["y.db"] if not os.path.exists("y.db-wal") else sorted(os.listdir(".")))
        self.assertFalse(os.path.exists(ts.MANAGEMENT_START_CACHE_PATH))  # JSON'a yazılmadı


if __name__ == "__main__":
    unittest.main()
