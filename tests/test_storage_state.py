"""Strateji state/config dosyalarının ve önbelleklerin storage.py'ye bağlanması:
SQLite açıkken (YATIRIM_DB_PATH tanımlı) işler ve arayüz aynı kaydı kullanmalı ve
diske JSON yazmamalı; kapalıyken dosyalar eskisi gibi okunup yazılmalı."""

import glob
import json
import os
import tempfile
import unittest
from unittest import mock

import storage
from scripts import migrate_json_to_sqlite as migration

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Base(unittest.TestCase):
    ENABLED = True

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = os.path.join(self._tmp.name, "repo")
        os.makedirs(self.workdir)
        old_cwd = os.getcwd()
        os.chdir(self.workdir)
        self.addCleanup(os.chdir, old_cwd)
        patcher = mock.patch.dict(os.environ, {"YATIRIM_DB_PATH": os.path.join(self._tmp.name, "db", "y.db")})
        patcher.start()
        self.addCleanup(patcher.stop)
        if not self.ENABLED:
            os.environ.pop("YATIRIM_DB_PATH")

    def json_files(self):
        return sorted(f for f in os.listdir(self.workdir) if f.endswith(".json"))


class KeyForPathTest(unittest.TestCase):
    def test_mapping(self):
        cases = {
            "orb_scan_holdings_berkakar.json": ("berkakar", "orb_scan_holdings"),
            "/opt/yatirim/work/x/orb_scan_config_umuts.json": ("umuts", "orb_scan_config"),
            "custom_stock_group_markets_berkakar.json": ("berkakar", "custom_stock_group_markets"),
            "custom_stock_groups_berkakar.json": ("berkakar", "custom_stock_groups"),
            "alpaca_intraday_bars_cache_berkakar.json": ("berkakar", "alpaca_intraday_bars_cache"),
            "tefas_fonlari_cache.json": (storage.SHARED, "tefas_fonlari_cache"),
            "bildirim_durumu.json": (storage.SHARED, "bildirim_durumu"),
            "version_info.json": None,
            "holdings_debug_dump.json": None,
            "orb_scan_holdings_.json": None,
            "orb_scan_holdings_berkakar.txt": None,
        }
        for path, expected in cases.items():
            self.assertEqual(storage.key_for_path(path), expected, path)

    def test_every_repo_json_maps_like_the_migration_script(self):
        """Taşıma betiğinin her dosyayı yazdığı kayıt, kodun o dosyayı okuduğu kayıtla aynı olmalı."""
        users = ["berkakar", "umuts", "utkusenses", "baranesirgen"]
        files = [p for p in glob.glob(os.path.join(REPO_DIR, "*.json"))
                 if os.path.basename(p) not in migration.SKIP_FILES]
        self.assertTrue(files)
        for path in files:
            self.assertEqual(storage.key_for_path(path), migration.parse_filename(path, users), path)


class JsonHelpersEnabledTest(_Base):
    def test_roundtrip_without_files(self):
        self.assertEqual(storage.load_json("orb_scan_holdings_berkakar.json", {}), {})
        self.assertFalse(storage.json_exists("orb_scan_holdings_berkakar.json"))
        storage.save_json("orb_scan_holdings_berkakar.json", {"AAPL": {"qty": 1}})
        self.assertTrue(storage.json_exists("orb_scan_holdings_berkakar.json"))
        self.assertEqual(storage.read("orb_scan_holdings", "berkakar"), {"AAPL": {"qty": 1}})
        self.assertEqual(self.json_files(), [])

    def test_unknown_file_still_uses_disk(self):
        storage.save_json("version_info.json", {"v": 1})
        self.assertEqual(self.json_files(), ["version_info.json"])
        self.assertEqual(storage.load_json("version_info.json"), {"v": 1})

    def test_update_json(self):
        storage.save_json("backtest_results_berkakar.json", [1])
        storage.update_json("backtest_results_berkakar.json", lambda r: r + [2], [])
        self.assertEqual(storage.load_json("backtest_results_berkakar.json", []), [1, 2])


