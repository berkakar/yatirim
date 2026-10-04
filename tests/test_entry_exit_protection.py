"""Heikin Ashi ve Premium Buy Point kırılım girişleri ile modül çıkışlarında
pozisyon stopsuz kalmıyor mu:

- AlpacaClient.wait_for_fill: kısmi / geç dolum stopsuz kalmaz (dolan adet döner).
- close_position_market: market çıkışı reddedilirse stop geri kurulur.
- HA girişi ve PBP kırılım girişi: stop gerçek dolan adet için, fiyat yapısal
  stopun altına indiyse güncel fiyatın altına kurulur."""

import json
import unittest
from unittest import mock

import requests

import alpaca_buy_points as abp
import alpaca_client
import alpaca_trailing_stop as ts
import heikin_ashi_intraday_core as hai
from alpaca_client import AlpacaClient
from structure import Bar


def _resp(body, status=200):
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(body).encode()
    r.url = "x"
    r.reason = "x"
    return r


class WaitForFillTest(unittest.TestCase):
    def setUp(self):
        self.client = AlpacaClient("k", "s")
        for target in (mock.patch.object(alpaca_client.time, "sleep"),
                       mock.patch.object(alpaca_client.time, "monotonic", side_effect=iter(range(0, 1000, 10)))):
            target.start()
            self.addCleanup(target.stop)

    def _run(self, states, cancel_raises=False):
        gets = iter(states)
        deletes = []

        def fake_delete(url, headers=None):
            deletes.append(url)
            return _resp({"message": "already filled"}, 422) if cancel_raises else _resp({}, 204)

        with mock.patch.object(alpaca_client.requests, "get", side_effect=lambda *a, **k: _resp(next(gets))), \
                mock.patch.object(alpaca_client.requests, "delete", side_effect=fake_delete):
            try:
                return self.client.wait_for_fill("o", timeout=30), deletes
            except TimeoutError as e:
                return e, deletes

    def test_full_fill(self):
        order, deletes = self._run([{"status": "filled", "filled_qty": "6", "filled_avg_price": "10"}])
        self.assertEqual(order["filled_qty"], "6")
        self.assertEqual(deletes, [])

    def test_partial_fill_on_timeout_returns_filled_part(self):
        partial = {"status": "partially_filled", "filled_qty": "2", "filled_avg_price": "10"}
        canceled = {"status": "canceled", "filled_qty": "2", "filled_avg_price": "10"}
        order, deletes = self._run([partial, partial, partial, canceled])
        self.assertEqual(order["filled_qty"], "2")
        self.assertEqual(len(deletes), 1)

    def test_fill_arriving_during_cancel_is_returned(self):
        new = {"status": "new", "filled_qty": "0"}
        filled = {"status": "filled", "filled_qty": "6", "filled_avg_price": "10"}
        order, _ = self._run([new, new, new, filled], cancel_raises=True)
        self.assertEqual(order["status"], "filled")

    def test_nothing_filled_raises(self):
        new = {"status": "new", "filled_qty": "0"}
        canceled = {"status": "canceled", "filled_qty": "0"}
        result, deletes = self._run([new, new, new, canceled])
        self.assertIsInstance(result, TimeoutError)
        self.assertEqual(len(deletes), 1)

    def test_rejected_returns_quickly_without_cancel(self):
        rejected = {"status": "rejected", "filled_qty": "0"}
        result, deletes = self._run([rejected, rejected])
        self.assertIsInstance(result, TimeoutError)
        self.assertEqual(deletes, [])


class ClosePositionMarketTest(unittest.TestCase):
    def _client(self, stop_tag_value, exit_raises):
        client = mock.Mock()
        client.get_open_stop_order.return_value = {"id": "s1", "stop_price": "95.00", "client_order_id": stop_tag_value}
        if exit_raises:
            client.place_market_exit.side_effect = requests.HTTPError("halted")
        return client

    def test_exit_ok_cancels_stop_only(self):
        client = self._client("stop-initial-X-1", exit_raises=False)
        with mock.patch.object(ts, "place_protective_stop") as place:
            ts.close_position_market(client, "X", 5, client_order_id="hai-exit-X")
        client.cancel_order.assert_called_once_with("s1")
        place.assert_not_called()

    def test_failed_exit_restores_stop(self):
        client = self._client("stop-initial-X-1", exit_raises=True)
        with mock.patch.object(ts, "place_protective_stop") as place, mock.patch.object(ts.time, "sleep"), \
                self.assertRaises(requests.HTTPError):
            ts.close_position_market(client, "X", 5)
        self.assertEqual(place.call_args.args[:5], (client, "X", 5, "long", 95.0))
        self.assertTrue(place.call_args.kwargs["client_order_id"].startswith("stop-restore-"))

    def test_failed_exit_keeps_shield_real_level(self):
        client = self._client(ts.shield_tag("X", 99.0, now=1), exit_raises=True)
        with mock.patch.object(ts, "place_protective_stop") as place, mock.patch.object(ts.time, "sleep"), \
                self.assertRaises(requests.HTTPError):
            ts.close_position_market(client, "X", 5)
        self.assertEqual(ts.parse_shield_real_stop(place.call_args.kwargs["client_order_id"]), 99.0)

    def test_ha_sell_and_rs_use_safe_close(self):
        client = mock.Mock()
        client.get_position.return_value = {"qty": "5"}
        with mock.patch.object(hai, "close_position_market") as close:
            hai._sell(client, "X", "exit")
        self.assertEqual(close.call_args.args[:3], (client, "X", 5.0))


