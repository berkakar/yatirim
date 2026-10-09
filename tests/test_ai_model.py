"""Yapay zeka modeli (ai_model.py): veri kontrolü (boş veri, IQR), kronolojik
bölme ve pencereler, RevIN, factorized attention, eğitim / kayıt - küçük ayarlarla."""

import os
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

import ai_dataset as ad
import ai_model as am

try:
    import torch
except ImportError:  # sunucuda PyTorch kurulu olmayabilir
    torch = None

SMALL = {"lookback": 16, "horizon": 3, "block_size": 4, "d_model": 16, "n_heads": 2, "n_layers": 1,
         "d_ff": 32, "epochs": 3, "patience": 3, "batch_size": 16, "channels_per_batch": 3}


def frame(n=200, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-01", periods=n)
    close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, n)))
    df = pd.DataFrame({
        "close": close,
        "ret_1d_pct": np.r_[np.nan, np.diff(close) / close[:-1] * 100],
        "rsi14": rng.uniform(20, 80, n),
        "flag": rng.integers(0, 2, n).astype(float),
        "volume_rel20": rng.lognormal(0, 0.3, n),
    }, index=idx)
    return df.join(ad.temporal_features(idx))


class PrepareTests(unittest.TestCase):
    def test_nan_filled_and_calendar_separated(self):
        df = frame()
        df.iloc[[5, 6, 50], df.columns.get_loc("rsi14")] = np.nan
        prep = am.prepare_data(df)
        self.assertFalse(np.isnan(prep["values"]).any())
        self.assertEqual(prep["report"]["nan_filled"]["rsi14"], 3)
        self.assertEqual(prep["report"]["nan_filled"]["ret_1d_pct"], 1)        # ilk gün, geri taşıma
        self.assertEqual(prep["calendar"].shape[1], len(am.CALENDAR_COLS))
        for c in am.CALENDAR_COLS + ("time_idx", "dow_sin"):
            self.assertNotIn(c, prep["channels"])
        self.assertEqual(prep["n_train"], 160)                                    # %80

    def test_iqr_uses_train_bounds_and_skips_close_and_flags(self):
        df = frame()
        df.iloc[40, df.columns.get_loc("ret_1d_pct")] = 500.0                    # eğitimde şok
        df.iloc[190, df.columns.get_loc("ret_1d_pct")] = -400.0                  # testte şok
        df.iloc[180:, df.columns.get_loc("close")] *= 5                           # testte trend - kırpılmaz
        prep = am.prepare_data(df, iqr_k=3.0)
        clipped = prep["report"]["clipped"]
        self.assertEqual(clipped["ret_1d_pct"], {"train": 1, "test": 1})
        self.assertNotIn("close", clipped)
        self.assertNotIn("flag", prep["bounds"])
        lo, hi = prep["bounds"]["ret_1d_pct"]
        raw = prep["raw"][:, prep["channels"].index("ret_1d_pct")]
        self.assertAlmostEqual(raw[40], hi)
        self.assertAlmostEqual(raw[190], lo)
        train = df["ret_1d_pct"].bfill().iloc[:160]                              # ilk gün doldurulmuş
        q1, q3 = train.quantile([0.25, 0.75])
        self.assertAlmostEqual(hi, q3 + 3 * (q3 - q1), places=6)                  # yalnızca eğitim dönemi

    def test_valuation_and_bounded_indicators_not_clipped(self):
        df = frame()
        df["valuation_net_margin_pct"] = np.r_[np.full(160, 20.0) + np.random.default_rng(1).normal(0, 0.1, 160),
                                               np.full(40, 45.0)]          # yeni çeyrekte gerçek seviye değişimi
        df.iloc[50, df.columns.get_loc("rsi14")] = 99.0
        prep = am.prepare_data(df)
        self.assertNotIn("valuation_net_margin_pct", prep["report"]["clipped"])
        self.assertNotIn("rsi14", prep["bounds"])
        self.assertTrue(am.is_clippable("ret_1d_pct"))
        self.assertFalse(am.is_clippable("nasdaq_100__momentum"))

    def test_standardized_with_train_stats(self):
        prep = am.prepare_data(frame())
        tr = prep["values"][:prep["n_train"]]
        self.assertTrue(np.allclose(tr.mean(axis=0), 0, atol=1e-4))
        back = prep["values"] * prep["std"] + prep["mean"]
        self.assertTrue(np.allclose(back, prep["raw"], atol=1e-3))

    def test_windows_are_chronological_without_target_leak(self):
        w = am.make_windows(200, 160, 16, 3, 0.1)
        self.assertTrue(all(t + 3 <= 160 for t in w["train"] + w["val"]))
        self.assertLess(max(w["train"]), min(w["val"]))
        self.assertEqual(min(w["test"]), 160)
        self.assertEqual(max(w["test"]), 197)


