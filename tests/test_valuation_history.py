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
        # F/K bugün: info F/K'sı (30); PEG hesaplanmaz.
        self.assertAlmostEqual(m["F/K"].iloc[-1], 30.0)
        self.assertNotIn("PEG", m.columns)
        self.assertFalse(m[list(vh.PROFITABILITY) + ["F/K", "Cari Oran"]].isna().any().any())

    def test_score_days_uses_valuation_rules(self):
        import valuation_rules

        days = pd.bdate_range("2026-09-01", "2026-10-07")
        m = vh.daily_metrics(pd.Series(180.0, index=days), vh.anchor_points(fake_fundamentals()), CURRENT_RAW)
        rows = vh.score_days("AAPL", m, CURRENT_SCORED)
        day, row = rows[-1]
        self.assertEqual(day, "2026-10-07")
        self.assertEqual(row["Alt Sektör İskontosu %"], round((40 - row["F/K"]) / 40 * 100, 1))
        self.assertEqual(row["Nihai Skor"], valuation_rules.score_row(row))
        self.assertNotIn("PEG", row)
        self.assertTrue(row["_reconstructed"])


class ReconstructTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"YATIRIM_DB_PATH": os.path.join(self.tmp.name, "t.db")})
        self.env.start()
        import valuation_db
        import ai_dataset
        self.db = valuation_db
        earnings = mock.patch.object(ai_dataset, "default_earnings_fetcher", lambda t: [])
        earnings.start()
        self.addCleanup(earnings.stop)
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

    def _peer_rows(self, peers):
        for t, pe in peers.items():
            self.db.upsert_rows([{"market": vh.MARKET, "ticker": t, "raw": {"F/K": pe},
                                  "scored": {**CURRENT_SCORED, "Hisse": t, "F/K": pe},
                                  "fetched_at": "2026-10-06T21:30:00Z", "scored_at": "2026-10-06T21:30:00Z"}])

    def test_sub_sector_median_uses_peer_history(self):
        self._peer_rows({"P1": 20.0, "P2": 50.0, "P3": 35.0})
        self.db.upsert_rows([{"market": vh.MARKET, "ticker": "OTHER", "raw": {"F/K": 1.0},
                              "scored": {**CURRENT_SCORED, "Hisse": "OTHER", "Alt Sektör (İş Modeli)": "Başka"},
                              "fetched_at": "2026-10-06T21:30:00Z", "scored_at": "2026-10-06T21:30:00Z"}])
        days = pd.bdate_range("2026-01-02", "2026-10-07")
        closes = pd.Series(np.linspace(150, 180, len(days)), index=days)
        peer_closes = pd.DataFrame({"P1": np.linspace(80, 100, len(days)), "P2": 200.0}, index=days)

        def fetcher(t):
            if t == "P3":
                raise RuntimeError("Yahoo yok")
            return fake_fundamentals()
        asked = []
        summary = vh.reconstruct("AAPL", closes, fundamentals=fake_fundamentals(), fetcher=fetcher,
                                 price_fetcher=lambda tickers, start: asked.append(sorted(tickers)) or peer_closes)
        self.assertEqual(asked, [["P1", "P2", "P3"]])                      # OTHER başka alt sektör
        self.assertEqual(summary["peers_failed"], ["P3"])
        self.assertFalse(summary["small_sector"])                           # 4 hisse > 3
        self.assertIn("P1", summary["written_peers"])
        self.assertNotIn("P3", summary["written_peers"])

        hist = {h["date"]: h["scored"] for h in self.db.get_daily_history(vh.MARKET, "AAPL")}
        p1 = {h["date"]: h["scored"] for h in self.db.get_daily_history(vh.MARKET, "P1")}
        p2 = {h["date"]: h["scored"] for h in self.db.get_daily_history(vh.MARKET, "P2")}
        for day in ("2026-03-02", "2026-08-03"):
            pes = [hist[day]["F/K"], p1[day]["F/K"], p2[day]["F/K"], 35.0]   # P3: bugünkü F/K sabit
            med = float(np.median(pes))
            self.assertAlmostEqual(hist[day]["Alt Sektör Ort. F/K"], med, places=1)
            self.assertAlmostEqual(hist[day]["Alt Sektör İskontosu %"], (med - hist[day]["F/K"]) / med * 100, places=0)
            self.assertEqual(hist[day]["Alt Sektör Ort. F/K"], p1[day]["Alt Sektör Ort. F/K"])
        self.assertNotEqual(hist["2026-03-02"]["Alt Sektör Ort. F/K"], hist["2026-08-03"]["Alt Sektör Ort. F/K"])

    def test_small_sub_sector_discount_is_one(self):
        self._peer_rows({"P1": 20.0})
        days = pd.bdate_range("2026-09-01", "2026-10-07")
        summary = vh.reconstruct("AAPL", pd.Series(180.0, index=days), fundamentals=fake_fundamentals(),
                                 fetcher=lambda t: fake_fundamentals(),
                                 price_fetcher=lambda tickers, start: pd.DataFrame({"P1": 90.0}, index=days))
        self.assertTrue(summary["small_sector"])
        day = self.db.get_daily_history(vh.MARKET, "AAPL")[0]["scored"]
        self.assertEqual(day["Alt Sektör İskontosu %"], 1.0)


