"""Günlük arşiv (market_archive.py): canlı / backfill satır kuralları, sektör
getirileri, eğitim tablosu ve dışa aktarma - Yahoo yerine sahte kapanışlarla."""

import os
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

import market_archive as ma
import market_sentiment as ms
from tests.test_market_sentiment import make_closes


def make_sector_closes(n=60):
    rng = np.random.default_rng(3)
    dates = pd.bdate_range("2026-06-01", periods=n)
    data = {s: 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, n))) for s in list(ms.SECTOR_ETFS) + ["SPY"]}
    return pd.DataFrame(data, index=dates)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"YATIRIM_DB_PATH": os.path.join(self.tmp.name, "t.db")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _run_nasdaq(self, closes, put_call=None):
        return ms.run(["nasdaq100"], pause_s=0, downloader=lambda t: closes, universe=[f"S{i}" for i in range(20)],
                      put_call_fetcher=lambda sym: put_call)

    def test_daily_run_writes_live_last_day_and_backfills_history(self):
        closes = make_closes()
        self.assertEqual(self._run_nasdaq(closes, {"symbol": "QQQ", "ratio": 0.8, "score": 57.1}), 0)
        df = ma.load_frame(ma.SENTIMENT_TABLE)
        snap = ms.load_all()["NASDAQ 100"]
        self.assertGreater(len(df), 100)
        self.assertEqual(df["date"].is_unique, True)
        last = df.iloc[-1]
        self.assertEqual(last["source"], ma.SOURCE_LIVE)
        self.assertEqual(last["date"].strftime("%Y-%m-%d"), snap["as_of"])
        self.assertAlmostEqual(last["score"], snap["score"], places=0)
        self.assertAlmostEqual(last["put_call_ratio"], 0.8)
        self.assertEqual(last["formula_version"], ms.FORMULA_VERSION)
        self.assertAlmostEqual(last["index_close"], closes["^NDX"].iloc[-1])
        self.assertTrue((df.iloc[:-1]["source"] == ma.SOURCE_BACKFILL).all())
        self.assertTrue(df.iloc[:-1]["put_call_ratio"].isna().all())

    def test_live_row_survives_next_day_and_rebuild(self):
        closes = make_closes()
        self._run_nasdaq(closes.iloc[:-1])          # dün canlı
        self._run_nasdaq(closes)                    # bugün canlı, dün backfill olarak tekrar gelir
        df = ma.load_frame(ma.SENTIMENT_TABLE).set_index("date")
        self.assertEqual((df["source"] == ma.SOURCE_LIVE).sum(), 2)
        yesterday = closes.index[-2]
        self.assertEqual(df.loc[yesterday, "source"], ma.SOURCE_LIVE)

        # rebuild: backfill satırları güncellenir, canlı satırlar korunur
        comp, size = ms.compute_market_series("nasdaq100", lambda t: closes * 1.1, [f"S{i}" for i in range(20)])
        ma.backfill_sentiment("NASDAQ 100", comp, size, ms.FORMULA_VERSION, rebuild=True)
        after = ma.load_frame(ma.SENTIMENT_TABLE).set_index("date")
        self.assertEqual(after.loc[yesterday, "index_close"], df.loc[yesterday, "index_close"])
        some_backfill = df[df["source"] == ma.SOURCE_BACKFILL].index[0]
        self.assertAlmostEqual(after.loc[some_backfill, "index_close"], df.loc[some_backfill, "index_close"] * 1.1)

    def test_backfill_without_rebuild_does_not_change_rows(self):
        closes = make_closes()
        self._run_nasdaq(closes)
        before = ma.load_frame(ma.SENTIMENT_TABLE)
        failures = ms.backfill(["nasdaq100"], years=10, sectors=False, downloader=lambda t: closes * 2,
                               pause_s=0, universe=[f"S{i}" for i in range(20)])
        self.assertEqual(failures, 0)
        after = ma.load_frame(ma.SENTIMENT_TABLE)
        pd.testing.assert_series_equal(before["index_close"], after["index_close"])

    def test_backfill_fills_empty_archive_from_since(self):
        closes = make_closes(n=900)
        failures = ms.backfill(["nasdaq100"], years=10, downloader=lambda t: closes if "^NDX" in t else
                               make_sector_closes(n=900).set_index(closes.index), pause_s=0,
                               universe=[f"S{i}" for i in range(20)])
        self.assertEqual(failures, 0)
        df = ma.load_frame(ma.SENTIMENT_TABLE)
        self.assertGreater(len(df), 500)
        self.assertTrue((df["source"] == ma.SOURCE_BACKFILL).all())
        self.assertEqual(len(ma.load_frame(ma.SECTOR_TABLE)), 900 * (len(ms.SECTOR_ETFS) + 1))

    def test_sector_frame_returns(self):
        closes = make_sector_closes()
        frame = ma.sector_frame(closes, ms.SECTOR_ETFS, "SPY")
        xlk = frame[frame["symbol"] == "XLK"].set_index("date")
        spy = frame[frame["symbol"] == "SPY"].set_index("date")
        d = xlk.index[-1]
        c = closes["XLK"]
        self.assertAlmostEqual(xlk.loc[d, "ret_5d"], (c.iloc[-1] / c.iloc[-6] - 1) * 100)
        self.assertAlmostEqual(xlk.loc[d, "ret_21d"], (c.iloc[-1] / c.iloc[-22] - 1) * 100)
        self.assertAlmostEqual(xlk.loc[d, "rel_5d_vs_benchmark"], xlk.loc[d, "ret_5d"] - spy.loc[d, "ret_5d"])
        self.assertEqual(len(frame), len(closes) * (len(ms.SECTOR_ETFS) + 1))

    def test_run_sectors_archives_and_backfill_sectors(self):
        closes = make_sector_closes()
        self.assertEqual(ms.run_sectors(downloader=lambda t: closes), 0)
        df = ma.load_frame(ma.SECTOR_TABLE)
        self.assertEqual(set(df["symbol"]), set(ms.SECTOR_ETFS) | {"SPY"})
        self.assertTrue((df[df["date"] == df["date"].max()]["source"] == ma.SOURCE_LIVE).all())

    def test_feature_frame_and_export(self):
        self._run_nasdaq(make_closes())
        ms.run_sectors(downloader=lambda t: make_sector_closes(n=600).set_index(make_closes().index))
        wide = ma.feature_frame()
        self.assertIn("nasdaq_100__score", wide.columns)
        self.assertIn("xlk__ret_5d", wide.columns)
        self.assertTrue(wide.index.is_monotonic_increasing)
        self.assertTrue(wide.index.is_unique)

        paths = ma.export(os.path.join(self.tmp.name, "ml"), "csv")
        self.assertEqual([os.path.basename(p) for p in paths],
                         ["sentiment_daily.csv", "sector_etf_daily.csv", "features_daily.csv"])
        back = pd.read_csv(paths[2])
        self.assertEqual(len(back), len(wide))
        status = ma.summary()
        self.assertIn("NASDAQ 100", [r["key"] for r in status])

    def test_archive_failure_does_not_fail_run(self):
        with mock.patch.object(ma, "record_sentiment", side_effect=RuntimeError("disk dolu")):
            self.assertEqual(self._run_nasdaq(make_closes()), 0)
        self.assertIn("NASDAQ 100", ms.load_all())


if __name__ == "__main__":
    unittest.main()
