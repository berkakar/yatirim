"""Piyasa Duyarlılığı servisi (market_sentiment.py): bileşen hesapları, bileşik skor,
etiketler ve kayıt - Yahoo yerine sahte kapanışlarla."""

import os
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

import market_sentiment as ms
import storage


def _series(values, dates):
    return pd.Series(values, index=dates, dtype=float)


def make_closes(n=600, trend=0.001, seed=1, stocks=20):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n)
    index = 100 * np.exp(np.cumsum(trend + rng.normal(0, 0.01, n)))
    vix = 20 + np.cumsum(rng.normal(0, 0.3, n)).clip(-10, 30)
    tlt = 100 * np.exp(np.cumsum(rng.normal(0, 0.006, n)))
    data = {"^NDX": index, "^VXN": vix, "TLT": tlt}
    for i in range(stocks):
        data[f"S{i}"] = 50 * np.exp(np.cumsum(trend + rng.normal(0, 0.015, n)))
    return pd.DataFrame(data, index=dates)


class ComponentTests(unittest.TestCase):
    def test_pct_above_sma_counts_only_valid(self):
        dates = pd.bdate_range("2024-01-01", periods=4)
        closes = pd.DataFrame({"A": [1, 2, 3, 4], "B": [4, 3, 2, 1], "C": [np.nan, np.nan, 1, 1]}, index=dates)
        pct = ms.pct_above_sma(closes, 2)
        # Son gün: A ortalamanın üstünde, B altında, C eşit (üstünde değil) -> 1/3
        self.assertAlmostEqual(pct.iloc[-1], 100 / 3)
        self.assertTrue(np.isnan(pct.iloc[0]))

    def test_highs_lows_neutral_when_none(self):
        dates = pd.bdate_range("2024-01-01", periods=10)
        flat = pd.DataFrame({"A": [1, 2, 1, 2, 1, 2, 1, 2, 1, 2]}, index=dates, dtype=float)
        s = ms.highs_lows_score(flat, window=3, smooth=1)
        self.assertTrue(np.isnan(s.iloc[0]))   # pencere dolmadan veri yok
        self.assertEqual(s.iloc[-1], 100.0)    # 2 = son 3 günün zirvesi

    def test_uptrend_scores_higher_than_downtrend(self):
        up = make_closes(trend=0.002)
        down = make_closes(trend=-0.002)
        def score(df):
            stocks = df[[c for c in df if c.startswith("S")]]
            return ms.compute_components(df["^NDX"], df["^VXN"], df["TLT"], stocks)
        up_c, down_c = score(up), score(down)
        self.assertGreater(up_c["breadth"].iloc[-1], down_c["breadth"].iloc[-1])
        self.assertTrue(((up_c["score"].dropna() >= 0) & (up_c["score"].dropna() <= 100)).all())

    def test_put_call_score_bounds(self):
        self.assertEqual(ms.put_call_score(0.4), 100)
        self.assertEqual(ms.put_call_score(1.5), 0)
        self.assertAlmostEqual(ms.put_call_score(0.85), 50)
        self.assertIsNone(ms.put_call_score(None))

    def test_labels(self):
        self.assertEqual(ms.label_for(10)[0], "Aşırı Korku")
        self.assertEqual(ms.label_for(50)[0], "Nötr")
        self.assertEqual(ms.label_for(75)[0], "Aşırı Açgözlülük")
        self.assertEqual(ms.label_for(None)[0], "Veri yok")


