import json
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

import storage
from scripts import migrate_json_to_sqlite as migration


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "sub", "test.db")
        patcher = mock.patch.dict(os.environ, {"YATIRIM_DB_PATH": self.db})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)


class ReadWriteTest(StorageTestCase):
    def test_missing_returns_copy_of_default(self):
        default = {"weights": {}}
        value = storage.read("portfolio_config", "berkakar", default)
        self.assertEqual(value, default)
        value["weights"]["AAPL"] = 1
        self.assertEqual(default, {"weights": {}})

    def test_creates_db_directory(self):
        storage.write("x", "berkakar", 1)
        self.assertTrue(os.path.exists(self.db))

    def test_roundtrip_keeps_json_shape(self):
        value = {"budget": 1000.5, "weights": {"AAPL": 0.5}, "tickers": ["ŞİŞE", "THYAO.IS"], "on": True, "x": None}
        storage.write("portfolio_config", "berkakar", value)
        self.assertEqual(storage.read("portfolio_config", "berkakar"), value)

    def test_list_value(self):
        storage.write("selected_tickers", "umuts", ["AAPL", "MSFT"])
        self.assertEqual(storage.read("selected_tickers", "umuts"), ["AAPL", "MSFT"])

    def test_overwrite(self):
        storage.write("initial_capital", "berkakar", {"initial_capital": 100})
        storage.write("initial_capital", "berkakar", {"initial_capital": 200})
        self.assertEqual(storage.read("initial_capital", "berkakar"), {"initial_capital": 200})

    def test_users_are_isolated(self):
        storage.write("selected_tickers", "berkakar", ["AAPL"])
        storage.write("selected_tickers", "umuts", ["MSFT"])
        self.assertEqual(storage.read("selected_tickers", "berkakar"), ["AAPL"])
        self.assertEqual(storage.read("selected_tickers", "umuts"), ["MSFT"])
        self.assertIsNone(storage.read("selected_tickers", "utkusenses"))

    def test_shared_is_default_and_none_means_shared(self):
        storage.write("tefas_fonlari_cache", None, {"a": 1})
        self.assertEqual(storage.read("tefas_fonlari_cache"), {"a": 1})
        self.assertEqual(storage.read("tefas_fonlari_cache", storage.SHARED), {"a": 1})
        self.assertIsNone(storage.read("tefas_fonlari_cache", "berkakar"))

    def test_listing(self):
        storage.write("b", "umuts", 1)
        storage.write("a", "umuts", 1)
        storage.write("c", "berkakar", 1)
        storage.write("tefas_fonlari_cache", storage.SHARED, 1)
        self.assertEqual(storage.list_names("umuts"), ["a", "b"])
        self.assertEqual(storage.list_names(), ["tefas_fonlari_cache"])
        self.assertEqual(storage.list_users(), ["berkakar", "umuts"])

    def test_delete_and_exists(self):
        storage.write("x", "berkakar", 1)
        self.assertTrue(storage.exists("x", "berkakar"))
        self.assertTrue(storage.delete("x", "berkakar"))
        self.assertFalse(storage.exists("x", "berkakar"))
        self.assertFalse(storage.delete("x", "berkakar"))

    def test_updated_at(self):
        self.assertIsNone(storage.updated_at("x", "berkakar"))
        storage.write("x", "berkakar", 1)
        self.assertRegex(storage.updated_at("x", "berkakar"), r"^\d{4}-\d\d-\d\dT")

    def test_uses_wal_mode(self):
        storage.write("x", "berkakar", 1)
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        finally:
            conn.close()