class JsonHelpersDisabledTest(_Base):
    ENABLED = False

    def test_file_roundtrip_keeps_repo_format(self):
        self.assertEqual(storage.load_json("orb_scan_holdings_berkakar.json", {"x": []}), {"x": []})
        storage.save_json("orb_scan_holdings_berkakar.json", {"ŞİŞE": 1})
        with open("orb_scan_holdings_berkakar.json", encoding="utf-8") as f:
            self.assertEqual(f.read(), '{\n  "ŞİŞE": 1\n}')
        storage.update_json("orb_scan_holdings_berkakar.json", lambda h: {**h, "A": 2}, {})
        self.assertEqual(storage.load_json("orb_scan_holdings_berkakar.json"), {"ŞİŞE": 1, "A": 2})
        self.assertFalse(os.path.exists(os.path.join(self._tmp.name, "db")))

    def test_corrupt_file_still_raises(self):
        with open("buy_stop_rebuy_state_berkakar.json", "w") as f:
            f.write("{bozuk")
        with self.assertRaises(ValueError):
            storage.load_json("buy_stop_rebuy_state_berkakar.json", {})


class GithubRedirectTest(_Base):
    def test_known_paths_use_storage_and_unknown_go_to_github(self):
        import github_config

        with mock.patch.object(github_config.requests, "get") as get, \
                mock.patch.object(github_config.requests, "put") as put:
            github_config.write_portfolio_config("r", "t", {"budget": 5}, "berkakar")
            self.assertEqual(github_config.read_portfolio_config("r", "t", "berkakar"), {"budget": 5})
            self.assertEqual(github_config.read_json_from_github("r", "t", "orb_scan_config_umuts.json",
                                                                 {"enabled": False}), {"enabled": False})
            merged = github_config.update_json_on_github(
                "r", "t", "valuation_cache.json", {}, lambda c: {**c, "AAPL": 1}, "m")
            self.assertEqual(merged, {"AAPL": 1})
            get.assert_not_called()
            put.assert_not_called()

            get.return_value.status_code = 404
            self.assertEqual(github_config.read_json_from_github("r", "t", "version_info.json", {}), {})
            get.assert_called_once()
        self.assertEqual(self.json_files(), [])


class StrategyStateTest(_Base):
    def test_holdings_and_configs_of_all_modules(self):
        import heikin_ashi_intraday_core as ha
        import orb_core
        import relative_strength_core as rs

        for core in (orb_core, rs, ha):
            core.save_holdings_local("berkakar", {"AAPL": {"qty": 1}})
            self.assertEqual(core.load_holdings_local("berkakar"), {"AAPL": {"qty": 1}}, core.__name__)
            # Arayüzün GitHub API üzerinden yazdığı config'i iş aynı kayıttan okur
            import github_config
            github_config.write_json_to_github("r", "t", core.config_path("berkakar"), {"enabled": True}, "m")
            self.assertTrue(core.load_config_local("berkakar")["enabled"], core.__name__)
        self.assertEqual(self.json_files(), [])

    def test_runner_configs(self):
        import heikin_ashi_intraday_runner
        import orb_scan_runner
        import otomatik_alim_satim_runner
        import relative_strength_runner

        for runner in (orb_scan_runner, relative_strength_runner, heikin_ashi_intraday_runner,
                       otomatik_alim_satim_runner):
            self.assertEqual(runner.load_local_config(), {"enabled": False}, runner.__name__)
            runner.save_local_config({"enabled": True, "last_run_at": "x"})
            self.assertEqual(runner.load_local_config()["last_run_at"], "x", runner.__name__)
        self.assertEqual(self.json_files(), [])

    def test_backtest_results_from_job_and_ui_accumulate(self):
        import backtest_data
        import otomatik_alim_satim_runner

        otomatik_alim_satim_runner.append_backtest_results([{"run": 1}])
        backtest_data.append_results("berkakar", [{"run": 2}])
        otomatik_alim_satim_runner.append_backtest_results([{"run": 3}])
        self.assertEqual(backtest_data.load_results("berkakar"), [{"run": 1}, {"run": 2}, {"run": 3}])
        self.assertEqual(self.json_files(), [])

    def test_buy_stop_rebuy_state(self):
        import buy_stop_rebuy

        self.assertEqual(buy_stop_rebuy.load_state(), {"tracked": {}, "pending": {}})
        buy_stop_rebuy.save_state({"tracked": {"AAPL": 1}})
        self.assertEqual(buy_stop_rebuy.load_state(), {"tracked": {"AAPL": 1}, "pending": {}})
        self.assertEqual(self.json_files(), [])

    def test_caches(self):
        import alpaca_bars_cache
        import alpaca_realized_pnl_cache
        import alpaca_trailing_stop
        import tefas_fonlari_data

        alpaca_bars_cache._save(alpaca_bars_cache.DAILY_BARS_CACHE_PATH, {"series": {"A": []}})
        self.assertEqual(alpaca_bars_cache._load(alpaca_bars_cache.DAILY_BARS_CACHE_PATH), {"series": {"A": []}})
        self.assertEqual(alpaca_bars_cache._load(alpaca_bars_cache.INTRADAY_BARS_CACHE_PATH), {"series": {}})
        alpaca_realized_pnl_cache._save({"symbols": {"A": 1}})
        self.assertEqual(alpaca_realized_pnl_cache._load(), {"symbols": {"A": 1}})
        alpaca_trailing_stop._save_management_start_cache({"A": "t"})
        self.assertEqual(alpaca_trailing_stop._load_management_start_cache(), {"A": "t"})
        self.assertEqual(tefas_fonlari_data.load_cache(), {"meta": {}, "funds": {}, "table": []})
        tefas_fonlari_data.save_cache({"meta": {"x": 1}, "funds": {}, "table": []})
        self.assertEqual(tefas_fonlari_data.load_cache()["meta"], {"x": 1})
        self.assertEqual(self.json_files(), [])


