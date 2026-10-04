"""Stop her koşulda kurulsun - AlpacaClient.place_stop_order'ın yeniden deneme
güvencesi, alpaca_trailing_stop.place_protective_stop'un bildirimleri ve
sistemdeki tüm stop kurulumlarının bu ortak yoldan geçmesi."""

import json
import os
import re
import unittest
from unittest import mock

import requests

import alpaca_client
import alpaca_trailing_stop as ts
from alpaca_client import AlpacaClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _response(status, body):
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(body).encode()
    r.reason = "x"
    r.url = "https://paper-api.alpaca.markets/v2/orders"
    return r


WRONG_SIDE = {"code": 42210000, "market_price": "211.16",
              "message": "stop price must be less than current price", "stop_price": "212.02"}


class PlaceStopOrderRetryTest(unittest.TestCase):
    def setUp(self):
        self.client = AlpacaClient("k", "s")
        sleep = mock.patch.object(alpaca_client.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def _post(self, responses):
        sent = []

        def fake_post(url, headers=None, json=None):
            sent.append(json)
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return mock.patch.object(alpaca_client.requests, "post", side_effect=fake_post), sent

    def test_wrong_side_uses_alpacas_market_price_and_entry_distance(self):
        patcher, sent = self._post([_response(422, WRONG_SIDE), _response(200, {"id": "o", "stop_price": "210.10"})])
        with patcher:
            order = self.client.place_stop_order("NUTX", 6, "long", 212.02, reference_price=212.19)
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[1]["stop_price"], f"{211.16 * (1 - 0.005):.2f}")  # min %0.5 > 0.17 mesafe
        self.assertEqual(order["_adjusted"]["requested"], 212.02)
        self.assertEqual(order["_adjusted"]["market_price"], 211.16)

    def test_wide_risk_distance_is_preserved(self):
        body = {**WRONG_SIDE, "market_price": "325.22", "stop_price": "390.09"}
        patcher, sent = self._post([_response(422, body), _response(200, {"id": "o"})])
        with patcher:
            self.client.place_stop_order("MDB", 20, "long", 390.09, reference_price=308.82)
        self.assertEqual(sent[1]["stop_price"], f"{325.22 - (390.09 - 308.82):.2f}")

    def test_unknown_entry_falls_back_to_pct(self):
        patcher, sent = self._post([_response(422, WRONG_SIDE), _response(200, {"id": "o"})])
        with patcher:
            self.client.place_stop_order("NUTX", 6, "long", 212.02)
        self.assertEqual(sent[1]["stop_price"], f"{211.16 * (1 - 0.015):.2f}")

    def test_price_keeps_falling_is_followed(self):
        second = {**WRONG_SIDE, "market_price": "205.00"}
        patcher, sent = self._post([_response(422, WRONG_SIDE), _response(422, second), _response(200, {"id": "o"})])
        with patcher:
            self.client.place_stop_order("NUTX", 6, "long", 212.02, reference_price=212.19)
        self.assertEqual(sent[2]["stop_price"], f"{205.0 * (1 - 0.005):.2f}")

    def test_transient_errors_are_retried(self):
        patcher, sent = self._post([_response(503, {"message": "busy"}), requests.ConnectionError("reset"),
                                    _response(200, {"id": "o"})])
        with patcher:
            order = self.client.place_stop_order("X", 1, "long", 10.0)
        self.assertEqual(len(sent), 3)
        self.assertNotIn("_adjusted", order)

    def test_other_rejections_are_not_retried(self):
        patcher, sent = self._post([_response(403, {"code": 40310000, "message": "insufficient qty available"})])
        with patcher, self.assertRaises(requests.HTTPError):
            self.client.place_stop_order("X", 1, "long", 10.0)
        self.assertEqual(len(sent), 1)

    def test_gives_up_after_max_attempts(self):
        patcher, sent = self._post([_response(503, {}), _response(503, {}), _response(503, {})])
        with patcher, self.assertRaises(requests.HTTPError):
            self.client.place_stop_order("X", 1, "long", 10.0)
        self.assertEqual(len(sent), alpaca_client.STOP_PLACE_MAX_ATTEMPTS)


class PlaceProtectiveStopTest(unittest.TestCase):
    def _client(self, last_price, placed=None, raises=None):
        client = mock.Mock()
        client.get_latest_trade_price.return_value = last_price
        if raises:
            client.place_stop_order.side_effect = raises
        else:
            client.place_stop_order.side_effect = lambda s, q, side, price, **kw: {
                "stop_price": f"{placed if placed is not None else price:.2f}"}
        return client

    def test_valid_stop_is_placed_silently(self):
        client = self._client(100.0)
        with mock.patch.object(ts, "notify_once_per_day") as notify:
            order = ts.place_protective_stop(client, "X", 5, "long", 98.0, entry_price=99.0)
        self.assertEqual(order["stop_price"], "98.00")
        self.assertEqual(client.place_stop_order.call_args.kwargs["reference_price"], 99.0)
        notify.assert_not_called()

    def test_moved_stop_notifies(self):
        client = self._client(211.16)
        with mock.patch.object(ts, "notify_once_per_day") as notify:
            ts.place_protective_stop(client, "NUTX", 6, "long", 212.02, entry_price=212.19)
        self.assertEqual(notify.call_args.args[1], "stop_moved")

    def test_adjusted_by_alpaca_notifies(self):
        # Kendi fiyat kontrolümüz geçti ama Alpaca'nın fiyatıyla taşındı.
        client = self._client(213.0, placed=210.10)
        with mock.patch.object(ts, "notify_once_per_day") as notify:
            ts.place_protective_stop(client, "NUTX", 6, "long", 212.02, entry_price=212.19)
        self.assertEqual(notify.call_args.args[1], "stop_moved")

    def test_failure_notifies_and_raises(self):
        client = self._client(100.0, raises=requests.HTTPError("boom"))
        with mock.patch.object(ts, "notify_once_per_day") as notify, self.assertRaises(requests.HTTPError):
            ts.place_protective_stop(client, "X", 5, "long", 98.0, entry_price=99.0)
        self.assertEqual(notify.call_args.args[1], "stop_failed")


class AllStopsGoThroughGuaranteeTest(unittest.TestCase):
    """Yeni bir modül stopu doğrudan client.place_stop_order ile kurarsa bu test
    kırılır - place_protective_stop kullanılmalı (Telegram + güncel fiyat)."""

    ALLOWED = {
        ("alpaca_trailing_stop.py", "place_protective_stop"),  # ortak yolun kendisi
        ("alpaca_trailing_stop.py", "guard_position"),         # kendi uyarı mesajı var, yine de retry'lı
    }

    def test_no_direct_stop_placement(self):
        offenders = []
        for name in sorted(os.listdir(REPO)):
            if not name.endswith(".py"):
                continue
            current_def = None
            with open(os.path.join(REPO, name), encoding="utf-8") as f:
                for line in f:
                    m = re.match(r"def (\w+)", line)
                    if m:
                        current_def = m.group(1)
                    if "client.place_stop_order(" in line and (name, current_def) not in self.ALLOWED:
                        offenders.append(f"{name}:{current_def}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
