"""Yapay zeka veri seti (ai_dataset.py): EMA, direnç (sızıntısız), VWAP
yaklaşığı, interpolasyon, temporal özellikler, birleştirme ve veritabanı
kayıt / gün ekleme - Yahoo yerine sahte barlarla."""

import os
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

import ai_dataset as ad

END = "2026-10-07"
VALUATION = {"valuation_score": 62.0, "valuation_sector_discount_pct": 12.5, "valuation_pe": 30.0,
             "valuation_sector_pe": 34.0, "sector": "Technology"}


def fake_ohlcv(last_day=END, seed=1):
    def fetch(ticker, start, end=None):
        dates = pd.bdate_range(start, last_day)
        rng = np.random.default_rng(seed)
        c = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, len(dates))))
        return pd.DataFrame({"open": c * 0.995, "high": c * 1.01, "low": c * 0.985, "close": c,
                             "volume": rng.integers(1_000_000, 2_000_000, len(dates))}, index=dates)
    return fetch


def fake_market(missing=(5, 100)):
    def load(start, end):
        dates = pd.bdate_range(start, end).delete(list(missing))
        return pd.DataFrame({
            "nasdaq_100__momentum": np.linspace(20, 80, len(dates)),
            "nasdaq_100__score": 50.0,                       # türetilmiş - veri setine alınmaz
            "nasdaq_100__put_call_ratio": np.nan,
            "nyse__score": 50.0,
            "xlk__ret_5d": np.linspace(-2, 2, len(dates)),
            "xlv__ret_5d": 0.5,
            "spy__close": 500.0,
            "xlk__rel_5d_vs_benchmark": 1.0,                 # türetilmiş - veri setine alınmaz
        }, index=dates)
    return load


