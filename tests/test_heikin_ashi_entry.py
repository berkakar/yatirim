"""Heikin Ashi alış tarafı: Stokastik kesişimi, sinyal mumunun tazeliği ve
bar bütünlüğü (heikin_ashi.long_entry, heikin_ashi_intraday_core.
signal_bars_problem) ve likidite filtresi varsayılanı."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import heikin_ashi
import heikin_ashi_intraday_core as core
from structure import Bar

ET = ZoneInfo("America/New_York")


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def session_bars(days: list[datetime], last_day_bars: int = 13, skip: set | None = None) -> list[Bar]:
    """Her gün için 09:30 ET'den başlayan 30dk mumlar (tam gün 13 mum);
    `skip`, atlanacak (gün indeksi, mum indeksi) çiftleri."""
    bars = []
    price = 100.0
    for d_idx, day in enumerate(days):
        count = last_day_bars if d_idx == len(days) - 1 else 13
        for i in range(count):
            if skip and (d_idx, i) in skip:
                continue
            start = day.replace(hour=9, minute=30, tzinfo=ET) + timedelta(minutes=30 * i)
            bars.append(Bar(t=_ts(start), o=price, h=price + 0.5, l=price - 0.5, c=price + 0.1, v=1000))
            price += 0.1
    return bars


DAYS = [datetime(2026, 9, d) for d in (21, 22, 23, 24, 25)]  # Pzt-Cum


class SignalBarsProblemTest(unittest.TestCase):
    def test_fresh_complete_window_passes(self):
        bars = session_bars(DAYS, last_day_bars=2)  # son mum 10:00-10:30
        now = datetime(2026, 9, 25, 10, 32, tzinfo=ET)
        self.assertIsNone(core.signal_bars_problem(bars, now))

    def test_morning_run_on_yesterdays_last_bar_is_rejected(self):
        # 09:32 ET: 09:30 mumu henüz kapanmadı, son kapanmış mum dünkü 15:30.
        bars = session_bars(DAYS[:4])
        now = datetime(2026, 9, 25, 9, 32, tzinfo=ET)
        self.assertIn("bugüne ait değil", core.signal_bars_problem(bars, now))

    def test_old_bar_today_is_rejected(self):
        # IEX'te 10:30 ve 11:00 mumları hiç gelmemiş: son mum 10:00-10:30.
        bars = session_bars(DAYS, last_day_bars=2)
        now = datetime(2026, 9, 25, 11, 32, tzinfo=ET)
        self.assertIn("dk önce kapanmış", core.signal_bars_problem(bars, now))

    def test_missing_bar_in_window_is_rejected(self):
        bars = session_bars(DAYS, last_day_bars=2, skip={(1, 5)})
        now = datetime(2026, 9, 25, 10, 32, tzinfo=ET)
        self.assertIn("eksik mum", core.signal_bars_problem(bars, now))

    def test_session_not_starting_at_open_is_rejected(self):
        bars = session_bars(DAYS, last_day_bars=2, skip={(2, 0)})
        now = datetime(2026, 9, 25, 10, 32, tzinfo=ET)
        self.assertIn("09:30", core.signal_bars_problem(bars, now))

    def test_window_truncated_mid_session_on_first_day_is_accepted(self):
        # _fetch_bars "şu andan 10 gün önce"den çeker: ilk gün seans ortasından başlar.
        bars = session_bars(DAYS, last_day_bars=2, skip={(0, i) for i in range(6)})
        now = datetime(2026, 9, 25, 10, 32, tzinfo=ET)
        self.assertIsNone(core.signal_bars_problem(bars, now))

    def test_half_day_is_accepted(self):
        # 24 Eylül yarım gün gibi 12:30 mumunda bitiyor (7 mum).
        bars = session_bars(DAYS, last_day_bars=2, skip={(3, i) for i in range(7, 13)})
        now = datetime(2026, 9, 25, 10, 32, tzinfo=ET)
        self.assertIsNone(core.signal_bars_problem(bars, now))

    def test_day_ending_early_is_rejected(self):
        bars = session_bars(DAYS, last_day_bars=2, skip={(3, i) for i in range(4, 13)})
        now = datetime(2026, 9, 25, 10, 32, tzinfo=ET)
        self.assertIn("erken bitiyor", core.signal_bars_problem(bars, now))


class ScanCandidatesSkipsStaleBarsTest(unittest.TestCase):
    def test_stale_window_never_reaches_signal(self):
        bars = session_bars(DAYS[:4])
        with patch.object(core, "_fetch_bars", return_value=bars), \
                patch.object(core, "heikin_ashi_stoch_signal") as signal:
            result = core.scan_candidates(object(), ["XYZ"], now=datetime(2026, 9, 25, 9, 32, tzinfo=ET))
        self.assertEqual(result, [])
        signal.assert_not_called()


def _entry_ready_bars() -> list[Bar]:
    """Stokastik dışındaki tüm alış şartlarını sağlayan mumlar: SMA50 üstünde,
    kırmızı HA mumundan sonra alt fitilsiz yeşil HA mumu."""
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    bars, prev = [], 100.0
    for i in range(60):
        c = 100 + i * 0.5
        bars.append(Bar(t=_ts(start + timedelta(minutes=30 * i)), o=prev, h=max(prev, c) + 0.2,
                        l=min(prev, c) - 0.2, c=c, v=1000))
        prev = c
    bars.append(Bar(t=_ts(start + timedelta(minutes=30 * 60)), o=129.5, h=129.6, l=127.0, c=127.2, v=1000))
    bars.append(Bar(t=_ts(start + timedelta(minutes=30 * 61)), o=128.8, h=130.5, l=128.8, c=130.4, v=1000))
    return bars


def _stoch(prev_k, prev_d, k, d):
    def fake(bars, k_period, d_period):
        ks, ds = [None] * len(bars), [None] * len(bars)
        ks[-2], ds[-2], ks[-1], ds[-1] = prev_k, prev_d, k, d
        return ks, ds
    return fake


class StochasticCrossoverTest(unittest.TestCase):
    def setUp(self):
        self.bars = _entry_ready_bars()
        ha = heikin_ashi.heikin_ashi(self.bars)
        self.assertTrue(ha[-2].red and ha[-1].green)

    def test_fresh_cross_below_oversold_signals(self):
        with patch.object(heikin_ashi, "stochastic_series", _stoch(20, 22, 25, 23)):
            self.assertIsNotNone(heikin_ashi.long_entry(self.bars))

    def test_k_already_above_d_is_not_a_cross(self):
        # Eski kural (sadece %K > %D) bunu sinyal sayıyordu.
        with patch.object(heikin_ashi, "stochastic_series", _stoch(24, 22, 25, 23)):
            self.assertIsNone(heikin_ashi.long_entry(self.bars))

    def test_cross_above_oversold_does_not_signal(self):
        with patch.object(heikin_ashi, "stochastic_series", _stoch(28, 30, 33, 31)):
            self.assertIsNone(heikin_ashi.long_entry(self.bars))

    def test_missing_previous_value_does_not_signal(self):
        with patch.object(heikin_ashi, "stochastic_series", _stoch(None, None, 25, 23)):
            self.assertIsNone(heikin_ashi.long_entry(self.bars))


class LiquidityDefaultTest(unittest.TestCase):
    def test_zero_saved_value_uses_default_like_settings_page(self):
        captured = {}

        def fake_filter(client, universe, min_dollar_volume):
            captured["value"] = min_dollar_volume
            return []

        cfg = {"enabled": True, "cash_allocation_pct": 10.0, "min_avg_dollar_volume": 0.0}
        clock = {"next_close": _ts(datetime.now(timezone.utc) + timedelta(hours=3))}
        client = type("C", (), {"get_clock": lambda self: clock, "get_all_positions": lambda self: []})()
        with patch.object(core, "filter_by_liquidity", fake_filter), \
                patch.object(core, "build_universe", return_value=["XYZ"]), \
                patch.object(core, "excluded_symbols", return_value=set()), \
                patch.object(core, "load_holdings_local", return_value={}), \
                patch.object(core, "save_holdings_local"), \
                patch.object(core, "scan_candidates", return_value=[]):
            try:
                core.run_pass(client, "test", cfg, {})
            except Exception:
                pass  # hesap/nakit adımları bu testin konusu değil
        self.assertEqual(captured.get("value"), core.DEFAULT_MIN_AVG_DOLLAR_VOLUME)


if __name__ == "__main__":
    unittest.main()