class MixedDatetimeUnitTests(ReconstructTests):
    """Sunucu hatası (2026-10-08): 'incompatible merge keys [0] dtype('<M8[s]') and
    dtype('<M8[us]')' - Yahoo / arşiv / veritabanı farklı çözünürlükte tarih üretince."""

    test_writes_reconstructed_without_touching_service_rows = None
    test_requires_current_score = None
    test_ai_dataset_marks_reconstructed_days = None
    test_sub_sector_median_uses_peer_history = None
    test_small_sub_sector_discount_is_one = None

    def test_create_with_mixed_units(self):
        import ai_dataset as ad
        from tests.test_ai_dataset import fake_market, fake_ohlcv

        self._peer_rows({"P1": 20.0, "P2": 50.0, "P3": 35.0})

        def ohlcv(ticker, start, end=None):
            df = fake_ohlcv()(ticker, start)
            df.index = pd.DatetimeIndex(df.index).as_unit("s")
            return df

        def market(start, end):
            df = fake_market()(start, end)
            df.index = pd.DatetimeIndex(df.index).as_unit("us")
            return df

        def peer_prices(tickers, start):
            days = pd.DatetimeIndex(pd.bdate_range(start, "2026-10-07")).as_unit("ms")
            return pd.DataFrame({t: 100.0 for t in tickers}, index=days)

        def fundamentals(_t=None):
            f = fake_fundamentals()
            for k in ("quarterly_income", "quarterly_balance", "annual_income", "annual_balance"):
                f[k].columns = pd.DatetimeIndex(f[k].columns).as_unit("s")
            f["earnings_dates"] = list(pd.DatetimeIndex(EARNINGS).tz_localize("America/New_York").as_unit("us"))
            return f

        df, meta = ad.create(
            "AAPL", ohlcv_fetcher=ohlcv, market_loader=market, end="2026-10-07",
            vwap_fetcher=lambda t, start: pd.Series(
                101.0, index=pd.DatetimeIndex(pd.bdate_range("2026-01-02", "2026-10-07")).as_unit("ms")),
            reconstruct_valuation=lambda t, c: vh.reconstruct(t, c, fetcher=fundamentals,
                                                               price_fetcher=peer_prices))
        self.assertEqual(df.index.dtype, np.dtype("datetime64[ns]"))
        self.assertFalse(any("hesaplanamadı" in w for w in meta["warnings"]), meta["warnings"])
        self.assertEqual(int(df["valuation_is_snapshot"].sum()), 0)
        self.assertEqual(df.loc["2026-10-06", "valuation_score"], 77)
        self.assertEqual(df.loc["2026-10-07", "vwap"], 101.0)
        self.assertEqual(df.loc["2026-10-07", "vwap_is_proxy"], 0)
        self.assertFalse(df["nasdaq_100__momentum"].isna().any())
        self.assertGreater(len(meta["valuation_reconstruction"]["written_peers"]), 0)


if __name__ == "__main__":
    unittest.main()
