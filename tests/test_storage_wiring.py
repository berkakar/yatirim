"""Arayüzün yazdığı ayarların storage.py'ye bağlanması: SQLite açıkken
(YATIRIM_DB_PATH tanımlı) arayüz ve bu ayarları okuyan işler aynı kaydı
kullanmalı, GitHub'a ve repo içindeki JSON dosyalarına hiç dokunmamalı;
kapalıyken eski davranış aynen sürmeli."""

import json
import os
import tempfile
import unittest
from unittest import mock

import storage


def _github_must_not_be_called(*args, **kwargs):
    raise AssertionError("SQLite açıkken GitHub'a istek atılmamalı")


class WiringTestCase(unittest.TestCase):
    ENABLED = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = os.path.join(self._tmp.name, "repo")
        os.makedirs(self.workdir)
        old_cwd = os.getcwd()
        os.chdir(self.workdir)  # eski düzenin yerel JSON dosyaları buraya yazılır
        self.addCleanup(os.chdir, old_cwd)

        env = {"YATIRIM_DB_PATH": os.path.join(self._tmp.name, "db", "yatirim.db")} if self.ENABLED else {}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        if not self.ENABLED:
            os.environ.pop("YATIRIM_DB_PATH", None)

        for target in (
            "config.read_json_from_github", "config.write_json_to_github",
            "stop_loss_settings.read_json_from_github", "stop_loss_settings.write_json_to_github",
            "bildirim_data.read_json_from_github", "bildirim_data.write_json_to_github",
            "turk_fonlari_takip_data.read_json_from_github", "turk_fonlari_takip_data.write_json_to_github",
            "valuation.read_json_from_github", "valuation.update_json_on_github",
        ):
            p = mock.patch(target, side_effect=_github_must_not_be_called)
            p.start()
            self.addCleanup(p.stop)

    def assertNoJsonFiles(self):
        self.assertEqual([f for f in os.listdir(self.workdir) if f.endswith(".json")], [])


class ConfigWiringTest(WiringTestCase):
    def test_ticker_lists(self):
        import config
        lists = config.load_ticker_lists("umuts")
        self.assertIn("NASDAQ 100", lists)  # kayıt yokken varsayılanlar
        lists["NASDAQ 100"] = ["AAPL", "AAPL", "MSFT"]
        config.save_ticker_lists(lists, "umuts")
        self.assertEqual(storage.read("custom_tickers", "umuts")["NASDAQ 100"], ["AAPL", "AAPL", "MSFT"])
        self.assertEqual(config.load_ticker_lists("umuts")["NASDAQ 100"], ["AAPL", "MSFT"])
        self.assertNoJsonFiles()

    def test_stock_groups_and_markets(self):
        import config
        self.assertEqual(config.load_stock_groups("berkakar"), {})
        config.save_stock_groups({"Çip": ["NVDA", "AMD"]}, "berkakar")
        config.save_group_markets({"Çip": "NASDAQ 100"}, "berkakar")
        self.assertEqual(config.load_stock_groups("berkakar"), {"Çip": ["NVDA", "AMD"]})
        self.assertEqual(config.load_group_markets("berkakar"), {"Çip": "NASDAQ 100"})
        self.assertEqual(config.load_stock_groups("umuts"), {})
        self.assertNoJsonFiles()

    def test_initial_capital(self):
        import config
        self.assertIsNone(config.load_initial_capital("berkakar"))
        config.save_initial_capital(10000, "berkakar")
        self.assertEqual(config.load_initial_capital("berkakar"), 10000)
        self.assertEqual(storage.read("initial_capital", "berkakar"), {"initial_capital": 10000})
        self.assertNoJsonFiles()


class StopLossWiringTest(WiringTestCase):
    def test_ui_save_is_seen_by_all_trading_jobs(self):
        import alpaca_trailing_stop
        import heikin_ashi_intraday_runner
        import orb_scan_runner
        import relative_strength_runner
        import stop_loss_settings

        self.assertEqual(stop_loss_settings.load_stop_loss_settings("berkakar"), {})
        settings = {"shared": {"atr_period": 10}, "execution": {"opening_shield_enabled": False}}
        stop_loss_settings.save_stop_loss_settings("berkakar", settings)

        self.assertEqual(stop_loss_settings.load_stop_loss_settings("berkakar"), settings)
        self.assertEqual(alpaca_trailing_stop.load_stop_loss_settings(), settings)
        for runner in (orb_scan_runner, relative_strength_runner, heikin_ashi_intraday_runner):
            with mock.patch.object(runner, "USERNAME", "berkakar"):
                self.assertEqual(runner.load_local_stop_settings(), settings, runner.__name__)
        self.assertNoJsonFiles()


