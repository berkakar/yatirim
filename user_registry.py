"""Arayüz kullanıcıları - kayıt, onay, şifre ve yetki (SQLite, bkz. storage.py).

Giriş yapabilecek kullanıcıların TEK kaynağı bu modüldeki `users` tablosudur
(eskiden secrets.toml [credentials.usernames.*]; taşımak için
`sudo bash deploy/web/users.sh tasi`). Streamlit'e bağlı değildir: hem arayüz
hem sunucudaki yönetim script'i (deploy/web/manage_users.py) bunu kullanır.

Durumlar:
    pending  -> başvuru yapıldı, yönetici onayı bekliyor (giriş yapamaz)
    active   -> giriş yapabilir
    rejected -> başvuru reddedildi (giriş yapamaz; aynı adla yeniden başvurulamaz)
    disabled -> yönetici devre dışı bıraktı (giriş yapamaz, verisi durur)

Roller: "admin" (Kullanıcı Yönetimi sayfasını görür) ve "user".

Geçerli bir davet koduyla yapılan başvuru doğrudan `active` olur.
Her yönetim işlemi `user_audit` tablosuna yazılır.
"""

import re
import secrets
import string
from datetime import datetime, timedelta, timezone

import bcrypt

import storage

PENDING, ACTIVE, REJECTED, DISABLED = "pending", "active", "rejected", "disabled"
STATUSES = (PENDING, ACTIVE, REJECTED, DISABLED)
STATUS_LABELS = {PENDING: "Onay bekliyor", ACTIVE: "Aktif", REJECTED: "Reddedildi", DISABLED: "Devre dışı"}
ADMIN, USER = "admin", "user"

USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 10
# Kullanıcı adı aynı zamanda verinin anahtarı (settings.username, *_<kullanıcı>.json);
# ortak alanla ya da sistem adlarıyla çakışmasın.
RESERVED_USERNAMES = {storage.SHARED, "admin", "root", "system", "yatirim", "shared", "test"}
# Kötüye kullanım sınırı: son 1 saatte bu kadar bekleyen başvuru varsa yenisi alınmaz.
MAX_PENDING_PER_HOUR = 20

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username       TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    email          TEXT NOT NULL,
    password_hash  TEXT NOT NULL,
    status         TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'user',
    must_change_pw INTEGER NOT NULL DEFAULT 0,
    invite_code    TEXT,
    note           TEXT,
    created_at     TEXT NOT NULL,
    decided_at     TEXT,
    decided_by     TEXT,
    last_login_at  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS users_email ON users (email);