class CalculationTests(unittest.TestCase):
    def test_ema_matches_pandas_and_needs_warmup(self):
        df = pd.DataFrame({"close": np.arange(1, 301, dtype=float)}, index=pd.bdate_range("2025-01-01", periods=300))
        out = ad.add_emas(df)
        self.assertTrue(out["ema200"].iloc[:199].isna().all())
        self.assertAlmostEqual(out["ema20"].iloc[-1], df["close"].ewm(span=20, adjust=False).mean().iloc[-1])
        self.assertGreater(out["dist_ema200_pct"].iloc[-1], 0)

    def test_vwap_falls_back_to_typical_price(self):
        idx = pd.bdate_range("2026-01-05", periods=3)
        df = pd.DataFrame({"high": [11.0, 12, 13], "low": [9.0, 10, 11], "close": [10.0, 11, 12]}, index=idx)
        out = ad.add_vwap(df, pd.Series([10.5], index=idx[:1]))
        self.assertEqual(out["vwap"].tolist(), [10.5, 11.0, 12.0])
        self.assertEqual(out["vwap_is_proxy"].tolist(), [0, 1, 1])

    def test_resistance_picks_nearest_pivot_above_close(self):
        # Kapanış 100'de yatay; tepeler: 10. gün 120, 40. gün 110.
        n = 80
        high = np.full(n, 100.0)
        high[10], high[40] = 120.0, 110.0
        close = pd.Series(np.full(n, 100.0), index=pd.bdate_range("2026-01-01", periods=n))
        out = ad.resistance_levels(pd.Series(high, index=close.index), close)
        at50 = out.iloc[50]                       # 1 ay [29,49], 2 ay [8,49]: 120 ve 110 -> en yakın 110
        self.assertAlmostEqual(at50["resistance_1m"], 110.0)
        self.assertAlmostEqual(at50["resistance_2m"], 110.0)
        self.assertTrue(np.isnan(at50["resistance_3m"]))          # 63 günlük geçmiş yok
        self.assertAlmostEqual(at50["resistance_nearest_dist_pct"], 10.0)
        last = out.iloc[-1]                       # 1 ay [58,78]: üstte tepe yok -> pencere zirvesi 100
        self.assertAlmostEqual(last["resistance_1m"], 100.0)
        self.assertAlmostEqual(last["resistance_3m"], 110.0)
        self.assertAlmostEqual(last["resistance_nearest_dist_pct"], 0.0)
        self.assertEqual(last["resistance_nearest_window"], 1)
        self.assertFalse(np.isnan(out["resistance_3m"].iloc[63]))

    def test_resistance_has_no_lookahead(self):
        n = 60
        idx = pd.bdate_range("2026-01-01", periods=n)
        close = pd.Series(np.linspace(100, 110, n), index=idx)
        high = close * 1.01
        base = ad.resistance_levels(high, close)
        spiked = high.copy()
        spiked.iloc[45:] = 500.0                  # gelecekteki sıçrama geçmiş günleri değiştirmemeli
        changed = ad.resistance_levels(spiked, close)
        pd.testing.assert_frame_equal(base.iloc[:46], changed.iloc[:46])

    def test_breakout_gives_non_positive_distance(self):
        n = 40
        idx = pd.bdate_range("2026-01-01", periods=n)
        close = pd.Series(np.linspace(100, 140, n), index=idx)   # sürekli yükselen: üstte direnç yok
        out = ad.resistance_levels(close + 0.5, close)
        self.assertLessEqual(out["resistance_1m_dist_pct"].iloc[-1], 0)
        self.assertEqual(out["resistance_nearest_window"].iloc[-1], 1)

    def test_fill_gaps_interpolates_inside_and_counts(self):
        idx = pd.bdate_range("2026-01-05", periods=5)
        df = pd.DataFrame({"a": [np.nan, 1.0, np.nan, 3.0, np.nan], "flag": [1, np.nan, 1, 1, 1]}, index=idx)
        out, report = ad.fill_gaps(df, skip=("flag",))
        self.assertEqual(out["a"].tolist(), [1.0, 1.0, 2.0, 3.0, 3.0])
        self.assertTrue(np.isnan(out["flag"].iloc[1]))
        self.assertEqual(out["interpolated_cells"].tolist(), [1, 0, 1, 0, 1])
        self.assertEqual(report, {"a": 3})

    def test_temporal_features(self):
        t = ad.temporal_features(pd.DatetimeIndex(["2026-10-05", "2026-10-09"]))   # pazartesi, cuma
        self.assertEqual(t["day_of_week"].tolist(), [0, 4])
        self.assertEqual(t["time_idx"].tolist(), [0, 1])
        self.assertAlmostEqual(t["dow_sin"].iloc[0], 0.0)
        self.assertTrue(((t[[c for c in t.columns if c.endswith(("_sin", "_cos"))]].abs()) <= 1).all().all())

    def test_add_valuation_uses_asof_history(self):
        idx = pd.bdate_range("2026-10-01", "2026-10-09")
        df = pd.DataFrame({"close": 1.0}, index=idx)
        history = pd.DataFrame({"valuation_score": [50.0, 60.0], "valuation_sector_discount_pct": [1.0, 2.0],
                                "valuation_pe": [np.nan, 20.0], "valuation_sector_pe": [25.0, 25.0]},
                               index=pd.to_datetime(["2026-10-05", "2026-10-08"]))
        out = ad.add_valuation(df, VALUATION, history)
        self.assertEqual(out["valuation_score"].tolist(), [50, 50, 50, 50, 50, 60, 60])   # 1,2 | 5,6,7 | 8,9
        self.assertEqual(out["valuation_is_snapshot"].tolist(), [1, 1, 0, 0, 0, 0, 0])
        self.assertTrue(np.isnan(out.loc["2026-10-06", "valuation_pe"]))
        no_hist = ad.add_valuation(df, VALUATION, None)
        self.assertTrue((no_hist["valuation_score"] == 62.0).all())
        self.assertTrue((no_hist["valuation_is_snapshot"] == 1).all())

    def test_market_columns_keep_nasdaq_and_etfs_without_derived(self):
        frame = fake_market(missing=())("2026-01-01", "2026-01-10")
        out = ad.market_columns(frame)
        self.assertIn("nasdaq_100__momentum", out)
        self.assertIn("xlv__ret_5d", out)
        self.assertIn("xlk__ret_5d", out)
        for col in ("nasdaq_100__score", "nasdaq_100__put_call_ratio", "nyse__score", "xlk__rel_5d_vs_benchmark"):
            self.assertNotIn(col, out)
        self.assertFalse([c for c in out if c.startswith("sector_etf__")])


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"YATIRIM_DB_PATH": os.path.join(self.tmp.name, "t.db")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _create(self, **kw):
        kw.setdefault("ohlcv_fetcher", fake_ohlcv())
        kw.setdefault("market_loader", fake_market())
        return ad.create("aapl", valuation=VALUATION, end=END, **kw)

    def test_build_keeps_two_years_and_fills_everything(self):
        df, meta = self._create()
        self.assertEqual(meta["ticker"], "AAPL")
        self.assertGreater(pd.Timestamp(meta["start"]), pd.Timestamp(END) - pd.DateOffset(years=2))
        self.assertLess(pd.Timestamp(meta["start"]), pd.Timestamp(END) - pd.DateOffset(years=2) + pd.Timedelta(days=5))
        self.assertGreater(len(df), 500)
        for col in ("open", "vwap", "close", "volume", "ema20", "ema50", "ema200", "sent_momentum", "rsi14",
                    "resistance_1m", "resistance_2m", "resistance_3m", "resistance_nearest_dist_pct",
                    "valuation_score", "nasdaq_100__momentum", "xlk__ret_5d", "month_sin"):
            self.assertIn(col, df.columns)
            self.assertFalse(df[col].isna().any(), col)
        for col in ("stock_sentiment", "nasdaq_100__score", "xlk__rel_5d_vs_benchmark", "sector_etf__ret_5d"):
            self.assertNotIn(col, df.columns)                            # türetilmiş sütunlar çıkarıldı
        self.assertEqual(meta["sector_etf"], "XLK")
        self.assertEqual(meta["filled"].get("nasdaq_100__momentum"), 2)  # arşivde eksik iki gün
        self.assertEqual(int((df["interpolated_cells"] > 0).sum()), 2)
        self.assertTrue((df["source"] == ad.SOURCE_BACKFILL).all())
        self.assertTrue((df["valuation_is_snapshot"] == 1).all())

    def test_missing_archive_warns(self):
        _, meta = self._create(market_loader=lambda s, e: pd.DataFrame())
        self.assertTrue(any("arşiv" in w.lower() for w in meta["warnings"]))

    def test_update_appends_only_new_days_and_keeps_extra(self):
        df, meta = self._create()
        ad.set_extra("AAPL", meta["end"], {"news_sentiment": 0.4})
        history = pd.DataFrame({"valuation_score": [65.0, 70.0], "valuation_sector_discount_pct": [10.0, 11.0],
                                "valuation_pe": [29.0, 28.0], "valuation_sector_pe": [34.0, 34.0]},
                               index=pd.to_datetime(["2026-10-07", "2026-10-09"]))
        added, _ = ad.update("AAPL", valuation=VALUATION, valuation_history=history,
                             ohlcv_fetcher=fake_ohlcv("2026-10-09"), market_loader=fake_market(), end="2026-10-09")
        self.assertEqual(added, 2)
        after = ad.load_dataset("AAPL")
        self.assertEqual(len(after), len(df) + 2)
        self.assertEqual(after.index.max(), pd.Timestamp("2026-10-09"))
        self.assertEqual(after["source"].iloc[-1], ad.SOURCE_DAILY)
        self.assertEqual(after["valuation_score"].iloc[-1], 70.0)               # 2026-10-09 skoru
        self.assertEqual(after["valuation_score"].iloc[-2], 65.0)               # 10-08: önceki günün skoru
        self.assertEqual(after["valuation_is_snapshot"].iloc[-1], 0)
        self.assertEqual(after["valuation_score"].iloc[-3], 62.0)        # eski gün değişmedi
        self.assertEqual(after.loc[meta["end"], "news_sentiment"], 0.4)
        self.assertEqual(ad.get_dataset_info("aapl")["end"], "2026-10-09")
        self.assertEqual(ad.update("AAPL", valuation=VALUATION, ohlcv_fetcher=fake_ohlcv("2026-10-09"),
                                   market_loader=fake_market(), end="2026-10-09")[0], 0)

    def test_rebuild_preserves_extra_and_delete(self):
        _, meta = self._create()
        ad.set_extra("AAPL", meta["end"], {"note": "x"})
        self._create()
        self.assertEqual(ad.load_dataset("AAPL").loc[meta["end"], "note"], "x")
        ad.set_extra("AAPL", meta["end"], {"note": None})
        self.assertNotIn("note", ad.load_dataset("AAPL").columns)
        with self.assertRaises(KeyError):
            ad.set_extra("AAPL", "1999-01-04", {"note": 1})
        self.assertGreater(ad.delete_dataset("AAPL"), 0)
        self.assertEqual(ad.list_datasets(), [])
        self.assertTrue(ad.load_dataset("AAPL").empty)

    def test_build_uses_daily_valuation_history_from_db(self):
        import valuation_db

        def write(day_utc, score):
            valuation_db.upsert_rows([{
                "market": ad.MARKET, "ticker": "AAPL", "raw": {"Hisse": "AAPL"},
                "scored": {"Hisse": "AAPL", "Nihai Skor": score, "F/K": 30.0, "Ana Sektör": "Technology"},
                "fetched_at": day_utc, "scored_at": day_utc,
            }])
        write("2026-10-05T21:30:00Z", 40)
        write("2026-10-07T21:30:00Z", 45)
        df, meta = self._create()
        self.assertEqual(meta["valuation_history_days"], 2)
        self.assertEqual(df.loc["2026-10-02", "valuation_score"], 40)       # geçmişten önce: en eski skor
        self.assertEqual(df.loc["2026-10-02", "valuation_is_snapshot"], 1)
        self.assertEqual(df.loc["2026-10-06", "valuation_score"], 40)
        self.assertEqual(df.loc["2026-10-07", "valuation_score"], 45)
        self.assertEqual(df.loc["2026-10-07", "valuation_is_snapshot"], 0)

    def test_valuation_from_db(self):
        import valuation_db

        valuation_db.upsert_rows([{
            "market": ad.MARKET, "ticker": "AAPL", "raw": {"Hisse": "AAPL"},
            "scored": {"Hisse": "AAPL", "Nihai Skor": 55, "Alt Sektör İskontosu %": -8.2, "F/K": 31.0,
                       "Alt Sektör Ort. F/K": 28.6, "Ana Sektör": "Technology", "Alt Sektör (İş Modeli)": "Donanım"},
            "fetched_at": "2026-10-07T21:00:00Z", "scored_at": "2026-10-07T21:00:00Z",
        }])
        v = ad.load_valuation("AAPL")
        self.assertEqual(v["valuation_score"], 55.0)
        self.assertEqual(v["valuation_sector_discount_pct"], -8.2)
        self.assertEqual(ad.YAHOO_SECTOR_TO_ETF[v["sector"]], "XLK")
        self.assertIsNone(ad.load_valuation("MSFT"))


