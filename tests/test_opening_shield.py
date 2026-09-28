"""[Öneri 3] Açılış kalkanı: guard seans dışında stopu felaket seviyesine
genişletir, seansın ilk 15 dakikasında dokunulmaz, sonra asıl seviyeye döner
ya da fiyat asıl stopun altındaysa market çıkışı yapılır."""

import unittest
from unittest import mock

import alpaca_trailing_stop as ats
from stop_tags import parse_shield_real_stop, shield_tag
from tests.fake_client import FakeClient
from tests.helpers import make_bars

SETTINGS = {"execution": {"opening_shield_enabled": True, "opening_shield_minutes": 15,
                          "shield_disaster_pct": 4.0, "extended_hours_trail_enabled": False}}
POSITION = {"symbol": "MSFT", "qty": "24", "avg_entry_price": "494.44", "asset_class": "us_equity"}


def _stop(price, tag=None):
    return {"id": "s1", "symbol": "MSFT", "qty": "24", "side": "sell", "type": "stop", "status": "new",
            "stop_price": f"{price:.2f}", "client_order_id": tag, "created_at": "2026-09-18T13:36:00Z"}


class GuardShieldTest(unittest.TestCase):
    def test_guard_widens_stop_and_keeps_real_level_in_tag(self):
        client = FakeClient(positions=[POSITION], orders=[_stop(474.24)], last_price=480.0)
        ats.guard_position(client, POSITION, None, None, "atr_volatility", SETTINGS)
        stop = client.get_open_stop_order("MSFT")
        self.assertAlmostEqual(float(stop["stop_price"]), round(474.24 * 0.96, 2))
        self.assertAlmostEqual(parse_shield_real_stop(stop["client_order_id"]), 474.24)
        # asıl stop (474.24) seans dışında kırılmış olsa bile... burada fiyat 480, satış yok
        self.assertFalse(any(c[0] == "ext_limit" for c in client.calls))

    def test_guard_does_not_sell_when_only_real_stop_is_broken(self):
        client = FakeClient(positions=[POSITION], orders=[_stop(474.24)], last_price=470.0)
        ats.guard_position(client, POSITION, None, None, "atr_volatility", SETTINGS)
        self.assertFalse(any(c[0] == "ext_limit" for c in client.calls))

    def test_guard_sells_when_disaster_stop_is_broken(self):
        client = FakeClient(positions=[POSITION], orders=[_stop(474.24)], last_price=450.0)
        ats.guard_position(client, POSITION, None, None, "atr_volatility", SETTINGS)
        self.assertTrue(any(c[0] == "ext_limit" for c in client.calls))

    def test_shield_disabled_keeps_old_behaviour(self):
        settings = {"execution": {**SETTINGS["execution"], "opening_shield_enabled": False}}
        client = FakeClient(positions=[POSITION], orders=[_stop(474.24)], last_price=470.0)
        ats.guard_position(client, POSITION, None, None, "atr_volatility", settings)
        self.assertTrue(any(c[0] == "ext_limit" for c in client.calls))


@mock.patch.object(ats, "get_management_start", return_value=ats.datetime(2026, 9, 18, tzinfo=ats.timezone.utc))
@mock.patch.object(ats, "get_initial_stop_price", return_value=474.24)
@mock.patch.object(ats, "get_trend_daily_closes", return_value=[])
@mock.patch.object(ats, "_stop_bars_for_timeframe",
                   return_value=(make_bars([495.0] * 5, spread=10), make_bars([495.0] * 20, spread=10)))
class ManagePositionShieldTest(unittest.TestCase):
    def _client(self, last_price):
        tag = shield_tag("MSFT", 474.24)
        return FakeClient(positions=[POSITION], orders=[_stop(round(474.24 * 0.96, 2), tag)], last_price=last_price)

    def test_inside_window_nothing_changes(self, *_):
        client = self._client(480.0)
        ats.manage_position(client, POSITION, stop_algorithm="atr_volatility", stop_settings=SETTINGS,
                            timeframe="1Day", minutes_since_open=5)
        self.assertEqual(client.calls, [])

    def test_after_window_restores_real_stop(self, *_):
        client = self._client(480.0)
        ats.manage_position(client, POSITION, stop_algorithm="atr_volatility", stop_settings=SETTINGS,
                            timeframe="1Day", minutes_since_open=16)
        stop = client.get_open_stop_order("MSFT")
        self.assertAlmostEqual(float(stop["stop_price"]), 474.24)
        self.assertIsNone(parse_shield_real_stop(stop["client_order_id"]))

    def test_after_window_exits_if_real_stop_broken(self, *_):
        client = self._client(470.0)
        ats.manage_position(client, POSITION, stop_algorithm="atr_volatility", stop_settings=SETTINGS,
                            timeframe="1Day", minutes_since_open=16)
        self.assertTrue(any(c[0] == "market_exit" for c in client.calls))
        self.assertIsNone(client.get_open_stop_order("MSFT"))

    def test_unshielded_stop_is_shielded_in_first_minutes(self, *_):
        client = FakeClient(positions=[POSITION], orders=[_stop(474.24)], last_price=480.0)
        ats.manage_position(client, POSITION, stop_algorithm="atr_volatility", stop_settings=SETTINGS,
                            timeframe="1Day", minutes_since_open=2)
        stop = client.get_open_stop_order("MSFT")
        self.assertAlmostEqual(parse_shield_real_stop(stop["client_order_id"]), 474.24)


class ManagePositionTrailTest(unittest.TestCase):
    """Kalkan dışında (seansın 60. dakikası) normal trail: +%1.5 üzerinde
    breakeven, girişin %0.2 üstü, emir 'stop-breakeven' etiketiyle."""

    def test_breakeven_trail_is_tagged(self):
        position = {**POSITION, "avg_entry_price": "100"}
        client = FakeClient(positions=[position], orders=[_stop(98.5)], last_price=102.0)
        bars = make_bars([100.0, 101.0, 102.0], spread=0.4)
        with mock.patch.object(ats, "get_management_start", return_value=ats.datetime(2026, 1, 1, tzinfo=ats.timezone.utc)), \
                mock.patch.object(ats, "get_initial_stop_price", return_value=98.5), \
                mock.patch.object(ats, "get_trend_daily_closes", return_value=[]), \
                mock.patch.object(ats, "_stop_bars_for_timeframe", return_value=(bars, bars)):
            ats.manage_position(client, position, stop_algorithm="breakeven_atr_structure",
                                stop_settings={**SETTINGS, "shared": {"trend_ema_period": 0}},
                                timeframe="1Day", minutes_since_open=60)
        stop = client.get_open_stop_order("MSFT")
        self.assertAlmostEqual(float(stop["stop_price"]), 100.2)
        self.assertTrue(stop["client_order_id"].startswith("stop-breakeven-MSFT-"))


class StopTimeframeTest(unittest.TestCase):
    def test_pbp_symbol_uses_entry_timeframe(self):
        config = {"symbol_settings": {"MSFT": {"timeframe": "1Day"}}}
        self.assertEqual(ats.resolve_stop_timeframe_for_position(config, {}, {}, {}, "MSFT"), "1Day")
        self.assertEqual(ats.resolve_stop_timeframe_for_position(config, {}, {"MSFT": {}}, {}, "MSFT"), ats.TIMEFRAME)
        self.assertEqual(ats.resolve_stop_timeframe({**config, "stop_timeframe_mode": "global"}, "MSFT"), ats.TIMEFRAME)


if __name__ == "__main__":
    unittest.main()
