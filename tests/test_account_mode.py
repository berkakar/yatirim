import os
import tempfile
import unittest
from unittest import mock

import alpaca_account
import orb_scan_runner
import alpaca_keys
from alpaca_account import LIVE, PAPER, build_job_client, load_account_mode, save_account_mode, user_keys
from alpaca_client import DEFAULT_TRADING_URL, AlpacaClient, is_paper_url

PAPER_ENV = {"APCA_API_KEY_ID": "PKPAPER", "APCA_API_SECRET_KEY": "paper-secret"}
LIVE_ENV = {"APCA_LIVE_API_KEY_ID": "AKLIVE", "APCA_LIVE_API_SECRET_KEY": "live-secret"}


class AccountUrlTest(unittest.TestCase):
    def test_paper_and_live_urls(self):
        self.assertTrue(is_paper_url(alpaca_account.trading_url(PAPER)))
        self.assertFalse(is_paper_url(alpaca_account.trading_url(LIVE)))

    def test_client_defaults_to_paper(self):
        self.assertTrue(AlpacaClient("k", "s").is_paper)
        self.assertTrue(is_paper_url(DEFAULT_TRADING_URL))


class AccountModeSettingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = os.path.join(self.tmp.name, "test.db")
        patcher = mock.patch.dict(os.environ, {"YATIRIM_DB_PATH": db})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_default_is_paper(self):
        self.assertEqual(load_account_mode("berkakar"), PAPER)

    def test_save_and_load_live(self):
        save_account_mode("berkakar", LIVE)
        self.assertEqual(load_account_mode("berkakar"), LIVE)
        self.assertEqual(load_account_mode("baska"), PAPER)
        save_account_mode("berkakar", PAPER)
        self.assertEqual(load_account_mode("berkakar"), PAPER)

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            save_account_mode("berkakar", "demo")

    def test_live_keys_never_fall_back_to_paper_keys(self):
        with mock.patch.dict(os.environ, {alpaca_keys.ENV_KEY: alpaca_keys.generate_key()}):
            alpaca_keys.set_keys("berkakar", PAPER, "PK", "s")
            self.assertEqual(user_keys("berkakar", PAPER), ("PK", "s"))
            self.assertEqual(user_keys("berkakar", LIVE), (None, None))
            alpaca_keys.set_keys("berkakar", LIVE, "AK", "ls")
            self.assertEqual(user_keys("berkakar", LIVE), ("AK", "ls"))

    def test_job_client_prefers_db_keys_over_env(self):
        with mock.patch.dict(os.environ, {**PAPER_ENV, **LIVE_ENV, alpaca_keys.ENV_KEY: alpaca_keys.generate_key()}):
            alpaca_keys.set_keys("berkakar", PAPER, "PKDB", "db-secret")
            client = build_job_client("berkakar")
            self.assertEqual(client.headers["APCA-API-KEY-ID"], "PKDB")
            save_account_mode("berkakar", LIVE)
            # Gerçek Para anahtarı veritabanında yoksa ortam değişkenine düşülür, paper DB anahtarına değil.
            client = build_job_client("berkakar")
            self.assertFalse(client.is_paper)
            self.assertEqual(client.headers["APCA-API-KEY-ID"], "AKLIVE")

    def test_job_client_follows_setting(self):
        with mock.patch.dict(os.environ, {**PAPER_ENV, **LIVE_ENV}):
            client = build_job_client("berkakar")
            self.assertTrue(client.is_paper)
            self.assertEqual(client.headers["APCA-API-KEY-ID"], "PKPAPER")

            save_account_mode("berkakar", LIVE)
            client = orb_scan_runner.build_client()
            self.assertFalse(client.is_paper)
            self.assertEqual(client.trading_url, "https://api.alpaca.markets/v2")
            self.assertEqual(client.headers["APCA-API-KEY-ID"], "AKLIVE")

    def test_live_without_live_keys_refuses(self):
        save_account_mode("berkakar", LIVE)
        env = {k: v for k, v in os.environ.items() if not k.startswith("APCA_LIVE_")}
        with mock.patch.dict(os.environ, {**env, **PAPER_ENV}, clear=True):
            with self.assertRaises(RuntimeError):
                build_job_client("berkakar")


if __name__ == "__main__":
    unittest.main()
