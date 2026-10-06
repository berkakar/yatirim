"""Kullanıcıların Alpaca API anahtarları - şifrelenmiş olarak SQLite'ta.

Eskiden secrets.toml [alpaca.<kullanıcı>] bölümündeydi; artık her kullanıcı
kendi Sanal Para (paper) ve Gerçek Para (live) anahtarını arayüzde
"👤 Hesabım" sayfasından girer. Gizli anahtar (secret) Fernet ile şifrelenir;
şifreleme anahtarı YATIRIM_SECRET_KEY ortam değişkenindedir (/etc/yatirim/env -
hem arayüz hem zamanlanmış işler okur; `users.sh tasi` / `users.sh anahtar`
yoksa üretir). Bu değişken kaybolursa kayıtlı anahtarlar çözülemez, kullanıcılar
yeniden girer - veritabanı yedeği tek başına anahtarları açığa çıkarmaz.

Streamlit'e bağlı değildir (bkz. alpaca_account.build_job_client).
"""

import os
from datetime import datetime, timezone

import storage

PAPER, LIVE = "paper", "live"  # alpaca_account.PAPER / LIVE ile aynı (döngüsel import olmasın)
ENV_KEY = "YATIRIM_SECRET_KEY"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alpaca_keys (
    username   TEXT NOT NULL,
    mode       TEXT NOT NULL,
    key_id     TEXT NOT NULL,
    secret_enc TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (username, mode)
);
"""
_schema_ready = set()


class KeyStoreError(Exception):
    pass


def encryption_available() -> bool:
    return bool(os.environ.get(ENV_KEY)) and storage.enabled()


def generate_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def _fernet():
    key = os.environ.get(ENV_KEY)
    if not key:
        raise KeyStoreError(
            f"{ENV_KEY} tanımlı değil - Alpaca anahtarları şifrelenemez/çözülemez "
            "(sunucuda: sudo bash deploy/web/users.sh anahtar)."
        )
    from cryptography.fernet import Fernet
    try:
        return Fernet(key.encode())
    except ValueError as e:
        raise KeyStoreError(f"{ENV_KEY} geçerli bir Fernet anahtarı değil: {e}")


def _conn():
    if not storage.enabled():
        raise KeyStoreError("Alpaca anahtarları için SQLite gerekli (YATIRIM_DB_PATH tanımlı değil).")
    return storage.connection()


def _ensure(conn) -> None:
    path = storage.db_path()
    if path not in _schema_ready:
        conn.executescript(_SCHEMA)
        _schema_ready.add(path)


def _check_mode(mode: str) -> None:
    if mode not in (PAPER, LIVE):
        raise KeyStoreError(f"Geçersiz hesap türü: {mode}")


def set_keys(username: str, mode: str, key_id: str, secret_key: str) -> None:
    _check_mode(mode)
    key_id, secret_key = (key_id or "").strip(), (secret_key or "").strip()
    if not key_id or not secret_key:
        raise KeyStoreError("API Key ID ve Secret Key boş olamaz.")
    secret_enc = _fernet().encrypt(secret_key.encode()).decode()
    with _conn() as conn:
        _ensure(conn)
        with storage.write_transaction(conn):
            conn.execute(
                """INSERT INTO alpaca_keys (username, mode, key_id, secret_enc, updated_at) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (username, mode) DO UPDATE SET key_id = excluded.key_id,
                       secret_enc = excluded.secret_enc, updated_at = excluded.updated_at""",
                (username, mode, key_id, secret_enc, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )


def delete_keys(username: str, mode: str) -> bool:
    _check_mode(mode)
    with _conn() as conn:
        _ensure(conn)
        with storage.write_transaction(conn):
            return conn.execute(
                "DELETE FROM alpaca_keys WHERE username = ? AND mode = ?", (username, mode)
            ).rowcount > 0


def delete_all(username: str) -> None:
    with _conn() as conn:
        _ensure(conn)
        with storage.write_transaction(conn):
            conn.execute("DELETE FROM alpaca_keys WHERE username = ?", (username,))


def key_info(username: str, mode: str) -> dict | None:
    """Şifre çözmeden: {"key_id", "updated_at"} ya da None."""
    if not storage.enabled():
        return None
    with _conn() as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT key_id, updated_at FROM alpaca_keys WHERE username = ? AND mode = ?", (username, mode)
        ).fetchone()
    return {"key_id": row[0], "updated_at": row[1]} if row else None


def has_keys(username: str, mode: str) -> bool:
    return key_info(username, mode) is not None


def get_keys(username: str, mode: str) -> tuple[str | None, str | None]:
    """(key_id, secret_key) - kayıt yoksa (None, None). Gerçek Para için
    paper anahtarlarına asla geri düşülmez. Kayıt var ama çözülemiyorsa
    KeyStoreError (sessizce yok sayılmasın)."""
    _check_mode(mode)
    if not storage.enabled():
        return None, None
    with _conn() as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT key_id, secret_enc FROM alpaca_keys WHERE username = ? AND mode = ?", (username, mode)
        ).fetchone()
    if not row:
        return None, None
    from cryptography.fernet import InvalidToken
    try:
        secret = _fernet().decrypt(row[1].encode()).decode()
    except InvalidToken:
        raise KeyStoreError(
            f"'{username}' için kayıtlı {mode} anahtarı çözülemedi ({ENV_KEY} değişmiş olabilir); "
            "anahtarları Hesabım sayfasından yeniden girin."
        )
    return row[0], secret


def mask(key_id: str | None) -> str:
    if not key_id:
        return "-"
    return key_id[:4] + "…" + key_id[-4:] if len(key_id) > 10 else key_id[:2] + "…"
