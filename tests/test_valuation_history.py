"""Ucuzluk Skoru geçmişinin bilanço tablolarından yeniden hesabı
(valuation_history.py) - Yahoo yerine yfinance biçiminde sahte tablolarla."""

import os
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

import valuation_history as vh

QUARTERS = pd.to_datetime(["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"])
YEARS = pd.to_datetime(["2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"])
EARNINGS = pd.to_datetime(["2025-07-30", "2025-10-29", "2026-01-28", "2026-04-29", "2026-07-29"])


def yf_table(rows: dict, periods) -> pd.DataFrame:
    """yfinance düzeni: satır kalem, sütun dönem sonu (yeniden eskiye)."""
    df = pd.DataFrame(rows, index=periods).T
    return df[df.columns[::-1]]


def fake_fundamentals():
    q_income = yf_table({
        "Total Revenue": [100, 110, 120, 130, 140], "Gross Profit": [50, 55, 60, 65, 70],
        "Net Income": [10, 12, 14, 16, 30], "Diluted EPS": [1.0, 1.2, 1.4, 1.6, 3.0],
        "EBITDA": [20, 22, 24, 26, 28], "Interest Expense": [2, 2, 2, 2, 2],
    }, QUARTERS)
    q_balance = yf_table({
        "Stockholders Equity": [200] * 5, "Total Assets": [400] * 5, "Total Debt": [100, 100, 100, 100, 200],
        "Current Assets": [150] * 5, "Current Liabilities": [100] * 5, "Inventory": [30] * 5,
    }, QUARTERS)
    a_income = yf_table({
        "Total Revenue": [300, 360, 420, 460], "Gross Profit": [150, 180, 210, 230],
        "Net Income": [30, 36, 42, 52], "Diluted EPS": [3.0, 3.6, 4.2, 5.2],
        "EBITDA": [60, 70, 80, 90], "Interest Expense": [8, 8, 8, 8],
    }, YEARS)
    a_balance = yf_table({
        "Stockholders Equity": [150, 170, 190, 200], "Total Assets": [300, 340, 380, 400],
        "Total Debt": [150, 140, 120, 100], "Current Assets": [120, 130, 140, 150],
        "Current Liabilities": [100] * 4, "Inventory": [20] * 4,
    }, YEARS)
    return {"quarterly_income": q_income, "quarterly_balance": q_balance, "annual_income": a_income,
            "annual_balance": a_balance, "earnings_dates": list(EARNINGS)}


CURRENT_RAW = {"F/K": 30.0, "PEG": 1.5, "Borç / Özsermaye": 1.0, "Cari Oran": 1.5,
               "Öz Sermaye Getirisi (ROE) %": 99.0}
CURRENT_SCORED = {"Hisse": "AAPL", "Nihai Skor": 50, "Alt Sektör Ort. F/K": 40.0, "Ana Sektör": "Technology",
                  "Alt Sektör (İş Modeli)": "Donanım", "_az_hisseli": False}


class MetricTests(unittest.TestCase):
    def test_quarterly_ttm_profitability(self):
        q = vh.period_metrics(fake_fundamentals()["quarterly_income"], fake_fundamentals()["quarterly_balance"], 4)
        last = q.iloc[-1]                                     # son 4 çeyrek NI = 12+14+16+30 = 72
        self.assertAlmostEqual(last["Öz Sermaye Getirisi (ROE) %"], 72 / 200 * 100)
        self.assertAlmostEqual(last["Net Kar Marjı %"], 72 / 500 * 100)
        self.assertAlmostEqual(q.iloc[0]["Öz Sermaye Getirisi (ROE) %"], 10 * 4 / 200 * 100)   # 4 çeyrek yok: x4
        self.assertAlmostEqual(last["EPS Büyümesi %"], (3.0 / 1.0 - 1) * 100)                   # yıllık bazda çeyrek
        self.assertAlmostEqual(last[vh.EPS_TTM], 1.2 + 1.4 + 1.6 + 3.0)
        self.assertAlmostEqual(last["Likidite Oranı"], 1.2)
        self.assertAlmostEqual(last["Faiz Karşılama Oranı"], 14.0)

    def test_announce_dates_use_earnings_or_lag(self):
        got = vh.announce_dates(pd.to_datetime(["2026-03-31", "2026-09-30"]), EARNINGS, 45)
        self.assertEqual(list(got), [pd.Timestamp("2026-04-30"), pd.Timestamp("2026-11-14")])

    def test_anchors_use_annual_only_before_quarterly(self):
        anchors = vh.anchor_points(fake_fundamentals())
        q_start = pd.Timestamp("2025-07-31")
        self.assertEqual(anchors.index[anchors.index >= q_start].size, 5)
        self.assertTrue((anchors.index[anchors.index < q_start] < q_start).all())
        self.assertEqual(anchors.index[anchors.index < q_start].size, 3)   # 2025 yıllığı çeyreklerle çakışıyor

    def test_daily_profitability_is_step_and_others_interpolate(self):
        days = pd.bdate_range("2025-01-02", "2026-10-07")
        closes = pd.Series(np.linspace(150, 200, len(days)), index=days)
        m = vh.daily_metrics(closes, vh.anchor_points(fake_fundamentals()), CURRENT_RAW)
        roe = m["Öz Sermaye Getirisi (ROE) %"]
        # Son çeyrek (Haziran) 29 Temmuz'da açıklandı -> 30 Temmuz'dan itibaren geçerli, öncesi bir önceki çeyrek.
        self.assertAlmostEqual(roe.loc["2026-07-29"], (10 + 12 + 14 + 16) / 200 * 100)
        self.assertAlmostEqual(roe.loc["2026-07-30"], 72 / 200 * 100)
        self.assertAlmostEqual(roe.iloc[-1], 36.0)       # bugünkü info değeri (99) kullanılmaz
        # Borç/özsermaye: 30 Tem (1.0) -> bugün (1.0); 29 Nis (0.5) ile 30 Tem arası doğrusal artış.
        de = m["Borç / Özsermaye"]
        self.assertAlmostEqual(de.loc["2026-04-30"], 0.5)
        mid = de.loc["2026-06-15"]
        self.assertTrue(0.5 < mid < 1.0)
        self.assertAlmostEqual(de.iloc[-1], 1.0)
        # F/K bugün: info F/K'sı (30); PEG: ima edilen büyüme (30 / 1.5 = 20) sabit.
        self.assertAlmostEqual(m["F/K"].iloc[-1], 30.0)
        self.assertAlmostEqual(m["PEG"].iloc[-1], 1.5)
        self.assertTrue(np.allclose(m["PEG"].dropna(), (m["F/K"] / 20).dropna()))
        self.assertFalse(m[list(vh.PROFITABILITY) + ["F/K", "Cari Oran"]].isna().any().any())

    def test_score_days_uses_valuation_rules(self):
        import valuation

        days = pd.bdate_range("2026-09-01", "2026-10-07")
        m = vh.daily_metrics(pd.Series(180.0, index=days), vh.anchor_points(fake_fundamentals()), CURRENT_RAW)
        rows = vh.score_days("AAPL", m, CURRENT_SCORED)
        day, row = rows[-1]
        self.assertEqual(day, "2026-10-07")
        self.assertEqual(row["Alt Sektör İskontosu %"], round((40 - row["F/K"]) / 40 * 100, 1))
        self.assertEqual(row["Nihai Skor"], valuation.score_row(row))
        self.assertTrue(row["_reconstructed"])


class ReconstructTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"YATIRIM_DB_PATH": os.path.join(self.tmp.name, "t.db")})
        self.env.start()
        import valuation_db
        self.db = valuation_db
        self._service_row("2026-10-06T21:30:00Z", 77)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def _service_row(self, at, score):
        self.db.upsert_rows([{"market": vh.MARKET, "ticker": "AAPL", "raw": CURRENT_RAW,
                              "scored": {**CURRENT_SCORED, "Nihai Skor": score},
                              "fetched_at": at, "scored_at": at}])

    def test_writes_reconstructed_without_touching_service_rows(self):
        days = pd.bdate_range("2025-10-01", "2026-10-07")
        closes = pd.Series(180.0, index=days)
        summary = vh.reconstruct("AAPL", closes, fundamentals=fake_fundamentals())
        hist = {h["date"]: h for h in self.db.get_daily_history(vh.MARKET, "AAPL")}
        self.assertEqual(summary["days"], len(days))
        self.assertEqual(summary["written"], len(days) - 1)
        self.assertEqual(hist["2026-10-06"]["score"], 77)                         # servis satırı korundu
        self.assertEqual(hist["2026-10-05"]["source"], self.db.SOURCE_RECONSTRUCTED)
        self.assertIsNotNone(hist["2026-10-05"]["scored"]["Öz Sermaye Getirisi (ROE) %"])
        # Yeniden hesap aynı günleri günceller; servis o günü skorlarsa üzerine yazar.
        vh.reconstruct("AAPL", closes, fundamentals=fake_fundamentals())
        self._service_row("2026-10-07T21:30:00Z", 66)
        hist = {h["date"]: h for h in self.db.get_daily_history(vh.MARKET, "AAPL")}
        self.assertEqual((hist["2026-10-07"]["score"], hist["2026-10-07"]["source"]), (66, "service"))

    def test_requires_current_score(self):
        with self.assertRaises(KeyError):
            vh.reconstruct("MSFT", pd.Series([1.0], index=pd.bdate_range("2026-10-01", periods=1)),
                           fundamentals=fake_fundamentals())

    def test_ai_dataset_marks_reconstructed_days(self):
        import ai_dataset as ad
        from tests.test_ai_dataset import fake_market, fake_ohlcv

        df, meta = ad.create("AAPL", ohlcv_fetcher=fake_ohlcv(), market_loader=fake_market(), end="2026-10-07",
                             reconstruct_valuation=lambda t, c: vh.reconstruct(t, c, fundamentals=fake_fundamentals()))
        self.assertEqual(meta["valuation_reconstruction"]["written"], len(df) - 1)
        self.assertEqual(int(df["valuation_is_snapshot"].sum()), 0)
        self.assertEqual(int(df["valuation_is_reconstructed"].sum()), len(df) - 1)
        self.assertEqual(df.loc["2026-10-06", "valuation_score"], 77)
        self.assertEqual(df.loc["2026-10-06", "valuation_is_reconstructed"], 0)
        self.assertGreater(df["valuation_roe_pct"].nunique(), 3)          # çeyrekten çeyreğe değişiyor
        self.assertNotIn("valuation_peg", df.columns)                     # PEG veri setinde yok


if __name__ == "__main__":
    unittest.main()
