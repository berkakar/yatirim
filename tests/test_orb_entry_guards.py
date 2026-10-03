"""ORB girişi: seans barlarının doğrulanması (orb_core.orb_bars_problem),
alıştan hemen önce kırılımın yeniden doğrulanması (_breakout_still_valid) ve
fiyat yapısal stopun altına indiyse koruyucu stop (protective_stop) - 2026-09-30
NUTX/HVT/KRUS vakaları."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import orb_core
from alpaca_trailing_stop import protective_stop
from structure import Bar

ET = ZoneInfo("America/New_York")
DAY = datetime(2026, 9, 30, tzinfo=ET)


def _ts(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def session(n, start=(9, 30), skip=(), prices=None):
    """Bugünkü seans için 15dk barlar. prices: [(o,h,l,c,v), ...]."""
    bars = []
    t0 = DAY.replace(hour=start[0], minute=start[1])
    for i in range(n):
        if i in skip:
            continue
        o, h, l, c, v = prices[i] if prices else (100, 101, 99, 100.5, 1000)
        bars.append(Bar(t=_ts(t0 + timedelta(minutes=15 * i)), o=o, h=h, l=l, c=c, v=v))
    return bars


def yesterday():
    t0 = (DAY - timedelta(days=1)).replace(hour=9, minute=30)
    return [Bar(t=_ts(t0 + timedelta(minutes=15 * i)), o=100, h=101, l=99, c=100, v=1000) for i in range(26)]


class OrbBarsProblemTest(unittest.TestCase):
    def test_complete_fresh_session_passes(self):
        bars = yesterday() + session(4)  # son bar 10:15-10:30
        self.assertIsNone(orb_core.orb_bars_problem(bars, "15Min", DAY.replace(hour=10, minute=32)))

    def test_sparse_iex_session_is_rejected(self):
        # 2026-09-30: seyrek hissede gün boyu birkaç bar - "ilk bar" 09:30 değil.
        bars = yesterday() + session(4, start=(14, 45))
        problem = orb_core.orb_bars_problem(bars, "15Min", DAY.replace(hour=15, minute=47))
        self.assertIn("09:30", problem)

    def test_gap_in_session_is_rejected(self):
        bars = yesterday() + session(4, skip={1})
        self.assertIn("eksik", orb_core.orb_bars_problem(bars, "15Min", DAY.replace(hour=10, minute=32)))

    def test_stale_last_bar_is_rejected(self):
        bars = yesterday() + session(4)
        self.assertIn("bayat", orb_core.orb_bars_problem(bars, "15Min", DAY.replace(hour=11, minute=0)))

    def test_yesterday_only_is_rejected(self):
        self.assertIn("bugüne ait değil",
                      orb_core.orb_bars_problem(yesterday(), "15Min", DAY.replace(hour=9, minute=47)))


class BreakoutStillValidTest(unittest.TestCase):
    # Açılış barı 100-101, sonraki barlar yüksek hacimle 101'i kırıyor.
    PRICES = [(100, 101, 100, 100.8, 1000), (100.8, 101.5, 100.7, 101.4, 2000),
              (101.4, 101.8, 101.2, 101.7, 1800), (101.7, 102.2, 101.6, 102.0, 1600)]
    NOW = DAY.replace(hour=10, minute=32)

    def _check(self, last_price):
        bars = yesterday() + session(4, prices=self.PRICES)
        client = mock.Mock(get_latest_trade_price=mock.Mock(return_value=last_price))
        return orb_core._breakout_still_valid(client, "XYZ", bars, "15Min", 1.5, 4, now=self.NOW)

    def test_breakout_holding_is_valid(self):
        self.assertIsNone(self._check(101.9))

    def test_price_back_inside_opening_range_is_rejected(self):
        self.assertIn("geri döndü", self._check(100.9))


class ProtectiveStopTest(unittest.TestCase):
    def test_nutx_case(self):
        # Dolum 212.19, yapısal stop 212.02, fiyat 211.16 -> Alpaca reddediyordu.
        price, moved = protective_stop(212.02, 212.19, 211.16, "long")
        self.assertTrue(moved)
        self.assertAlmostEqual(price, 211.16 - (212.19 - 212.02))
        self.assertLess(price, 211.16)

    def test_valid_stop_is_untouched(self):
        self.assertEqual(protective_stop(98.0, 100.0, 99.5, "long"), (98.0, False))

    def test_unknown_price_keeps_stop(self):
        self.assertEqual(protective_stop(98.0, 100.0, None, "long"), (98.0, False))


class ScanAndBuyStopTest(unittest.TestCase):
    """scan_and_buy'ın alış sonrası stop adımı: fiyat yapısal stopun altına
    inmişse koruyucu stop kurulur ve Telegram'a haber verilir; stop yine de
    kurulamazsa KORUMASIZ uyarısı gider."""

    def _run(self, last_prices, stop_raises=False):
        cand = orb_core.OrbCandidate(symbol="NUTX", price=212.30, score=1.0, reason="test")
        client = mock.Mock()
        client.get_account.return_value = {"cash": "100000"}
        client.get_open_orders.return_value = []
        client.get_all_positions.return_value = []
        client.get_watchlist_by_name.return_value = None
        client.get_latest_trade_price.side_effect = list(last_prices)
        client.place_market_entry.return_value = {"id": "b"}
        client.wait_for_fill.return_value = {"filled_avg_price": "212.19", "filled_qty": "6"}
        if stop_raises:
            client.place_stop_order.side_effect = RuntimeError("422")
        algo = mock.Mock(initial_stop=mock.Mock(return_value=212.02))
        cfg = {"enabled": True, "cash_allocation_pct": 10.0, "top_n": 1, "stop_algorithm": "opening_range"}
        with mock.patch.object(orb_core, "load_holdings_local", return_value={}), \
                mock.patch.object(orb_core, "save_holdings_local"), \
                mock.patch.object(orb_core, "build_universe", return_value=["NUTX"]), \
                mock.patch.object(orb_core, "filter_by_liquidity", side_effect=lambda c, u, m: u), \
                mock.patch.object(orb_core, "scan_candidates", return_value=[cand]), \
                mock.patch.object(orb_core, "get_bars_for_timeframe", return_value=[]), \
                mock.patch.object(orb_core, "_breakout_still_valid", return_value=None), \
                mock.patch.dict(orb_core.STOP_ALGORITHMS, {"opening_range": algo}), \
                mock.patch.object(orb_core, "resolve_kwargs", return_value={}), \
                mock.patch("relative_strength_core.load_holdings_local", return_value={}), \
                mock.patch("heikin_ashi_intraday_core.load_holdings_local", return_value={}), \
                mock.patch("alpaca_buy_points.load_module_risk_context", return_value=None), \
                mock.patch.object(orb_core, "notify_once_per_day") as notify:
            summary = orb_core.scan_and_buy(client, "test", cfg, {})
        return client, notify, summary

    def test_price_below_structural_stop_places_protective_stop(self):
        client, notify, summary = self._run([211.16])
        placed = client.place_stop_order.call_args.args[3]
        self.assertEqual(placed, round(211.16 - (212.19 - 212.02), 2))
        self.assertEqual(notify.call_args.args[1], "orb_stop_moved")
        self.assertEqual(summary["buy_errors"], [])

    def test_failed_stop_alerts(self):
        client, notify, summary = self._run([212.50], stop_raises=True)
        self.assertEqual(client.place_stop_order.call_args.args[3], 212.02)
        self.assertEqual(notify.call_args.args[1], "orb_stop_failed")
        self.assertIn("KORUMASIZ", summary["buy_errors"][0])

    def test_stale_breakout_is_not_bought(self):
        cand = orb_core.OrbCandidate(symbol="NUTX", price=212.30, score=1.0, reason="test")
        client = mock.Mock()
        client.get_account.return_value = {"cash": "100000"}
        client.get_open_orders.return_value = []
        client.get_watchlist_by_name.return_value = None
        cfg = {"enabled": True, "cash_allocation_pct": 10.0, "top_n": 1}
        with mock.patch.object(orb_core, "load_holdings_local", return_value={}), \
                mock.patch.object(orb_core, "save_holdings_local"), \
                mock.patch.object(orb_core, "build_universe", return_value=["NUTX"]), \
                mock.patch.object(orb_core, "filter_by_liquidity", side_effect=lambda c, u, m: u), \
                mock.patch.object(orb_core, "scan_candidates", return_value=[cand]), \
                mock.patch.object(orb_core, "get_bars_for_timeframe", return_value=[]), \
                mock.patch.object(orb_core, "_breakout_still_valid", return_value="kırılım sinyali artık yok"), \
                mock.patch("relative_strength_core.load_holdings_local", return_value={}), \
                mock.patch("heikin_ashi_intraday_core.load_holdings_local", return_value={}), \
                mock.patch("alpaca_buy_points.load_module_risk_context", return_value=None):
            summary = orb_core.scan_and_buy(client, "test", cfg, {})
        client.place_market_entry.assert_not_called()
        self.assertEqual(summary["bought"], [])
        self.assertIn("kırılım sinyali artık yok", summary["buy_errors"][0])


if __name__ == "__main__":
    unittest.main()