class PruneHoldingsTest(_Base):
    def test_closed_positions_are_pruned_atomically(self):
        import alpaca_trailing_stop
        import orb_core

        orb_core.save_holdings_local("berkakar", {"AAPL": {"q": 1}, "TREX": {"q": 2}})
        calls = []
        result = alpaca_trailing_stop._prune_holdings(
            orb_core.holdings_path("berkakar"), orb_core.load_holdings_local,
            lambda *a: calls.append(a), {"AAPL"},
        )
        self.assertEqual(result, {"AAPL": {"q": 1}})
        self.assertEqual(orb_core.load_holdings_local("berkakar"), {"AAPL": {"q": 1}})
        self.assertEqual(calls, [], "SQLite açıkken kaydetme storage.update ile yapılmalı")

    def test_prune_uses_value_current_at_write_time(self):
        """Temizlik başladıktan sonra başka bir işin eklediği alım ezilmemeli."""
        import alpaca_trailing_stop
        import orb_core

        path = orb_core.holdings_path("berkakar")
        orb_core.save_holdings_local("berkakar", {"TREX": {"q": 2}})
        stale = orb_core.load_holdings_local("berkakar")  # eski düzende temizlik bu kopyayı yazardı
        orb_core.save_holdings_local("berkakar", {"TREX": {"q": 2}, "NEW": {"q": 1}})  # ORB yeni alım yaptı
        alpaca_trailing_stop._prune_holdings(path, lambda u: stale, orb_core.save_holdings_local, {"NEW"})
        self.assertEqual(orb_core.load_holdings_local("berkakar"), {"NEW": {"q": 1}})


class PruneHoldingsDisabledTest(_Base):
    ENABLED = False

    def test_file_mode_saves_only_when_changed(self):
        import alpaca_trailing_stop
        import orb_core

        orb_core.save_holdings_local("berkakar", {"AAPL": {"q": 1}})
        with mock.patch.object(orb_core, "save_holdings_local") as save:
            alpaca_trailing_stop._prune_holdings(
                orb_core.holdings_path("berkakar"), orb_core.load_holdings_local, save, {"AAPL"})
            save.assert_not_called()
            alpaca_trailing_stop._prune_holdings(
                orb_core.holdings_path("berkakar"), orb_core.load_holdings_local, save, set())
            save.assert_called_once_with("berkakar", {})


class ExportRoundtripTest(_Base):
    def test_migrate_then_export_reproduces_repo_files(self):
        """JSON -> SQLite -> JSON turu repodaki dosyaların içeriğini aynen geri vermeli."""
        from scripts import export_sqlite_to_json as export

        users = ["berkakar", "umuts", "utkusenses", "baranesirgen"]
        result = migration.migrate(REPO_DIR, users)
        self.assertEqual(result["failed"], [])
        out = os.path.join(self._tmp.name, "out")
        os.makedirs(out)
        files = export.export(out)
        self.assertEqual(len(files), len(result["migrated"]))
        for filename in files:
            with open(os.path.join(REPO_DIR, filename), encoding="utf-8") as a, \
                    open(os.path.join(out, filename), encoding="utf-8") as b:
                self.assertEqual(json.load(a), json.load(b), filename)


class FundAlertStateTest(_Base):
    def test_alert_state_roundtrip(self):
        import fon_hisse_uyari

        self.assertEqual(fon_hisse_uyari._load_state(), {})
        storage.save_json(fon_hisse_uyari.STATE_FILE, {"umuts": {"THYAO": -3.1}})
        self.assertEqual(fon_hisse_uyari._load_state(), {"umuts": {"THYAO": -3.1}})
        self.assertEqual(self.json_files(), [])


if __name__ == "__main__":
    unittest.main()
