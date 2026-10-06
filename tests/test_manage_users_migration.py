"""deploy/web/manage_users.py `tasi`: secrets.toml kullanıcıları ve Alpaca anahtarları
veritabanına taşınır, sonra yalnızca bu bölümler secrets dosyalarından silinir."""
import importlib.util
import os
import tempfile
import tomllib
import unittest
from unittest import mock

import alpaca_keys
import user_registry as reg

GLOBAL = '''GITHUB_TOKEN = "ghp_x"

[cookie]
name = "yatirim"
key = "k"
expiry_days = 30

[credentials.usernames.berkakar]
name = "Berk"
email = "berk@o.com"
password = "{hash}"

[alpaca.berkakar]
key_id = "PKAAAA111122223333"
secret_key = "papersecret"
live_key_id = "AKBBBB"
live_secret_key = "livesecret"
'''
# İkinci dosyadaki [credentials] Streamlit'te birinciyi gizler (üst düzey birleştirme).
PROJECT = '''TELEGRAM_CHAT_ID = "123"

[credentials.usernames.umuts]
name = "Umut"
password = "duzmetin123"
'''


def load_cli(env):
    with mock.patch.dict(os.environ, env):
        spec = importlib.util.spec_from_file_location(
            "manage_users_under_test", os.path.join("deploy", "web", "manage_users.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class MigrationTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.g = os.path.join(tmp.name, "g.toml")
        self.p = os.path.join(tmp.name, "p.toml")
        with open(self.g, "w") as f:
            f.write(GLOBAL.format(hash=reg.hash_password("berkparola1")))
        with open(self.p, "w") as f:
            f.write(PROJECT)
        env = {"YATIRIM_DB_PATH": os.path.join(tmp.name, "t.db"), alpaca_keys.ENV_KEY: alpaca_keys.generate_key(),
               "SECRETS_GLOBAL": self.g, "SECRETS_PROJECT": self.p}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cli = load_cli(env)

    def run_tasi(self, admins=None):
        args = mock.Mock(yes=True, yonetici=admins)
        return self.cli.cmd_tasi(args)

    def test_moves_effective_users_and_keys_then_strips_sections(self):
        self.assertEqual(self.run_tasi(), 0)
        # İki dosyadaki kullanıcıların hepsi taşınır (Streamlit birincidekileri gizliyor olsa da).
        self.assertTrue(reg.is_admin("berkakar"))
        self.assertTrue(reg.verify_password("berkakar", "berkparola1"))
        self.assertFalse(reg.is_admin("umuts"))
        self.assertTrue(reg.verify_password("umuts", "duzmetin123"))  # düz metin şifre hash'lendi
        self.assertEqual(alpaca_keys.get_keys("berkakar", alpaca_keys.LIVE), ("AKBBBB", "livesecret"))
        with open(self.g, "rb") as f:
            g = tomllib.load(f)
        with open(self.p, "rb") as f:
            p = tomllib.load(f)
        self.assertEqual(g, {"GITHUB_TOKEN": "ghp_x", "cookie": {"name": "yatirim", "key": "k", "expiry_days": 30}})
        self.assertEqual(p, {"TELEGRAM_CHAT_ID": "123"})
        # Tekrar çalıştırmak güvenli.
        self.assertEqual(self.run_tasi(), 0)

    def test_refuses_without_an_admin(self):
        with self.assertRaises(self.cli.Hata):
            self.run_tasi(["yok"])
        self.assertEqual(reg.list_users(), [])
        with open(self.p) as f:
            self.assertIn("credentials", f.read())


if __name__ == "__main__":
    unittest.main()