def _filled(qty, price):
    return {"status": "filled", "filled_qty": str(qty), "filled_avg_price": str(price)}


class HeikinAshiEntryStopTest(unittest.TestCase):
    """run_pass alış adımı: stop dolan adet ve güncel fiyatla kurulur."""

    def _run(self, fill, last_price, structural_stop=37.47):
        cand = hai.HaCandidate(symbol="LAUR", price=37.58, score=1.0, reason="t")
        client = mock.Mock()
        client.get_clock.return_value = {"next_close": "2099-01-01T00:00:00Z"}
        client.get_all_positions.return_value = []
        client.get_account.return_value = {"cash": "100000"}
        client.place_market_entry.return_value = {"id": "b"}
        client.wait_for_fill.return_value = fill
        client.get_latest_trade_price.return_value = last_price
        client.place_stop_order.side_effect = lambda s, q, side, p, **kw: {"stop_price": f"{p:.2f}", "qty": q}
        algo = mock.Mock(initial_stop=mock.Mock(return_value=structural_stop))
        cfg = {"enabled": True, "cash_allocation_pct": 10.0, "max_positions": 5}
        with mock.patch.object(hai, "load_holdings_local", return_value={}), \
                mock.patch.object(hai, "save_holdings_local") as save, \
                mock.patch.object(hai, "build_universe", return_value=["LAUR"]), \
                mock.patch.object(hai, "excluded_symbols", return_value=set()), \
                mock.patch.object(hai, "filter_by_liquidity", side_effect=lambda c, u, m: u), \
                mock.patch.object(hai, "scan_candidates", return_value=[cand]), \
                mock.patch.object(hai, "_fetch_bars", return_value=[]), \
                mock.patch.dict(hai.STOP_ALGORITHMS, {"heikin_ashi_exit": algo}), \
                mock.patch.object(hai, "resolve_kwargs", return_value={}), \
                mock.patch("alpaca_buy_points.load_module_risk_context", return_value=None), \
                mock.patch.object(ts, "notify_once_per_day") as notify:
            hai.run_pass(client, "test", cfg, {})
        holdings = save.call_args.args[1]
        return client, notify, holdings

    def test_partial_fill_gets_stop_for_filled_qty(self):
        client, _, holdings = self._run(_filled(20, 37.58), last_price=37.60)
        self.assertEqual(client.place_stop_order.call_args.args[1], 20.0)
        self.assertEqual(holdings["LAUR"]["qty"], 20.0)

    def test_price_below_signal_low_moves_stop(self):
        client, notify, holdings = self._run(_filled(45, 37.58), last_price=37.30)
        placed = client.place_stop_order.call_args.args[3]
        self.assertLess(placed, 37.30)
        self.assertEqual(holdings["LAUR"]["stop_price"], round(placed, 2))
        self.assertEqual(notify.call_args.args[1], "stop_moved")


class PbpBreakoutEntryStopTest(unittest.TestCase):
    """check_symbol'ün kırılım (market emri) dalı."""

    def _run(self, fill, last_price, structural_stop=98.0):
        bars = [Bar(t=f"2026-10-05T14:{i:02d}:00Z", o=100, h=101, l=99, c=100, v=1000) for i in range(30)]
        signal = mock.Mock(price=100.0, style="breakout", reason="kırılım")
        client = mock.Mock()
        client.get_open_limit_buy_order.return_value = None
        client.get_position.return_value = None
        client.get_latest_trade_price.return_value = last_price
        client.place_market_entry.return_value = {"id": "b"}
        client.wait_for_fill.return_value = fill
        client.place_stop_order.side_effect = lambda s, q, side, p, **kw: {"stop_price": f"{p:.2f}", "qty": q}
        algo = mock.Mock(initial_stop=mock.Mock(return_value=structural_stop))
        with mock.patch.object(abp, "get_bars_for_timeframe", return_value=bars), \
                mock.patch.dict(abp.ALGORITHMS, {"breakout_volume": ("Kırılım", lambda b, d: signal)}), \
                mock.patch.object(abp, "reject_if_marketable", side_effect=lambda s, p: s), \
                mock.patch.dict(abp.STOP_ALGORITHMS, {"breakeven_atr_structure": algo}), \
                mock.patch.object(abp, "resolve_kwargs", return_value={}), \
                mock.patch.object(ts, "notify_once_per_day") as notify:
            abp.check_symbol(client, "XYZ", 10.0, 10_000.0, "breakout_volume", "15Min",
                             stop_algorithm="breakeven_atr_structure", stop_settings={})
        return client, notify

    def test_stop_uses_filled_qty_and_fill_price(self):
        client, notify = self._run(_filled(7, 100.2), last_price=100.3)
        self.assertEqual(client.place_stop_order.call_args.args[1], 7.0)
        self.assertEqual(client.place_stop_order.call_args.kwargs["reference_price"], 100.2)
        notify.assert_not_called()

    def test_price_below_stop_moves_stop(self):
        client, notify = self._run(_filled(10, 100.0), last_price=97.5)
        self.assertLess(client.place_stop_order.call_args.args[3], 97.5)
        self.assertEqual(notify.call_args.args[1], "stop_moved")


if __name__ == "__main__":
    unittest.main()