class ScheduledUpdateTests(unittest.TestCase):
    """deploy/jobs.sh `ai-dataset` işi: ai_dataset.py update --all -> run_updates."""

    setUp = DatasetTests.setUp
    tearDown = DatasetTests.tearDown

    def _saved(self, ticker, vwap_source):
        df = pd.DataFrame({"close": [1.0]}, index=pd.to_datetime(["2026-10-07"]))
        meta = {"ticker": ticker, "start": "2026-10-07", "end": "2026-10-07", "columns": ["close"]}
        ad.save_dataset(df, meta, {"fetch_years": 3, "keep_years": 2, "vwap_source": vwap_source})

    def test_no_datasets_is_success(self):
        self.assertEqual(ad.run_updates(sleep=lambda s: None), 0)

    def test_updates_all_with_alpaca_only_where_used_and_reports_failures(self):
        self._saved("AAPL", "alpaca")
        self._saved("MSFT", "typical_price")
        self._saved("NVDA", "alpaca")
        calls, pauses, factory_calls = [], [], []
        alpaca = object()

        def updater(ticker, vwap_fetcher=None):
            calls.append((ticker, vwap_fetcher))
            if ticker == "MSFT":
                raise RuntimeError("Yahoo yok")
            return 1, {"end": "2026-10-08", "warnings": []}

        rc = ad.run_updates(sleep=pauses.append, updater=updater,
                            vwap_fetcher_factory=lambda: factory_calls.append(1) or alpaca)
        self.assertEqual(rc, 1)                                   # MSFT hata -> iş başarısız (Telegram)
        self.assertEqual(calls, [("AAPL", alpaca), ("MSFT", None), ("NVDA", alpaca)])
        self.assertEqual(factory_calls, [1])                      # Alpaca istemcisi bir kez kurulur
        self.assertEqual(pauses, [ad.UPDATE_PAUSE_S] * 2)

    def test_cli_update_all_runs_scheduled_update(self):
        with mock.patch.object(ad, "run_updates", return_value=0) as run:
            self.assertEqual(ad.main(["update", "--all"]), 0)
            run.assert_called_once_with(None)
            ad.main(["update", "--ticker", "aapl"])
            run.assert_called_with(["aapl"])

    def test_env_vwap_fetcher_needs_keys(self):
        with mock.patch.dict(os.environ, {"APCA_API_KEY_ID": "", "APCA_API_SECRET_KEY": ""}):
            self.assertIsNone(ad.env_vwap_fetcher())
        with mock.patch.dict(os.environ, {"APCA_API_KEY_ID": "k", "APCA_API_SECRET_KEY": "s"}):
            self.assertTrue(callable(ad.env_vwap_fetcher()))


