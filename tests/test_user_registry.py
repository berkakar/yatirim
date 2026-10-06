import os
import tempfile
import unittest
from unittest import mock

import alpaca_keys
import storage
import user_registry as reg

GOOD_PW = "parola12345"


class RegistryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {
            "YATIRIM_DB_PATH": os.path.join(self._tmp.name, "test.db"),
            alpaca_keys.ENV_KEY: alpaca_keys.generate_key(),
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_admin(self, username="berkakar"):
        reg.create_user(username, "Berk", f"{username}@ornek.com", reg.hash_password(GOOD_PW), role=reg.ADMIN)


class RegisterTest(RegistryTestCase):
    def test_registration_is_pending_and_cannot_log_in(self):
        status = reg.register("Ali_V ", "Ali V", "Ali@Ornek.com", GOOD_PW, GOOD_PW)
        self.assertEqual(status, reg.PENDING)
        user = reg.get_user("ali_v")
        self.assertEqual(user["email"], "ali@ornek.com")
        self.assertNotIn("ali_v", reg.active_credentials()["usernames"])
        self.assertTrue(reg.check_password(GOOD_PW, user["password_hash"]))

    def test_validation(self):
        cases = [
            ("ab", "A", "a@b.co", GOOD_PW, GOOD_PW),            # kısa kullanıcı adı
            ("ali-v", "A", "a@b.co", GOOD_PW, GOOD_PW),         # geçersiz karakter
            ("_shared", "A", "a@b.co", GOOD_PW, GOOD_PW),       # ayrılmış
            ("admin", "A", "a@b.co", GOOD_PW, GOOD_PW),         # ayrılmış
            ("aliv", "", "a@b.co", GOOD_PW, GOOD_PW),           # ad boş
            ("aliv", "A", "a@b", GOOD_PW, GOOD_PW),             # e-posta
            ("aliv", "A", "a@b.co", "kisa1", "kisa1"),          # kısa şifre
            ("aliv", "A", "a@b.co", "sadeceharfler", "sadeceharfler"),
            ("aliv", "A", "a@b.co", GOOD_PW, GOOD_PW + "x"),    # tekrar uyuşmuyor
        ]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(reg.RegistryError):
                reg.register(*args)
        self.assertEqual(reg.list_users(), [])

    def test_duplicate_username_and_email(self):
        reg.register("aliv", "Ali", "ali@ornek.com", GOOD_PW, GOOD_PW)
        with self.assertRaises(reg.RegistryError):
            reg.register("ALIV", "Başka", "baska@ornek.com", GOOD_PW, GOOD_PW)
        with self.assertRaises(reg.RegistryError):
            reg.register("veli", "Veli", "ALI@ornek.com", GOOD_PW, GOOD_PW)

    def test_invite_code_activates_and_is_consumed(self):
        self.make_admin()
        code = reg.create_invite("berkakar", max_uses=1)
        self.assertEqual(reg.register("aliv", "Ali", "ali@o.com", GOOD_PW, GOOD_PW, code.lower()), reg.ACTIVE)
        self.assertIn("aliv", reg.active_credentials()["usernames"])
        with self.assertRaises(reg.RegistryError):
            reg.register("veli", "Veli", "veli@o.com", GOOD_PW, GOOD_PW, code)
        self.assertIsNone(reg.get_user("veli"))  # geçersiz kodla başvuru hiç alınmaz

    def test_revoked_and_expired_invites(self):
        self.make_admin()
        code = reg.create_invite("berkakar", max_uses=5)
        reg.revoke_invite(code, "berkakar")
        with self.assertRaises(reg.RegistryError):
            reg.register("aliv", "Ali", "ali@o.com", GOOD_PW, GOOD_PW, code)
        code = reg.create_invite("berkakar", max_uses=5, valid_days=1)
        with storage.connection() as conn:
            conn.execute("UPDATE invites SET expires_at = '2000-01-01T00:00:00+00:00' WHERE code = ?", (code,))
        with self.assertRaises(reg.RegistryError):
            reg.register("aliv", "Ali", "ali@o.com", GOOD_PW, GOOD_PW, code)

    def test_pending_flood_limit(self):
        with mock.patch.object(reg, "MAX_PENDING_PER_HOUR", 2):
            reg.register("kul1", "A", "a1@o.com", GOOD_PW, GOOD_PW)
            reg.register("kul2", "A", "a2@o.com", GOOD_PW, GOOD_PW)
            with self.assertRaises(reg.RegistryError):
                reg.register("kul3", "A", "a3@o.com", GOOD_PW, GOOD_PW)

    def test_requires_sqlite(self):
        with mock.patch.dict(os.environ, {"YATIRIM_DB_PATH": ""}):
            with self.assertRaises(reg.RegistryError):
                reg.register("aliv", "Ali", "ali@o.com", GOOD_PW, GOOD_PW)


class AdminFlowTest(RegistryTestCase):
    def setUp(self):
        super().setUp()
        self.make_admin()
        reg.register("aliv", "Ali", "ali@o.com", GOOD_PW, GOOD_PW)