class UpdateTest(StorageTestCase):
    def test_update_missing_starts_from_default_copy(self):
        default = {}
        result = storage.update("orb_scan_holdings", "berkakar", lambda h: {**h, "AAPL": 1}, default)
        self.assertEqual(result, {"AAPL": 1})
        self.assertEqual(default, {})
        self.assertEqual(storage.read("orb_scan_holdings", "berkakar"), {"AAPL": 1})

    def test_exception_rolls_back(self):
        storage.write("x", "berkakar", {"n": 1})

        def boom(value):
            raise RuntimeError("fail")

        with self.assertRaises(RuntimeError):
            storage.update("x", "berkakar", boom)
        self.assertEqual(storage.read("x", "berkakar"), {"n": 1})
        storage.write("x", "berkakar", {"n": 2})  # bağlantı/kilit serbest kalmış olmalı
        self.assertEqual(storage.read("x", "berkakar"), {"n": 2})

    def test_concurrent_updates_do_not_lose_writes(self):
        storage.write("counter", "berkakar", {"n": 0})
        threads_n, per_thread = 8, 25

        def worker():
            for _ in range(per_thread):
                storage.update("counter", "berkakar", lambda v: {"n": v["n"] + 1})

        threads = [threading.Thread(target=worker) for _ in range(threads_n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(storage.read("counter", "berkakar"), {"n": threads_n * per_thread})


class HistoryTest(StorageTestCase):
    def test_settings_keep_previous_values_newest_first(self):
        for i in range(3):
            storage.write("stop_loss_settings", "berkakar", {"v": i})
        values = [h["value"] for h in storage.history("stop_loss_settings", "berkakar")]
        self.assertEqual(values, [{"v": 1}, {"v": 0}])

    def test_unchanged_write_adds_no_history(self):
        storage.write("stop_loss_settings", "berkakar", {"v": 1})
        storage.write("stop_loss_settings", "berkakar", {"v": 1})
        self.assertEqual(storage.history("stop_loss_settings", "berkakar"), [])

    def test_caches_have_no_history(self):
        for name in ("tefas_fonlari_cache", "bildirim_durumu", "backtest_results"):
            storage.write(name, storage.SHARED, 1)
            storage.write(name, storage.SHARED, 2)
            self.assertEqual(storage.history(name), [], name)

    def test_history_is_pruned(self):
        with mock.patch.object(storage, "HISTORY_LIMIT", 3):
            for i in range(10):
                storage.write("x", "berkakar", i)
        self.assertEqual([h["value"] for h in storage.history("x", "berkakar")], [8, 7, 6])

    def test_delete_keeps_last_value_in_history(self):
        storage.write("x", "berkakar", {"v": 1})
        storage.delete("x", "berkakar")
        self.assertEqual(storage.history("x", "berkakar")[0]["value"], {"v": 1})


class BackupTest(StorageTestCase):
    def test_backup_is_readable_copy(self):
        storage.write("selected_tickers", "berkakar", ["AAPL"])
        dest = os.path.join(self._tmp.name, "yedek", "copy.db")
        storage.backup(dest)
        with mock.patch.dict(os.environ, {"YATIRIM_DB_PATH": dest}):
            self.assertEqual(storage.read("selected_tickers", "berkakar"), ["AAPL"])


class MigrationTest(StorageTestCase):
    USERS = ["berkakar", "umuts", "utkusenses", "baranesirgen"]

    def setUp(self):
        super().setUp()
        self.src = os.path.join(self._tmp.name, "repo")
        os.makedirs(self.src)

    def _put(self, filename, value):
        with open(os.path.join(self.src, filename), "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)

    def test_parse_filename(self):
        cases = {
            "selected_tickers_berkakar.json": ("berkakar", "selected_tickers"),
            "custom_stock_group_markets_berkakar.json": ("berkakar", "custom_stock_group_markets"),
            "selected_tickers_baranesirgen.json": ("baranesirgen", "selected_tickers"),
            "tefas_fonlari_cache.json": (storage.SHARED, "tefas_fonlari_cache"),
            "selected_tickers_bilinmeyen.json": (storage.SHARED, "selected_tickers_bilinmeyen"),
            "berkakar.json": (storage.SHARED, "berkakar"),
        }
        for filename, expected in cases.items():
            self.assertEqual(migration.parse_filename(filename, self.USERS), expected, filename)

    def test_longest_username_wins(self):
        self.assertEqual(migration.parse_filename("x_ali_veli.json", ["veli", "ali_veli"]), ("ali_veli", "x"))

    def test_migrates_and_skips_non_data_files(self):
        self._put("selected_tickers_umuts.json", ["AAPL"])
        self._put("kap_portfoy_cache.json", {"A": {"x": 1}})
        self._put("version_info.json", {"v": 1})
        result = migration.migrate(self.src, self.USERS)
        self.assertEqual(len(result["migrated"]), 2)
        self.assertEqual(storage.read("selected_tickers", "umuts"), ["AAPL"])
        self.assertEqual(storage.read("kap_portfoy_cache"), {"A": {"x": 1}})
        self.assertFalse(storage.exists("version_info"))

    def test_dry_run_writes_nothing(self):
        self._put("selected_tickers_umuts.json", ["AAPL"])
        result = migration.migrate(self.src, self.USERS, dry_run=True)
        self.assertEqual(len(result["migrated"]), 1)
        self.assertFalse(storage.exists("selected_tickers", "umuts"))

    def test_existing_rows_kept_unless_overwrite(self):
        storage.write("selected_tickers", "umuts", ["DB"])
        self._put("selected_tickers_umuts.json", ["FILE"])
        result = migration.migrate(self.src, self.USERS)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(storage.read("selected_tickers", "umuts"), ["DB"])
        migration.migrate(self.src, self.USERS, overwrite=True)
        self.assertEqual(storage.read("selected_tickers", "umuts"), ["FILE"])

    def test_invalid_json_is_reported_and_others_continue(self):
        with open(os.path.join(self.src, "bozuk_berkakar.json"), "w") as f:
            f.write("{bozuk")
        self._put("selected_tickers_umuts.json", ["AAPL"])
        result = migration.migrate(self.src, self.USERS)
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(storage.read("selected_tickers", "umuts"), ["AAPL"])

    def test_main_reads_users_from_secrets(self):
        os.makedirs(os.path.join(self.src, ".streamlit"))
        with open(os.path.join(self.src, ".streamlit", "secrets.toml"), "w") as f:
            f.write('[credentials.usernames.umuts]\nname = "U"\npassword = "x"\n')
        self._put("selected_tickers_umuts.json", ["AAPL"])
        with mock.patch("sys.stdout"):
            code = migration.main(["--source-dir", self.src])
        self.assertEqual(code, 0)
        self.assertEqual(storage.read("selected_tickers", "umuts"), ["AAPL"])

    def test_main_reads_users_from_home_secrets(self):
        home = os.path.join(self._tmp.name, "home")
        os.makedirs(os.path.join(home, ".streamlit"))
        with open(os.path.join(home, ".streamlit", "secrets.toml"), "w") as f:
            f.write('[credentials.usernames.umuts]\nname = "U"\npassword = "x"\n')
        self._put("selected_tickers_umuts.json", ["AAPL"])
        with mock.patch.dict(os.environ, {"HOME": home}), mock.patch("sys.stdout"):
            self.assertEqual(migration.main(["--source-dir", self.src]), 0)
        self.assertEqual(storage.read("selected_tickers", "umuts"), ["AAPL"])

    def test_main_without_users_fails(self):
        with mock.patch.dict(os.environ, {"HOME": self._tmp.name}), \
                mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            self.assertEqual(migration.main(["--source-dir", self.src]), 2)


if __name__ == "__main__":
    unittest.main()