class BistTests(unittest.TestCase):
    def test_realized_volatility_annualized(self):
        dates = pd.bdate_range("2024-01-01", periods=30)
        flat = pd.Series(100.0, index=dates)
        self.assertEqual(ms.realized_volatility(flat).iloc[-1], 0)
        alt = pd.Series([100, 101] * 15, index=dates, dtype=float)
        self.assertGreater(ms.realized_volatility(alt).iloc[-1], 10)

    def test_usd_base_removes_inflation_drift(self):
        # TL'de her gün %0,2 artan ama dolar bazında yatay bir piyasa: TL bazında
        # momentum hep yüksek, dolar bazında nötr civarı olmalı.
        n = 600
        dates = pd.bdate_range("2023-01-02", periods=n)
        rng = np.random.default_rng(5)
        fx = pd.Series(20 * np.exp(np.arange(n) * 0.002), index=dates)
        usd_index = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
        idx = pd.Series(usd_index, index=dates) * fx
        stocks = pd.DataFrame({f"S{i}.IS": idx * (1 + 0.01 * i) for i in range(5)})
        tl = ms.compute_components(idx, None, fx, stocks)
        usd = ms.compute_components(idx, None, fx, stocks, usd_fx=fx)
        self.assertGreater(tl["momentum_raw"].iloc[-1], usd["momentum_raw"].iloc[-1])
        self.assertAlmostEqual(usd["momentum_raw"].iloc[-1],
                               ((usd_index[-1] / pd.Series(usd_index).tail(125).mean()) - 1) * 100, places=6)
        self.assertTrue(usd["volatility"].notna().any())   # gerçekleşen oynaklıktan

    def test_drop_bad_bist_data(self):
        dates = pd.bdate_range("2024-01-01", periods=40)
        good = pd.Series(np.linspace(10, 12, 40), index=dates)
        jump = good.copy(); jump.iloc[30] = jump.iloc[29] * 1.3      # %30 sıçrama: hatalı mum
        frozen = pd.Series(np.r_[np.linspace(10, 12, 20), [12.0] * 20], index=dates)
        us = good.copy(); us.iloc[30] *= 1.3                          # ABD hissesine marj uygulanmaz
        closes = pd.DataFrame({"GOOD.IS": good, "JUMP.IS": jump, "FROZEN.IS": frozen, "AAPL": us})
        clean, dropped = ms.drop_bad_bist_data(closes)
        self.assertEqual(sorted(dropped), ["FROZEN.IS", "JUMP.IS"])
        self.assertEqual(list(clean.columns), ["GOOD.IS", "AAPL"])

    def test_compute_bist_market(self):
        base = make_closes(trend=0.003, seed=7)
        closes = pd.DataFrame({
            "XU100.IS": base["^NDX"], "TRY=X": 20 * np.exp(np.arange(len(base)) * 0.001),
            **{f"S{i}.IS": base[f"S{i}"] for i in range(20)},
        }, index=base.index)
        requested = []
        def downloader(tickers):
            requested.extend(tickers)
            return closes
        snap = ms.compute_market("bist100", downloader=downloader,
                                 universe=[f"S{i}.IS" for i in range(20)],
                                 put_call_fetcher=lambda s: self.fail("BIST'te put/call çekilmemeli"))
        self.assertEqual(snap["market"], "BIST 100")
        self.assertIsNone(snap["put_call"])
        self.assertIn("XU100.IS", requested)
        self.assertIn("TRY=X", requested)
        self.assertNotIn(None, requested)
        self.assertIn("dolar bazında", snap["components"]["momentum"]["detail"])
        self.assertIn("USD/TRY", snap["components"]["safe_haven"]["detail"])
        self.assertIsNotNone(snap["components"]["volatility"]["score"])

    def test_bist_requires_fx(self):
        base = make_closes()
        closes = pd.DataFrame({"XU100.IS": base["^NDX"], "S0.IS": base["S0"]})
        with self.assertRaises(RuntimeError):
            ms.compute_market("bist100", downloader=lambda t: closes, universe=["S0.IS"], put_call_fetcher=None)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"YATIRIM_DB_PATH": os.path.join(self.tmp.name, "t.db")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_compute_and_save_snapshot(self):
        closes = make_closes()
        tickers = [f"S{i}" for i in range(20)] + ["MISSING"]
        snap = ms.compute_market(
            "nasdaq100", downloader=lambda t: closes, universe=tickers,
            put_call_fetcher=lambda sym: {"symbol": sym, "ratio": 0.8, "score": 57.1},
        )
        self.assertEqual(snap["market"], "NASDAQ 100")
        self.assertEqual(snap["universe_size"], 20)
        self.assertEqual(set(snap["components"]), set(ms.COMPONENTS))
        self.assertEqual(snap["as_of"], closes.index[-1].strftime("%Y-%m-%d"))
        self.assertLessEqual(len(snap["history"]), ms.HISTORY_DAYS)
        self.assertEqual(snap["history"][-1]["score"], snap["score"])

        ms.save_snapshot(snap)
        ms.save_snapshot({**snap, "market": "NYSE", "score": 10})
        data = ms.load_all()
        self.assertEqual(data["NASDAQ 100"]["score"], snap["score"])
        self.assertEqual(data["NYSE"]["score"], 10)

    def test_run_keeps_old_record_when_market_fails(self):
        ms.save_snapshot({"market": "NYSE", "score": 42})
        def boom(tickers):
            raise RuntimeError("Yahoo yok")
        failures = ms.run(["nyse"], pause_s=0, downloader=boom, universe=["A"], put_call_fetcher=None)
        self.assertEqual(failures, 1)
        self.assertEqual(ms.load_all()["NYSE"]["score"], 42)


if __name__ == "__main__":
    unittest.main()