class TrainingFrameTests(unittest.TestCase):
    setUp = DatasetTests.setUp
    tearDown = DatasetTests.tearDown

    def test_training_export_drops_meta_and_old_derived_columns(self):
        df, meta = ad.create("aapl", valuation=VALUATION, end=END, ohlcv_fetcher=fake_ohlcv(),
                             market_loader=fake_market())
        for col in ad.META_COLS:
            self.assertIn(col, df.columns)                               # tabloda / veritabanında duruyor
        train = ad.training_frame(df)
        self.assertFalse(set(ad.META_COLS) & set(train.columns))
        self.assertIn("close", train.columns)
        out = tempfile.mkdtemp()
        path = ad.export("AAPL", out)
        self.assertFalse(set(ad.META_COLS) & set(pd.read_csv(path).columns))
        full = pd.read_csv(ad.export("AAPL", out, all_columns=True))
        self.assertIn("interpolated_cells", full.columns)

    def test_old_saved_rows_lose_removed_columns_on_load(self):
        df = pd.DataFrame({"close": [1.0], "stock_sentiment": [50.0], "sector_etf__ret_5d": [1.0],
                           "xlk__rel_5d_vs_benchmark": [0.2], "nasdaq_100__score": [40.0]},
                          index=pd.to_datetime(["2026-10-07"]))
        meta = {"ticker": "OLD", "start": "2026-10-07", "end": "2026-10-07", "columns": list(df.columns)}
        ad.save_dataset(df, meta, {})
        self.assertEqual(list(ad.load_dataset("OLD").columns), ["close", "source"])