class NotificationWiringTest(WiringTestCase):
    def test_settings_roundtrip_and_trailing_stop_chat_id(self):
        import alpaca_trailing_stop
        import bildirim_data

        defaults = bildirim_data.load_notification_settings("berkakar")
        self.assertEqual(defaults["telegram_chat_id"], "")
        bildirim_data.save_notification_settings({"telegram_chat_id": " 123 ", "loss_threshold_pct": -5.0}, "berkakar")
        self.assertEqual(bildirim_data.load_notification_settings("berkakar")["loss_threshold_pct"], -5.0)
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "bot"}):
            self.assertEqual(alpaca_trailing_stop.load_telegram_settings(), ("bot", "123"))
        self.assertNoJsonFiles()


class FundsWiringTest(WiringTestCase):
    def test_tracked_funds_and_kap_cache_are_seen_by_jobs(self):
        import fon_hisse_uyari
        import kap_refresh_holdings
        import turk_fonlari_takip_data as data

        self.assertEqual(data.load_tracked_funds("umuts"), [])
        data.save_tracked_funds([{"code": "THF", "name": "TERA"}], "umuts")
        data.save_tracked_funds([{"code": "AFT", "name": "AK"}], "berkakar")
        data.save_portfolio_cache({"THF": {"fund_name": "TERA", "reports": []}})

        self.assertEqual(data.load_tracked_funds("umuts"), [{"code": "THF", "name": "TERA"}])
        self.assertEqual(fon_hisse_uyari._discover_users(), ["berkakar", "umuts"])
        self.assertEqual(fon_hisse_uyari._load_setting("takip_fonlari", "umuts", []), [{"code": "THF", "name": "TERA"}])
        self.assertIn("THF", fon_hisse_uyari._load_setting("kap_portfoy_cache", None, {}))
        self.assertEqual(kap_refresh_holdings._discover_funds(), {"THF": "TERA", "AFT": "AK"})
        self.assertNoJsonFiles()

    def test_kap_refresh_writes_cache_to_storage(self):
        import kap_refresh_holdings

        storage.write("takip_fonlari", "umuts", [{"code": "THF", "name": "TERA"}])
        report = {"report_date": "01.09.2026", "report_date_sort": "2026-09-01", "period_label": "Ağustos",
                  "disclosure_index": 1, "holdings": [("THYAO", 9.5)]}
        with mock.patch.object(kap_refresh_holdings, "get_latest_top_holdings", return_value=report), \
                mock.patch.object(kap_refresh_holdings, "log"):
            kap_refresh_holdings.run_once()
        cache = storage.read("kap_portfoy_cache")
        self.assertEqual(cache["THF"]["reports"][0]["holdings"], [["THYAO", 9.5]])
        self.assertNoJsonFiles()


class ValuationWiringTest(WiringTestCase):
    def test_updates_merge_into_shared_cache(self):
        import valuation

        self.assertEqual(valuation._load_valuation_cache(), {})
        valuation._save_valuation_cache_updates({"AAPL": {"pe": 30}})
        valuation._save_valuation_cache_updates({"MSFT": {"pe": 35}})
        self.assertEqual(valuation._load_valuation_cache(), {"AAPL": {"pe": 30}, "MSFT": {"pe": 35}})
        self.assertNoJsonFiles()


class DisabledKeepsLegacyBehaviourTest(WiringTestCase):
    """YATIRIM_DB_PATH yokken (GITHUB_TOKEN da yok) eski düzen: yerel JSON dosyaları."""
    ENABLED = False

    def test_config_uses_local_json_files(self):
        import config
        config.save_stock_groups({"Çip": ["NVDA"]}, "umuts")
        with open("custom_stock_groups_umuts.json", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"Çip": ["NVDA"]})
        self.assertEqual(config.load_stock_groups("umuts"), {"Çip": ["NVDA"]})
        config.save_initial_capital(500, "umuts")
        self.assertEqual(config.load_initial_capital("umuts"), 500)

    def test_trading_jobs_read_local_files(self):
        import alpaca_trailing_stop
        with open("stop_loss_settings_berkakar.json", "w", encoding="utf-8") as f:
            json.dump({"shared": {"x": 1}}, f)
        self.assertEqual(alpaca_trailing_stop.load_stop_loss_settings(), {"shared": {"x": 1}})

    def test_fund_jobs_read_local_files(self):
        import fon_hisse_uyari
        with open("takip_fonlari_umuts.json", "w", encoding="utf-8") as f:
            json.dump([{"code": "THF", "name": "TERA"}], f)
        self.assertEqual(fon_hisse_uyari._discover_users(), ["umuts"])
        self.assertEqual(fon_hisse_uyari._load_setting("takip_fonlari", "umuts", []), [{"code": "THF", "name": "TERA"}])

    def test_no_database_is_created(self):
        import config
        config.save_stock_groups({"A": ["X"]}, "umuts")
        self.assertFalse(os.path.exists(os.path.join(self._tmp.name, "db")))
        self.assertFalse(storage.enabled())


if __name__ == "__main__":
    unittest.main()