CREATE TABLE IF NOT EXISTS invites (
    code       TEXT PRIMARY KEY,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    max_uses   INTEGER NOT NULL,
    used       INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT,
    revoked    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS user_audit (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       TEXT NOT NULL,
    username TEXT NOT NULL,
    action   TEXT NOT NULL,
    actor    TEXT NOT NULL,
    detail   TEXT
);
"""

_USER_COLUMNS = ("username", "name", "email", "password_hash", "status", "role", "must_change_pw",
                 "invite_code", "note", "created_at", "decided_at", "decided_by", "last_login_at")


class RegistryError(Exception):
    """Kullanıcıya gösterilebilecek doğrulama / işlem hatası."""


# ------------------------------------------------------------------ yardımcılar

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn():
    if not storage.enabled():
        raise RegistryError("Kullanıcı kaydı için SQLite gerekli (YATIRIM_DB_PATH tanımlı değil).")
    return storage.connection()


_schema_ready = set()


def _ensure(conn) -> None:
    path = storage.db_path()
    if path not in _schema_ready:
        conn.executescript(_SCHEMA)
        _schema_ready.add(path)


def _audit(conn, username: str, action: str, actor: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO user_audit (at, username, action, actor, detail) VALUES (?, ?, ?, ?, ?)",
        (_now(), username, action, actor, detail),
    )


def _row_to_user(row) -> dict | None:
    if row is None:
        return None
    user = dict(zip(_USER_COLUMNS, row))
    user["must_change_pw"] = bool(user["must_change_pw"])
    return user


def _select_user(conn, username: str) -> dict | None:
    row = conn.execute(
        f"SELECT {', '.join(_USER_COLUMNS)} FROM users WHERE username = ?", (username,)
    ).fetchone()
    return _row_to_user(row)


def _require_user(conn, username: str) -> dict:
    user = _select_user(conn, username)
    if user is None:
        raise RegistryError(f"'{username}' adında bir kullanıcı yok.")
    return user


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_username(username: str) -> None:
    if not USERNAME_RE.match(username):
        raise RegistryError("Kullanıcı adı 3-20 karakter olmalı; yalnızca küçük harf, rakam ve _ içerebilir.")
    if username in RESERVED_USERNAMES or username.startswith("_"):
        raise RegistryError(f"'{username}' kullanıcı adı ayrılmış, başka bir ad seçin.")


def validate_password(password: str, confirm: str | None = None) -> None:
    if len(password or "") < MIN_PASSWORD_LEN:
        raise RegistryError(f"Şifre en az {MIN_PASSWORD_LEN} karakter olmalı.")
    if password.isdigit() or password.isalpha():
        raise RegistryError("Şifre hem harf hem rakam (ya da simge) içermeli.")
    if confirm is not None and password != confirm:
        raise RegistryError("Şifreler eşleşmiyor.")


def validate_email(email: str) -> None:
    if not EMAIL_RE.match(email):
        raise RegistryError("Geçerli bir e-posta adresi girin.")


def generate_password(length: int = 14) -> str:
    """Geçici şifre: harf + rakam, karışması kolay karakterler (0/O, 1/l/I) hariç."""
    alphabet = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


# ------------------------------------------------------------------ okuma

def get_user(username: str) -> dict | None:
    with _conn() as conn:
        _ensure(conn)
        return _select_user(conn, normalize_username(username))


def list_users(status: str | None = None) -> list[dict]:
    with _conn() as conn:
        _ensure(conn)
        sql = f"SELECT {', '.join(_USER_COLUMNS)} FROM users"
        args = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        rows = conn.execute(sql + " ORDER BY created_at, username", args).fetchall()
    return [_row_to_user(r) for r in rows]


def active_credentials() -> dict:
    """streamlit-authenticator'ın beklediği biçimde yalnızca AKTİF kullanıcılar:
    {"usernames": {u: {"name", "email", "password", "roles"}}}."""
    creds = {}
    for u in list_users(ACTIVE):
        creds[u["username"]] = {
            "name": u["name"], "email": u["email"], "password": u["password_hash"], "roles": [u["role"]],
        }
    return {"usernames": creds}


def is_active(username: str) -> bool:
    user = get_user(username)
    return bool(user and user["status"] == ACTIVE)


def is_admin(username: str) -> bool:
    user = get_user(username)
    return bool(user and user["status"] == ACTIVE and user["role"] == ADMIN)


def admin_count() -> int:
    return sum(1 for u in list_users(ACTIVE) if u["role"] == ADMIN)


def audit_log(username: str | None = None, limit: int = 200) -> list[dict]:
    with _conn() as conn:
        _ensure(conn)
        sql = "SELECT at, username, action, actor, detail FROM user_audit"
        args: tuple = ()
        if username:
            sql += " WHERE username = ?"
            args = (username,)
        rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", args + (limit,)).fetchall()
    return [dict(zip(("at", "username", "action", "actor", "detail"), r)) for r in rows]


# ------------------------------------------------------------------ kayıt

def _invite_usable(row, now: str) -> bool:
    if row is None:
        return False
    _code, _by, _at, max_uses, used, expires_at, revoked = row
    return not revoked and used < max_uses and (expires_at is None or expires_at > now)


def register(username: str, name: str, email: str, password: str, confirm: str | None = None,
             invite_code: str | None = None) -> str:
    """Başvuruyu kaydeder, oluşan durumu döner (ACTIVE ya da PENDING).
    Davet kodu verilip geçersizse başvuru alınmaz (kullanıcı kodu düzeltebilsin)."""
    username = normalize_username(username)
    email = normalize_email(email)
    name = (name or "").strip()
    invite_code = (invite_code or "").strip().upper() or None
    validate_username(username)
    if not name:
        raise RegistryError("Ad Soyad boş olamaz.")
    validate_email(email)
    validate_password(password, confirm)
    password_hash = hash_password(password)  # kilit dışında (bcrypt yavaş)

    with _conn() as conn:
        _ensure(conn)
        with storage.write_transaction(conn):
            if _select_user(conn, username) is not None:
                raise RegistryError("Bu kullanıcı adı alınmış.")
            if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                raise RegistryError("Bu e-posta adresiyle zaten bir hesap/başvuru var.")
            now = _now()
            status = PENDING
            if invite_code:
                row = conn.execute(
                    "SELECT code, created_by, created_at, max_uses, used, expires_at, revoked "
                    "FROM invites WHERE code = ?", (invite_code,),
                ).fetchone()
                if not _invite_usable(row, now):
                    raise RegistryError("Davet kodu geçersiz, süresi dolmuş ya da kullanım hakkı bitmiş.")
                conn.execute("UPDATE invites SET used = used + 1 WHERE code = ?", (invite_code,))
                status = ACTIVE
            else:
                hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
                recent = conn.execute(
                    "SELECT COUNT(*) FROM users WHERE status = ? AND created_at > ?", (PENDING, hour_ago),
                ).fetchone()[0]
                if recent >= MAX_PENDING_PER_HOUR:
                    raise RegistryError("Şu an çok fazla başvuru var, lütfen daha sonra tekrar deneyin.")
            conn.execute(
                "INSERT INTO users (username, name, email, password_hash, status, role, invite_code, "
                "created_at, decided_at, decided_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (username, name, email, password_hash, status, USER, invite_code, now,
                 now if status == ACTIVE else None, f"davet:{invite_code}" if status == ACTIVE else None),
            )
            _audit(conn, username, "register", username,
                   f"davet kodu {invite_code} ile otomatik onay" if invite_code else "onay bekliyor")
    return status


def create_user(username: str, name: str, email: str, password_hash: str, role: str = USER,
                actor: str = "system", must_change_pw: bool = False, allow_reserved: bool = False) -> None:
    """Doğrudan AKTİF kullanıcı ekler (yönetim script'i ve secrets.toml'dan taşıma).
    password_hash bcrypt olmalı."""
    username = normalize_username(username)
    email = normalize_email(email) or f"{username}@yerel"  # e-postası olmayan eski kullanıcılar için
    if not allow_reserved:
        validate_username(username)
    if role not in (ADMIN, USER):
        raise RegistryError(f"Geçersiz rol: {role}")
    if not password_hash.startswith("$2"):
        raise RegistryError(f"'{username}' için şifre bcrypt hash'i değil.")
    with _conn() as conn:
        _ensure(conn)
        with storage.write_transaction(conn):
            if _select_user(conn, username) is not None:
                raise RegistryError(f"'{username}' zaten tanımlı.")
            if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                raise RegistryError(f"'{email}' e-postası başka bir kullanıcıda.")
            now = _now()
            conn.execute(
                "INSERT INTO users (username, name, email, password_hash, status, role, must_change_pw, "
                "created_at, decided_at, decided_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (username, (name or username).strip(), email, password_hash, ACTIVE, role,
                 int(must_change_pw), now, now, actor),
            )
            _audit(conn, username, "create", actor, f"rol={role}")


# ------------------------------------------------------------------ yönetim

def _decide(username: str, actor: str, allowed_from: tuple, new_status: str, action: str, note: str = "") -> dict:
    username = normalize_username(username)
    with _conn() as conn:
        _ensure(conn)
        with storage.write_transaction(conn):
            user = _require_user(conn, username)
            if user["status"] not in allowed_from:
                raise RegistryError(
                    f"'{username}' durumu '{STATUS_LABELS[user['status']]}'; bu işlem yapılamaz."
                )
            if new_status != ACTIVE and user["role"] == ADMIN and user["status"] == ACTIVE:
                _guard_last_admin(conn, username)
            conn.execute(
                "UPDATE users SET status = ?, decided_at = ?, decided_by = ?, note = ? WHERE username = ?",
                (new_status, _now(), actor, note or user["note"], username),
            )
            _audit(conn, username, action, actor, note)
            return _select_user(conn, username)


def _guard_last_admin(conn, username: str) -> None:
    others = conn.execute(
        "SELECT COUNT(*) FROM users WHERE role = ? AND status = ? AND username != ?", (ADMIN, ACTIVE, username),
    ).fetchone()[0]
    if others == 0:
        raise RegistryError("Son aktif yönetici bu işlemle kaldırılamaz; önce başka bir yönetici atayın.")


def approve(username: str, actor: str) -> dict:
    return _decide(username, actor, (PENDING, REJECTED), ACTIVE, "approve")


def reject(username: str, actor: str, note: str = "") -> dict:
    return _decide(username, actor, (PENDING,), REJECTED, "reject", note)


def disable(username: str, actor: str, note: str = "") -> dict:
    if normalize_username(username) == actor:
        raise RegistryError("Kendi hesabınızı devre dışı bırakamazsınız.")
    return _decide(username, actor, (ACTIVE,), DISABLED, "disable", note)


def enable(username: str, actor: str) -> dict:
    return _decide(username, actor, (DISABLED,), ACTIVE, "enable")


def set_role(username: str, role: str, actor: str) -> None:
    username = normalize_username(username)
    if role not in (ADMIN, USER):
        raise RegistryError(f"Geçersiz rol: {role}")
    with _conn() as conn:
        _ensure(conn)
        with storage.write_transaction(conn):
            user = _require_user(conn, username)
            if user["role"] == role:
                return
            if user["role"] == ADMIN and user["status"] == ACTIVE:
                _guard_last_admin(conn, username)
            conn.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
            _audit(conn, username, "role", actor, role)


def update_profile(username: str, name: str, email: str, actor: str) -> None:
    username = normalize_username(username)
    name = (name or "").strip()
    email = normalize_email(email)
    if not name:
        raise RegistryError("Ad Soyad boş olamaz.")
    validate_email(email)
    with _conn() as conn:
        _ensure(conn)
        with storage.write_transaction(conn):
            _require_user(conn, username)
            if conn.execute("SELECT 1 FROM users WHERE email = ? AND username != ?", (email, username)).fetchone():
                raise RegistryError("Bu e-posta adresi başka bir hesapta.")
            conn.execute("UPDATE users SET name = ?, email = ? WHERE username = ?", (name, email, username))
            _audit(conn, username, "profile", actor)


def _store_password(username: str, password_hash: str, must_change: bool, actor: str, action: str) -> None:
    with _conn() as conn:
        _ensure(conn)
        with storage.write_transaction(conn):
            _require_user(conn, username)
            conn.execute(
                "UPDATE users SET password_hash = ?, must_change_pw = ? WHERE username = ?",
                (password_hash, int(must_change), username),
            )
            _audit(conn, username, action, actor)


def change_password(username: str, current: str, new: str, confirm: str) -> None:
    """Kullanıcının kendi şifresini değiştirmesi - mevcut şifre doğrulanır."""
    username = normalize_username(username)
    user = get_user(username)
    if user is None or not check_password(current, user["password_hash"]):
        raise RegistryError("Mevcut şifre yanlış.")
    validate_password(new, confirm)
    if new == current:
        raise RegistryError("Yeni şifre eskisiyle aynı olamaz.")
    _store_password(username, hash_password(new), False, username, "password_change")


def set_password(username: str, password: str, actor: str, must_change: bool = False) -> None:
    """Yönetici / script tarafından şifre atama (mevcut şifre sorulmaz)."""
    username = normalize_username(username)
    validate_password(password)
    _store_password(username, hash_password(password), must_change, actor, "password_set")


def reset_password(username: str, actor: str) -> str:
    """Geçici şifre üretir ve döner; kullanıcı ilk girişte değiştirmek zorunda kalır."""
    temp = generate_password()
    _store_password(normalize_username(username), hash_password(temp), True, actor, "password_reset")
    return temp


def verify_password(username: str, password: str) -> bool:
    user = get_user(username)
    return bool(user and check_password(password, user["password_hash"]))


def record_login(username: str) -> None:
    with _conn() as conn:
        _ensure(conn)
        with storage.write_transaction(conn):
            conn.execute("UPDATE users SET last_login_at = ? WHERE username = ?", (_now(), normalize_username(username)))


def log_event(username: str, action: str, actor: str, detail: str = "") -> None:
    """Kayıt dışı olayları (ör. şifre sıfırlama talebi) denetim kaydına yazar."""
    with _conn() as conn:
        _ensure(conn)
        with storage.write_transaction(conn):
            _audit(conn, normalize_username(username), action, actor, detail)


def delete_user(username: str, actor: str, purge_data: bool = True) -> int:
    """Kullanıcıyı siler; purge_data ise `settings` kayıtlarını da (geçmiş tablosu
    denetim için kalır). Silinen ayar sayısını döner."""
    username = normalize_username(username)
    if username == actor:
        raise RegistryError("Kendi hesabınızı silemezsiniz.")
    with _conn() as conn:
        _ensure(conn)
        with storage.write_transaction(conn):
            user = _require_user(conn, username)
            if user["role"] == ADMIN and user["status"] == ACTIVE:
                _guard_last_admin(conn, username)
            conn.execute("DELETE FROM users WHERE username = ?", (username,))
            purged = 0
            if purge_data:
                purged = conn.execute("DELETE FROM settings WHERE username = ?", (username,)).rowcount
            _audit(conn, username, "delete", actor, f"{purged} ayar kaydı silindi" if purge_data else "veri korundu")
    return purged


# ------------------------------------------------------------------ davet kodları

def create_invite(actor: str, max_uses: int = 1, valid_days: int | None = 7) -> str:
    if max_uses < 1:
        raise RegistryError("Kullanım hakkı en az 1 olmalı.")
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))
    expires = None
    if valid_days:
        expires = (datetime.now(timezone.utc) + timedelta(days=valid_days)).isoformat(timespec="seconds")
    with _conn() as conn:
        _ensure(conn)
        with storage.write_transaction(conn):
            conn.execute(
                "INSERT INTO invites (code, created_by, created_at, max_uses, expires_at) VALUES (?, ?, ?, ?, ?)",
                (code, actor, _now(), max_uses, expires),
            )
            _audit(conn, "-", "invite_create", actor, f"{code} ({max_uses} kullanım)")
    return code


def list_invites() -> list[dict]:
    now = _now()
    with _conn() as conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT code, created_by, created_at, max_uses, used, expires_at, revoked FROM invites "
            "ORDER BY created_at DESC"
        ).fetchall()
    keys = ("code", "created_by", "created_at", "max_uses", "used", "expires_at", "revoked")
    return [dict(zip(keys, r), usable=_invite_usable(r, now)) for r in rows]


def revoke_invite(code: str, actor: str) -> None:
    with _conn() as conn:
        _ensure(conn)
        with storage.write_transaction(conn):
            if conn.execute("UPDATE invites SET revoked = 1 WHERE code = ?", (code,)).rowcount == 0:
                raise RegistryError("Davet kodu bulunamadı.")
            _audit(conn, "-", "invite_revoke", actor, code)