class RelativeFeatureTests(unittest.TestCase):
    def test_relative_features(self):
        idx = pd.bdate_range("2026-01-01", periods=25)
        df = pd.DataFrame({"open": 101.0, "high": 104.0, "low": 98.0, "close": 100.0, "vwap": 99.0,
                           "volume": [1000.0] * 24 + [3000.0]}, index=idx)
        out = ad.add_relative_features(df)
        self.assertTrue(np.isnan(out["gap_open_pct"].iloc[0]))
        self.assertAlmostEqual(out["gap_open_pct"].iloc[1], 1.0)
        self.assertAlmostEqual(out["range_pct"].iloc[-1], 6.0)
        self.assertAlmostEqual(out["close_vs_vwap_pct"].iloc[-1], (100 / 99 - 1) * 100)
        self.assertAlmostEqual(out["volume_rel20"].iloc[-1], 3.0)      # bugün ortalamaya katılmaz
        self.assertTrue(out["volume_rel20"].iloc[:20].isna().all())
        self.assertAlmostEqual(out["volume_rel20"].iloc[20], 1.0)

    def test_level_columns(self):
        for col in ("open", "high", "low", "vwap", "volume", "ema200", "resistance_nearest", "xlk__close",
                    "spy__close", "nasdaq_100__index_close"):
            self.assertTrue(ad.is_level_column(col), col)
        for col in ("close", "dist_ema200_pct", "resistance_nearest_dist_pct", "xlk__ret_5d", "range_pct",
                    "nasdaq_100__momentum"):
            self.assertFalse(ad.is_level_column(col), col)


class TrainingSelectionTests(unittest.TestCase):
    def test_only_embedding_calendar_and_etf_1d_returns(self):
        cols = list(ad.TEMPORAL_COLS) + ["xlk__ret_1d", "xlk__ret_5d", "xlk__ret_21d", "xlk__close",
                                         "spy__ret_1d", "spy__ret_21d", "nasdaq_100__momentum", "close"]
        train = ad.training_frame(pd.DataFrame(columns=cols))
        self.assertEqual(list(train.columns), list(ad.TRAINING_TEMPORAL_COLS)
                         + ["xlk__ret_1d", "spy__ret_1d", "nasdaq_100__momentum", "close"])