@unittest.skipIf(torch is None, "PyTorch kurulu değil")
class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"YATIRIM_DB_PATH": os.path.join(self.tmp.name, "m.db")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_revin_is_reversible_and_output_shape(self):
        cfg = {**am.DEFAULTS, **SMALL}
        model = am.build_model(4, cfg)
        x = torch.randn(2, 16, 4) * 10 + 50
        mean, std = model.revin.stats(x)
        self.assertTrue(torch.allclose(model.revin.denorm(model.revin.norm(x, mean, std), mean, std), x, atol=1e-3))
        pred, _, _ = model(x, torch.zeros(2, 16, 5, dtype=torch.long))
        self.assertEqual(tuple(pred.shape), (2, 3, 4))
        sub, _, _ = model(x[..., [0, 2]], torch.zeros(2, 16, 5, dtype=torch.long), torch.tensor([0, 2]))
        self.assertEqual(tuple(sub.shape), (2, 3, 2))

    def test_channel_independent(self):
        """Bir kanalın tahmini diğer kanallardan etkilenmez."""
        model = am.build_model(3, {**am.DEFAULTS, **SMALL})
        model.eval()
        cal = torch.zeros(1, 16, 5, dtype=torch.long)
        x = torch.randn(1, 16, 3)
        y = x.clone()
        y[..., 1:] = torch.randn(1, 16, 2) * 100
        with torch.no_grad():
            self.assertTrue(torch.allclose(model(x, cal)[0][..., 0], model(y, cal)[0][..., 0], atol=1e-5))

    def test_lookback_must_be_multiple_of_block(self):
        with self.assertRaises(ValueError):
            am.train(frame(), {**SMALL, "lookback": 18})

    def test_train_evaluate_save_load(self):
        epochs = []
        res = am.train(frame(), SMALL, progress=lambda e, n, tr, va: epochs.append(e))
        self.assertEqual(epochs[0], 1)
        m = res["metrics"]
        self.assertEqual(len(m["close"]), 3)
        self.assertEqual(m["test_windows"], 38)
        self.assertTrue(np.isfinite(m["norm_mse"]))
        self.assertGreater(m["close"][0]["mae"], 0)
        self.assertEqual(len(res["forecast"]["close"]), 3)
        self.assertGreater(min(res["forecast"]["close"]), 0)                       # dolar ölçeğinde
        self.assertEqual(len(res["test_predictions"]["date"]), 38)
        run_id = am.save_run("TEST", res)
        runs = am.list_runs("TEST")
        self.assertEqual(runs[0]["id"], run_id)
        self.assertEqual(runs[0]["metrics"], json_roundtrip(m))
        model, channels, cfg = am.load_model(run_id)
        self.assertEqual(channels, res["channels"])
        am.delete_run(run_id)
        self.assertEqual(am.list_runs("TEST"), [])


def json_roundtrip(obj):
    import json
    return json.loads(json.dumps(obj))


if __name__ == "__main__":
    unittest.main()