    def test_approve_reject_disable_enable(self):
        reg.approve("aliv", "berkakar")
        self.assertTrue(reg.is_active("aliv"))
        self.assertFalse(reg.is_admin("aliv"))
        reg.disable("aliv", "berkakar", "test")
        self.assertNotIn("aliv", reg.active_credentials()["usernames"])
        reg.enable("aliv", "berkakar")
        self.assertTrue(reg.is_active("aliv"))
        with self.assertRaises(reg.RegistryError):
            reg.reject("aliv", "berkakar")  # aktif kullanıcı reddedilemez
        actions = [e["action"] for e in reg.audit_log("aliv")]
        self.assertEqual(actions, ["enable", "disable", "approve", "register"])

    def test_rejected_cannot_log_in(self):
        reg.reject("aliv", "berkakar", "tanımıyorum")
        self.assertEqual(reg.get_user("aliv")["status"], reg.REJECTED)
        self.assertEqual(reg.get_user("aliv")["note"], "tanımıyorum")
        self.assertNotIn("aliv", reg.active_credentials()["usernames"])

    def test_last_admin_is_protected(self):
        with self.assertRaises(reg.RegistryError):
            reg.set_role("berkakar", reg.USER, "berkakar")
        with self.assertRaises(reg.RegistryError):
            reg.disable("berkakar", "berkakar")
        reg.approve("aliv", "berkakar")
        reg.set_role("aliv", reg.ADMIN, "berkakar")
        reg.set_role("berkakar", reg.USER, "aliv")
        self.assertFalse(reg.is_admin("berkakar"))
        self.assertEqual(reg.admin_count(), 1)

    def test_password_reset_forces_change(self):
        reg.approve("aliv", "berkakar")
        temp = reg.reset_password("aliv", "berkakar")
        self.assertTrue(reg.get_user("aliv")["must_change_pw"])
        self.assertFalse(reg.verify_password("aliv", GOOD_PW))
        with self.assertRaises(reg.RegistryError):
            reg.change_password("aliv", "yanlis", "yeniparola99", "yeniparola99")
        reg.change_password("aliv", temp, "yeniparola99", "yeniparola99")
        self.assertFalse(reg.get_user("aliv")["must_change_pw"])
        self.assertTrue(reg.verify_password("aliv", "yeniparola99"))

    def test_delete_purges_only_that_users_data(self):
        reg.approve("aliv", "berkakar")
        storage.write("selected_tickers", "aliv", ["AAPL"])
        storage.write("selected_tickers", "berkakar", ["MSFT"])
        self.assertEqual(reg.delete_user("aliv", "berkakar"), 1)
        self.assertIsNone(reg.get_user("aliv"))
        self.assertIsNone(storage.read("selected_tickers", "aliv"))
        self.assertEqual(storage.read("selected_tickers", "berkakar"), ["MSFT"])
        with self.assertRaises(reg.RegistryError):
            reg.delete_user("berkakar", "berkakar")

    def test_profile_email_must_stay_unique(self):
        reg.approve("aliv", "berkakar")
        with self.assertRaises(reg.RegistryError):
            reg.update_profile("aliv", "Ali", "berkakar@ornek.com", "aliv")
        reg.update_profile("aliv", "Ali Veli", "yeni@o.com", "aliv")
        self.assertEqual(reg.get_user("aliv")["name"], "Ali Veli")


class AlpacaKeysTest(RegistryTestCase):
    def test_secret_is_encrypted_at_rest(self):
        alpaca_keys.set_keys("aliv", alpaca_keys.PAPER, "PKTEST1234", "cok-gizli")
        with storage.connection() as conn:
            raw = conn.execute("SELECT secret_enc FROM alpaca_keys").fetchone()[0]
        self.assertNotIn("cok-gizli", raw)
        self.assertEqual(alpaca_keys.get_keys("aliv", alpaca_keys.PAPER), ("PKTEST1234", "cok-gizli"))
        self.assertEqual(alpaca_keys.mask("PKTEST123456"), "PKTE…3456")

    def test_wrong_encryption_key_is_an_error_not_silent(self):
        alpaca_keys.set_keys("aliv", alpaca_keys.LIVE, "AK", "s")
        with mock.patch.dict(os.environ, {alpaca_keys.ENV_KEY: alpaca_keys.generate_key()}):
            with self.assertRaises(alpaca_keys.KeyStoreError):
                alpaca_keys.get_keys("aliv", alpaca_keys.LIVE)

    def test_missing_encryption_key(self):
        with mock.patch.dict(os.environ, {alpaca_keys.ENV_KEY: ""}):
            self.assertFalse(alpaca_keys.encryption_available())
            with self.assertRaises(alpaca_keys.KeyStoreError):
                alpaca_keys.set_keys("aliv", alpaca_keys.PAPER, "PK", "s")

    def test_delete(self):
        alpaca_keys.set_keys("aliv", alpaca_keys.PAPER, "PK", "s")
        alpaca_keys.set_keys("aliv", alpaca_keys.LIVE, "AK", "s")
        self.assertTrue(alpaca_keys.delete_keys("aliv", alpaca_keys.LIVE))
        self.assertFalse(alpaca_keys.has_keys("aliv", alpaca_keys.LIVE))
        alpaca_keys.delete_all("aliv")
        self.assertFalse(alpaca_keys.has_keys("aliv", alpaca_keys.PAPER))


if __name__ == "__main__":
    unittest.main()