class TrainingValuationTests(unittest.TestCase):
    def test_nearest_resistance_not_in_training(self):
        cols = ["resistance_1m_dist_pct", "resistance_2m_dist_pct", "resistance_3m_dist_pct", "resistance_nearest",
                "resistance_nearest_dist_pct", "resistance_nearest_window"]
        self.assertEqual(list(ad.training_frame(pd.DataFrame(columns=cols)).columns), cols[:3])

    def test_sent_momentum_raw_not_in_training(self):
        train = ad.training_frame(pd.DataFrame(columns=["sent_momentum_raw", "sent_momentum", "dist_ema50_pct"]))
        self.assertEqual(list(train.columns), ["sent_momentum", "dist_ema50_pct"])

    def test_only_selected_valuation_columns_in_training(self):
        train = ad.training_frame(pd.DataFrame(columns=list(ad.VALUATION_FIELDS) + ["close"]))
        self.assertEqual(set(train.columns), set(ad.TRAINING_VALUATION_COLS) | {"close"})
        for col in ("valuation_pe", "valuation_sector_pe", "valuation_roe_pct", "valuation_roa_pct",
                    "valuation_gross_margin_pct", "valuation_interest_coverage", "valuation_debt_assets_pct",
                    "valuation_quick_ratio", "valuation_asset_turnover"):
            self.assertTrue(ad.is_training_excluded(col), col)


class TrainingViewTests(unittest.TestCase):
    setUp = DatasetTests.setUp
    tearDown = DatasetTests.tearDown

    def test_training_frame_keeps_close_and_relatives_drops_levels(self):
        df, _ = ad.create("aapl", valuation=VALUATION, end=END, ohlcv_fetcher=fake_ohlcv(),
                          market_loader=fake_market())
        for col in ad.RELATIVE_COLS:
            self.assertFalse(df[col].isna().any(), col)                  # 3 yıllık veride ısınma geride kalır
        self.assertEqual(ad.missing_relative_cols(df), [])
        train = ad.training_frame(df)
        self.assertIn("close", train.columns)
        for col in ad.RELATIVE_COLS + ("dist_ema200_pct", "resistance_1m_dist_pct") + ad.TRAINING_TEMPORAL_COLS:
            self.assertIn(col, train.columns)
        for col in ("xlk__ret_5d", "xlv__ret_5d", "dow_sin", "month_cos", "quarter", "year", "day_of_year"):
            self.assertIn(col, df.columns)                               # tabloda duruyor
            self.assertNotIn(col, train.columns)                         # eğitimde yok
        for col in ("open", "vwap", "volume", "ema200", "resistance_1m", "spy__close", "interpolated_cells"):
            self.assertIn(col, df.columns)                               # tabloda / veritabanında duruyor
            self.assertNotIn(col, train.columns)
        self.assertEqual(list(pd.read_csv(ad.export("AAPL", tempfile.mkdtemp()), index_col=0).columns),
                         list(train.columns))


class RedundancyTests(unittest.TestCase):
    def test_report_finds_constant_identical_and_correlated(self):
        idx = pd.bdate_range("2026-01-01", periods=50)
        x = np.linspace(1, 50, 50)
        df = pd.DataFrame({"close": x, "copy": x, "noisy": x + np.sin(x), "flat": 3.0,
                           "other": np.cos(x), "vwap_is_proxy": 1, "source": "backfill", "sub_sector": "A"}, index=idx)
        rep = ad.redundancy_report(df, 0.95)
        self.assertEqual(rep["constant"], ["flat"])                    # bayrak sütunları sayılmaz
        self.assertEqual(rep["identical"], [("close", "copy")])
        self.assertIn(("close", "copy"), [(a, b) for a, b, _ in rep["pairs"]])
        self.assertNotIn("other", {c for p in rep["pairs"] for c in p[:2]})

    def test_etf_coverage(self):
        present, missing = ad.etf_coverage(["xlk__close", "spy__close", "xlv__ret_5d"])
        self.assertEqual(present, ["XLK", "SPY"])
        self.assertIn("XLV", missing)                                  # yalnızca getirisi var, kapanışı yok
        self.assertEqual(len(present) + len(missing), 12)


if __name__ == "__main__":
    unittest.main()
