"""Değerleme piyasa servisleri (valuation_service.py) ve skor tablosu (valuation_db.py):
evren/tekrar eleme, 50'lik paketler, 429 bekleme, silme kuralı, üzerine yazma ve
arayüzün eksik hisseyi anlık çekip kaydetmesi."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import storage


def make_raw(ticker, pe=20.0, sub="Semis"):
    return {
        "Hisse": ticker, "Alt Sektör (İş Modeli)": sub, "Ana Sektör": "Technology",
        "F/K": pe, "PD/DD": 3.0, "FD/FAVÖK": 12.0, "PEG": 0.9,
        "EPS Büyümesi %": 12.0, "Gelir Büyümesi %": 11.0, "Öz Sermaye Getirisi (ROE) %": 15.0,
        "Net Kar Marjı %": 16.0, "Brüt Kar Marjı %": 45.0, "Faiz Karşılama Oranı": 5.0,
        "Varlık Getirisi (ROA) %": 7.0, "Borç / Özsermaye": 0.4, "Borç / Varlık %": 30.0,
        "Cari Oran": 1.5, "Likidite Oranı": 1.2, "Varlık Devir Hızı": 1.1,
        "_most_recent_quarter": "2026-06-30",
        "_next_earnings": "2026-10-29",
    }


class FakeFetcher:
    def __init__(self, pe=None, rate_limited=(), missing=(), etf=()):
        self.calls = []
        self.etf = set(etf)
        self.pe = pe or {}
        self.rate_limited = set(rate_limited)
        self.missing = set(missing)

    def __call__(self, ticker, sub_sectors_map=None, raise_on_rate_limit=False):
        import valuation
        self.calls.append(ticker)
        if ticker in self.rate_limited:
            raise valuation.YahooRateLimited("429 Too Many Requests")
        if ticker in self.missing:
            return None
        if ticker in self.etf:
            return {"Hisse": ticker, "_excluded": "ETF"}
        return make_raw(ticker, pe=self.pe.get(ticker, 20.0))


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 5, 21, 0, tzinfo=timezone.utc)

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


class ValuationServiceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        old_cwd = os.getcwd()
        os.chdir(self._tmp.name)
        self.addCleanup(os.chdir, old_cwd)
        p = mock.patch.dict(os.environ, {"YATIRIM_DB_PATH": os.path.join(self._tmp.name, "y.db")})
        p.start()
        self.addCleanup(p.stop)

        storage.write("custom_tickers", "u1", {"NASDAQ 100": ["AAPL", "MSFT", " aapl "], "BIST 100": ["THYAO.IS"]})
        storage.write("custom_tickers", "u2", {"NASDAQ 100": ["MSFT", "NVDA"]})
        storage.write("custom_stock_groups", "u1", {"Çip": ["AMD", "NVDA"], "Bist": ["ASELS.IS"]})
        storage.write("custom_stock_group_markets", "u1", {"Çip": "NASDAQ 100", "Bist": "BIST 100"})
        storage.write("selected_tickers", "u2", ["TSLA"])

        import valuation_service
        self.svc = valuation_service
        quiet = mock.patch.object(valuation_service, "log")
        quiet.start()
        self.addCleanup(quiet.stop)
        self.sleeps = []
        self.clock = Clock()

    def run_market(self, fetcher, market="NASDAQ 100", batch_size=50):
        return self.svc.run_market(market, batch_size=batch_size, batch_pause_s=30,
                                   ticker_delay_s=None, fetcher=fetcher,
                                   sleep=self.sleeps.append, now=self.clock)


class UniverseTest(ValuationServiceTestCase):
    def test_market_lists_and_linked_groups_without_duplicates(self):
        universe, user_stocks = self.svc.build_universe("NASDAQ 100")
        self.assertEqual(universe, ["AAPL", "MSFT", "NVDA", "AMD"])
        self.assertEqual(user_stocks, {"AMD", "NVDA", "ASELS.IS", "TSLA"})

    def test_groups_of_other_markets_stay_out(self):
        universe, _ = self.svc.build_universe("BIST 100")
        self.assertIn("ASELS.IS", universe)
        self.assertNotIn("AMD", universe)


class RunMarketTest(ValuationServiceTestCase):
    def test_fetches_in_batches_and_writes_scores_with_timestamp(self):
        import valuation_db
        fetcher = FakeFetcher()
        summary = self.run_market(fetcher, batch_size=2)

        self.assertEqual(sorted(fetcher.calls), ["AAPL", "AMD", "MSFT", "NVDA"])
        self.assertEqual(len(self.sleeps), 1)  # 4 hisse / 2'lik paket -> paketler arası 1 bekleme
        self.assertGreaterEqual(self.sleeps[0], 30)
        rows = valuation_db.get_rows("NASDAQ 100")
        self.assertEqual(sorted(rows), ["AAPL", "AMD", "MSFT", "NVDA"])
        self.assertTrue(rows["AAPL"]["fetched_at"].startswith("2026-10-05T21:00"))
        self.assertIn("Nihai Skor", rows["AAPL"]["scored"])
        self.assertEqual(rows["AAPL"]["score"], rows["AAPL"]["scored"]["Nihai Skor"])
        self.assertEqual(summary["written"], 4)
        run = valuation_db.get_run("NASDAQ 100")
        self.assertEqual(run["universe_size"], 4)
        self.assertEqual(run["fetched"], 4)
        self.assertEqual(run["aborted"], 0)

    def test_scores_are_relative_to_whole_universe(self):
        import valuation_db
        self.run_market(FakeFetcher(pe={"AAPL": 10.0, "MSFT": 20.0, "NVDA": 20.0, "AMD": 40.0}))
        rows = valuation_db.get_rows("NASDAQ 100")
        # Medyan F/K 20 -> AAPL %50 iskontolu, AMD %100 primli.
        self.assertEqual(rows["AAPL"]["scored"]["Alt Sektör İskontosu %"], 50.0)
        self.assertEqual(rows["AMD"]["scored"]["Alt Sektör İskontosu %"], -100.0)
        self.assertGreater(rows["AAPL"]["score"], rows["AMD"]["score"])

    def test_rerun_overwrites_rows(self):
        import valuation_db
        self.run_market(FakeFetcher(pe={"AAPL": 10.0}))
        first = valuation_db.get_rows("NASDAQ 100")["AAPL"]
        self.run_market(FakeFetcher(pe={"AAPL": 30.0}))
        rows = valuation_db.get_rows("NASDAQ 100")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows["AAPL"]["raw"]["F/K"], 30.0)
        self.assertGreater(rows["AAPL"]["fetched_at"], first["fetched_at"])

    def test_removes_rows_no_longer_in_market_or_user_stocks(self):
        import valuation_db
        old = {"market": "NASDAQ 100", "raw": make_raw("X"), "scored": {"Nihai Skor": 1},
               "fetched_at": "2026-10-01T00:00:00Z", "scored_at": "2026-10-01T00:00:00Z",
               "source": valuation_db.SOURCE_ON_DEMAND}
        valuation_db.upsert_rows([{**old, "ticker": "GONE"}, {**old, "ticker": "TSLA"}])
        fetcher = FakeFetcher()
        summary = self.run_market(fetcher)

        rows = valuation_db.get_rows("NASDAQ 100")
        self.assertNotIn("GONE", rows)        # ne piyasada ne kullanıcı hisselerinde -> silindi
        self.assertIn("TSLA", rows)           # u2'nin kayıtlı seçimi -> kaldı ve güncellendi
        self.assertIn("TSLA", fetcher.calls)
        self.assertEqual(rows["TSLA"]["source"], valuation_db.SOURCE_ON_DEMAND)
        self.assertGreater(rows["TSLA"]["fetched_at"], "2026-10-05")
        self.assertEqual(summary["removed"], 1)

    def test_rate_limit_backs_off_then_aborts_and_keeps_old_data(self):
        import valuation_db
        self.run_market(FakeFetcher(pe={"NVDA": 11.0}))
        self.sleeps.clear()

        fetcher = FakeFetcher(rate_limited={"NVDA"})
        summary = self.run_market(fetcher)

        self.assertTrue(summary["aborted"])
        self.assertEqual(self.sleeps, [60.0, 120.0, 240.0])
        self.assertNotIn("AMD", fetcher.calls)  # bırakıldıktan sonra denenmedi
        rows = valuation_db.get_rows("NASDAQ 100")
        self.assertEqual(rows["NVDA"]["raw"]["F/K"], 11.0)  # eski veriyle skorlandı
        self.assertIn("AMD", rows)
        self.assertEqual(valuation_db.get_run("NASDAQ 100")["aborted"], 1)

    def test_reuses_recent_fetch_from_other_market(self):
        import valuation_db
        valuation_db.upsert_rows([{
            "market": "NYSE", "ticker": "MSFT", "raw": make_raw("MSFT", pe=33.0),
            "scored": {"Nihai Skor": 5}, "fetched_at": "2026-10-05T20:00:00Z",
            "scored_at": "2026-10-05T20:00:00Z",
        }])
        fetcher = FakeFetcher()
        summary = self.run_market(fetcher)
        self.assertNotIn("MSFT", fetcher.calls)
        self.assertEqual(summary["reused"], 1)
        self.assertEqual(valuation_db.get_rows("NASDAQ 100")["MSFT"]["raw"]["F/K"], 33.0)

    def test_main_fails_when_nothing_written(self):
        missing = FakeFetcher(missing={"AAPL", "MSFT", "NVDA", "AMD"})
        with mock.patch.object(self.svc.valuation, "fetch_single_ticker_raw", missing), \
                mock.patch.object(self.svc.time, "sleep"), \
                mock.patch.object(self.svc, "SERVICE_TICKER_DELAY_S", None):
            self.assertEqual(self.svc.main(["--market", "nasdaq100", "--pause", "0"]), 1)


class SelectionTest(ValuationServiceTestCase):
    def test_reads_from_db_and_fetches_missing_with_db_peers(self):
        import valuation_db
        self.run_market(FakeFetcher(pe={"AAPL": 10.0, "MSFT": 20.0, "NVDA": 20.0, "AMD": 40.0}))

        fetcher = FakeFetcher(pe={"ZZZ": 10.0})
        rows, summary = self.svc.get_scores_for_selection(
            "NASDAQ 100", ["aapl", "ZZZ"], fetcher=fetcher, sleep=self.sleeps.append, now=self.clock)

        self.assertEqual(fetcher.calls, ["ZZZ"])
        self.assertEqual(summary["from_db"], 1)
        self.assertEqual(summary["fetched"], 1)
        self.assertEqual([r["Hisse"] for r in rows], ["AAPL", "ZZZ"])
        # ZZZ, veritabanındaki 4 akranla birlikte skorlandı: medyan F/K 20 -> %50 iskonto.
        self.assertEqual(rows[1]["Alt Sektör İskontosu %"], 50.0)
        saved = valuation_db.get_rows("NASDAQ 100")["ZZZ"]
        self.assertEqual(saved["source"], valuation_db.SOURCE_ON_DEMAND)

        again = FakeFetcher()
        self.svc.get_scores_for_selection("NASDAQ 100", ["ZZZ"], fetcher=again,
                                          sleep=self.sleeps.append, now=self.clock)
        self.assertEqual(again.calls, [])  # artık veritabanında

    def test_on_demand_ticker_not_in_user_stocks_is_removed_next_run(self):
        import valuation_db
        self.svc.get_scores_for_selection("NASDAQ 100", ["ZZZ"], fetcher=FakeFetcher(),
                                          sleep=self.sleeps.append, now=self.clock)
        self.assertIn("ZZZ", valuation_db.get_rows("NASDAQ 100"))
        self.run_market(FakeFetcher())
        self.assertNotIn("ZZZ", valuation_db.get_rows("NASDAQ 100"))

    def test_on_demand_user_ticker_is_refreshed_next_run(self):
        import valuation_db
        self.svc.get_scores_for_selection("NASDAQ 100", ["TSLA"], fetcher=FakeFetcher(),
                                          sleep=self.sleeps.append, now=self.clock)
        fetcher = FakeFetcher(pe={"TSLA": 99.0})
        self.run_market(fetcher)
        self.assertIn("TSLA", fetcher.calls)
        self.assertEqual(valuation_db.get_rows("NASDAQ 100")["TSLA"]["raw"]["F/K"], 99.0)


class BalanceSheetDateTest(ValuationServiceTestCase):
    def test_dates_are_stored_and_old_rows_without_them_still_score(self):
        import valuation
        info = {"sector": "Technology", "industry": "Semis", "trailingPE": 20, "marketCap": 1,
                "regularMarketPrice": 1, "mostRecentQuarter": 1782777600,
                "earningsTimestampStart": 1793232000}
        with mock.patch.object(valuation, "is_info_meaningful", return_value=True):
            raw = valuation._raw_from_info("AAPL", info, {})
        self.assertEqual(raw["_most_recent_quarter"], "2026-06-30")
        self.assertEqual(raw["_next_earnings"], "2026-10-29")

        old = make_raw("OLD")
        del old["_most_recent_quarter"], old["_next_earnings"]
        rows = valuation.calculate_sector_relative_scores([make_raw("AAPL"), old])
        self.assertEqual(rows[0]["Bilanço Tarihi"], "2026-06-30")
        self.assertEqual(rows[0]["Sonraki Bilanço"], "2026-10-29")
        self.assertIsNone(rows[1]["Bilanço Tarihi"])

    def test_service_writes_dates_into_scores(self):
        import valuation_db
        self.run_market(FakeFetcher())
        self.assertEqual(valuation_db.get_rows("NASDAQ 100")["AAPL"]["scored"]["Bilanço Tarihi"], "2026-06-30")


class WeeklyHourlyCycleTest(ValuationServiceTestCase):
    """Russell 2000: haftada 1 döngü, her saat 1 paket."""

    def setUp(self):
        super().setUp()
        storage.write("custom_tickers", "u1", {"NASDAQ 100": ["AAPL"],
                                               "Russell 2000": [f"R{i}" for i in range(5)]})
        storage.write("custom_tickers", "u2", {"NASDAQ 100": ["MSFT", "NVDA"], "Russell 2000": []})
        self.service = self.svc.SERVICES["russell2000"]

    def step(self, fetcher, when):
        self.clock.t = when
        return self.svc.run_cycle_step(self.service, batch_size=2, ticker_delay_s=None,
                                       fetcher=fetcher, sleep=self.sleeps.append, now=self.clock)

    def test_one_batch_per_step_and_scores_written_after_last(self):
        import valuation_db
        sat = datetime(2026, 10, 10, 4, 5, tzinfo=timezone.utc)  # Cumartesi 00:05 ET
        fetcher = FakeFetcher()

        first = self.step(fetcher, sat)
        self.assertEqual(first["action"], "step")
        self.assertEqual(len(fetcher.calls), 2)
        self.assertEqual(valuation_db.get_rows("Russell 2000"), {})  # skorlar henüz yazılmadı
        progress = self.svc.cycle_progress("Russell 2000")
        self.assertEqual((progress["done"], progress["total"]), (2, 5))

        self.step(fetcher, sat + timedelta(hours=1))
        last = self.step(fetcher, sat + timedelta(hours=2))
        self.assertEqual(last["action"], "finished")
        self.assertEqual(len(fetcher.calls), 5)
        self.assertEqual(len(valuation_db.get_rows("Russell 2000")), 5)
        self.assertIsNone(valuation_db.get_cycle("Russell 2000"))
        self.assertEqual(self.sleeps, [])  # paketler arası bekleme yok; aralık timer'ın 1 saati

        # Aynı hafta içinde yeni döngü başlamaz.
        self.assertEqual(self.step(fetcher, sat + timedelta(hours=3))["action"], "idle")
        self.assertEqual(self.step(fetcher, sat + timedelta(days=3))["action"], "idle")
        # Bir sonraki Cumartesi başlar.
        self.assertEqual(self.step(fetcher, sat + timedelta(days=7))["action"], "step")

    def test_not_started_on_weekdays(self):
        tue = datetime(2026, 10, 6, 14, 5, tzinfo=timezone.utc)
        fetcher = FakeFetcher()
        self.assertEqual(self.step(fetcher, tue)["action"], "idle")
        self.assertEqual(fetcher.calls, [])

    def test_rate_limited_tickers_retried_next_hour(self):
        sat = datetime(2026, 10, 10, 4, 5, tzinfo=timezone.utc)
        self.step(FakeFetcher(rate_limited={"R0"}), sat)
        self.assertEqual(self.svc.cycle_progress("Russell 2000")["done"], 0)
        fetcher = FakeFetcher()
        self.step(fetcher, sat + timedelta(hours=1))
        self.assertEqual(fetcher.calls, ["R0", "R1"])

    def test_main_runs_step_for_weekly_service(self):
        with mock.patch.object(self.svc, "run_cycle_step", return_value={"action": "idle"}) as step, \
                mock.patch.object(self.svc, "run_market") as full:
            self.assertEqual(self.svc.main(["--market", "russell2000"]), 0)
        step.assert_called_once()
        full.assert_not_called()


class ScheduleTest(unittest.TestCase):
    def test_next_start(self):
        import valuation_service as svc
        mon = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)  # Pazartesi 15:00 TRT
        self.assertEqual(svc.next_start(svc.SERVICES["bist100"], mon),
                         datetime(2026, 10, 5, 15, 40, tzinfo=timezone.utc))
        self.assertEqual(svc.next_start(svc.SERVICES["nasdaq100"], mon),
                         datetime(2026, 10, 5, 21, 15, tzinfo=timezone.utc))
        self.assertEqual(svc.next_start(svc.SERVICES["russell2000"], mon),
                         datetime(2026, 10, 10, 4, 5, tzinfo=timezone.utc))
        fri_night = datetime(2026, 10, 9, 22, 0, tzinfo=timezone.utc)  # Cuma, BIST saati geçti
        self.assertEqual(svc.next_start(svc.SERVICES["bist100"], fri_night),
                         datetime(2026, 10, 12, 15, 40, tzinfo=timezone.utc))


class SmallSectorAndMissingDataTest(unittest.TestCase):
    def test_small_sector_counts_as_one_and_missing_gets_no_points(self):
        import valuation
        raws = [make_raw(t, pe=pe, sub="Big") for t, pe in (("A", 10.0), ("B", 20.0), ("C", 20.0), ("D", 40.0))]
        raws.append(make_raw("NOPE", pe=None, sub="Big"))                 # F/K yok -> Y
        raws += [make_raw("S1", pe=5.0, sub="Small"), make_raw("S2", pe=50.0, sub="Small")]  # 2 hisse -> U
        rows = {r["Hisse"]: r for r in valuation.calculate_sector_relative_scores(raws)}

        self.assertFalse(rows["A"]["_az_hisseli"])
        self.assertEqual(rows["A"]["Alt Sektör İskontosu %"], 50.0)
        self.assertTrue(rows["S1"]["_az_hisseli"])
        self.assertEqual(rows["S1"]["Alt Sektör İskontosu %"], 1.0)   # gerçek iskonto %81.8 değil
        self.assertEqual(rows["S2"]["Alt Sektör İskontosu %"], 1.0)
        self.assertIsNone(rows["NOPE"]["Alt Sektör İskontosu %"])
        # Fark: iskonto 1 -> 5p / Y -> 0p, ve F/K yokken PEG de hesaplanmaz (10p).
        self.assertEqual(rows["S1"]["Nihai Skor"] - rows["NOPE"]["Nihai Skor"], 15)
        self.assertIsNone(rows["NOPE"]["PEG"])
        # Eksik F/K medyana katılmadı: Big medyanı 20.
        self.assertEqual(rows["A"]["Alt Sektör Ort. F/K"], 20.0)

    def test_display_marks_u_and_y(self):
        import pandas as pd
        import valuation
        raws = [make_raw("S1", pe=5.0, sub="Small"), make_raw("NOPE", pe=None, sub="Small")]
        raws[1]["PEG"] = None
        df = valuation.prepare_display_df(pd.DataFrame(valuation.calculate_sector_relative_scores(raws)))
        self.assertNotIn("_az_hisseli", df.columns)
        cell = valuation._format_cell
        s1, nope = df.iloc[0], df.iloc[1]
        self.assertEqual(cell(s1["Alt Sektör İskontosu %"]), "U")
        self.assertEqual(cell(s1["Alt Sektör Ort. F/K"]), "U")
        self.assertEqual(cell(nope["Alt Sektör İskontosu %"]), "Y")  # F/K yok: Y öncelikli
        self.assertEqual(cell(nope["PEG"]), "Y")
        self.assertEqual(cell(nope["F/K"]), "Y")
        self.assertEqual(cell(s1["F/K"]), "5")
        self.assertEqual(cell(s1["Bilanço Tarihi"]), "2026-06-30")


class PegAndEtfTest(ValuationServiceTestCase):
    def test_peg_only_when_pe_positive(self):
        import valuation
        base = {"sector": "Technology", "industry": "Semis", "marketCap": 1, "pegRatio": 0.8}
        with mock.patch.object(valuation, "is_info_meaningful", return_value=True):
            self.assertEqual(valuation._raw_from_info("A", {**base, "trailingPE": 15}, {})["PEG"], 0.8)
            self.assertIsNone(valuation._raw_from_info("B", {**base, "trailingPE": -4}, {})["PEG"])
            self.assertIsNone(valuation._raw_from_info("C", base, {})["PEG"])
            tp = {**base, "pegRatio": None, "trailingPegRatio": 1.2, "trailingPE": 15}
            self.assertEqual(valuation._raw_from_info("D", tp, {})["PEG"], 1.2)
        # Kural öncesi kaydedilmiş satır: F/K yok ama PEG var -> skorlamada PEG yok sayılır.
        old = make_raw("OLD", pe=None)
        rows = valuation.calculate_sector_relative_scores([old, make_raw("NEW")])
        self.assertIsNone(rows[0]["PEG"])
        self.assertEqual(rows[1]["PEG"], 0.9)

    def test_etf_detected_from_quote_type(self):
        import valuation
        raw = valuation._raw_from_info("XLF", {"quoteType": "ETF", "sector": None}, {})
        self.assertTrue(valuation.is_excluded(raw))

    def test_etf_not_scored_not_in_medians_and_reported(self):
        import valuation_db
        storage.write("custom_tickers", "u2", {"NASDAQ 100": ["MSFT", "NVDA", "XLF"]})
        fetcher = FakeFetcher(pe={"AAPL": 10.0, "MSFT": 20.0, "NVDA": 20.0, "AMD": 40.0}, etf={"XLF"})
        self.run_market(fetcher)
        rows = valuation_db.get_rows("NASDAQ 100")
        self.assertEqual(rows["XLF"]["scored"], {"Hisse": "XLF", "_excluded": "ETF"})
        self.assertIsNone(rows["XLF"]["score"])
        self.assertEqual(rows["AAPL"]["scored"]["Alt Sektör Ort. F/K"], 20.0)

        again = FakeFetcher()
        out, summary = self.svc.get_scores_for_selection(
            "NASDAQ 100", ["AAPL", "XLF"], fetcher=again, sleep=self.sleeps.append, now=self.clock)
        self.assertEqual([r["Hisse"] for r in out], ["AAPL"])
        self.assertEqual(summary["excluded"], ["XLF"])
        self.assertEqual(again.calls, [])  # ETF kaydı veritabanında, tekrar çekilmez

    def test_on_demand_etf_saved_as_excluded(self):
        import valuation_db
        self.run_market(FakeFetcher())
        out, summary = self.svc.get_scores_for_selection(
            "NASDAQ 100", ["SPY", "AAPL"], fetcher=FakeFetcher(etf={"SPY"}),
            sleep=self.sleeps.append, now=self.clock)
        self.assertEqual(summary["excluded"], ["SPY"])
        self.assertEqual([r["Hisse"] for r in out], ["AAPL"])
        self.assertTrue(valuation_db.get_rows("NASDAQ 100")["SPY"]["scored"]["_excluded"])


class RateLimitDetectionTest(unittest.TestCase):
    def test_detects_yfinance_and_http_429(self):
        import valuation

        class YFRateLimitError(Exception):
            pass

        self.assertTrue(valuation.is_rate_limit_error(YFRateLimitError("x")))
        self.assertTrue(valuation.is_rate_limit_error(Exception("HTTP Error 429: Too Many Requests")))
        self.assertFalse(valuation.is_rate_limit_error(Exception("404 Not Found")))

    def test_fetch_raises_only_when_asked(self):
        import valuation

        class Boom:
            def __init__(self, t):
                raise Exception("429 Too Many Requests")

        with mock.patch.object(valuation.yf, "Ticker", Boom):
            self.assertIsNone(valuation.fetch_single_ticker_raw("AAPL", {}))
            with self.assertRaises(valuation.YahooRateLimited):
                valuation.fetch_single_ticker_raw("AAPL", {}, raise_on_rate_limit=True)


if __name__ == "__main__":
    unittest.main()
