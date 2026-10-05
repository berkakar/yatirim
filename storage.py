"""
Uygulamanın kalıcı verisi (kullanıcı ayarları, strateji state'leri, önbellekler)
için SQLite tabanlı depolama.

Repodaki JSON dosyalarının yerini alır: her JSON dosyası `settings` tablosunda
bir satırdır. Satırın anahtarı (username, name) ikilisidir:

    selected_tickers_berkakar.json  ->  username="berkakar", name="selected_tickers"
    tefas_fonlari_cache.json        ->  username="_shared",  name="tefas_fonlari_cache"

Değer, JSON dosyasının içeriğiyle aynıdır (json.dumps ile metin olarak saklanır),
yani çağıran kod yine dict/list ile çalışır.

Veritabanı dosyasının yeri YATIRIM_DB_PATH ortam değişkeniyle belirlenir
(Droplet'te /var/lib/yatirim/yatirim.db, /etc/yatirim/env içinde - bkz. deploy/README.md). Değişken
yoksa repo içindeki data/yatirim.db kullanılır (data/ .gitignore'da).

Arayüz (Streamlit) ve zamanlanmış işler aynı dosyaya aynı anda yazabilir:
- WAL modu: yazma sürerken okumalar beklemez.
- timeout: yazmalar sırayla yapılır, sıra gelene kadar en fazla BUSY_TIMEOUT_S beklenir.
- update(): oku-değiştir-yaz işlemini tek bir kilitli işlemde yapar; iki süreç
  aynı anda güncellerse biri diğerinin değişikliğini ezmez.
"""

import copy
import json
import os
import sqlite3
import threading
from contextlib import contextmanager

SHARED = "_shared"
BUSY_TIMEOUT_S = 30

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "yatirim.db")

# Her ayar için saklanan en fazla eski değer sayısı (settings_history).
HISTORY_LIMIT = 50

# Sık yenilenen ve kaybı sorun olmayan veriler için geçmiş tutulmaz; aksi halde
# birkaç dakikada bir yenilenen fiyat önbellekleri tabloyu gereksiz büyütür.
_NO_HISTORY_NAMES = {"bildirim_durumu", "backtest_results"}


def _keeps_history(name: str) -> bool:
    return not name.endswith("_cache") and name not in _NO_HISTORY_NAMES


_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    username   TEXT NOT NULL,
    name       TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (username, name)
);
CREATE TABLE IF NOT EXISTS settings_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL,
    name       TEXT NOT NULL,
    value      TEXT NOT NULL,
    changed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS settings_history_key ON settings_history (username, name, id);
"""

_initialized_paths = set()
_init_lock = threading.Lock()


def enabled() -> bool:
    """Uygulama verisi SQLite'tan mı okunup yazılsın? Yalnızca YATIRIM_DB_PATH
    tanımlıysa evet. Tanımlı değilse eski düzen (GitHub API + repo içindeki JSON
    dosyaları) aynen geçerlidir; böylece bu kod main'e alındığında, Droplet'teki
    servislere ortam değişkeni eklenene kadar hiçbir davranış değişmez."""
    return bool(os.environ.get("YATIRIM_DB_PATH"))


def db_path() -> str:
    return os.environ.get("YATIRIM_DB_PATH") or DEFAULT_DB_PATH


def _ensure_schema(path: str) -> None:
    if path in _initialized_paths:
        return
    with _init_lock:
        if path in _initialized_paths:
            return
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
        finally:
            conn.close()
        _initialized_paths.add(path)


@contextmanager
def _connect():
    """Her işlem için kısa ömürlü bir bağlantı açar. Streamlit her oturumu ayrı
    bir thread'de çalıştırdığı için bağlantıları paylaşmak yerine böyle yapılır;
    SQLite'ta bağlantı açmak ucuzdur."""
    path = db_path()
    _ensure_schema(path)
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_S, isolation_level=None)
    try:
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_S * 1000}")
        yield conn
    finally:
        conn.close()


@contextmanager
def _write_transaction(conn):
    # IMMEDIATE: yazma kilidi işlemin başında alınır; böylece okuma ile yazma
    # arasında başka bir süreç araya giremez.
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def connection():
    """Aynı veritabanı dosyasında kendi tablolarını tutan modüller (örn.
    valuation_db) için kısa ömürlü bağlantı - `with storage.connection() as conn:`."""
    return _connect()


def write_transaction(conn):
    """`connection()` ile açılan bağlantıda kilitli (BEGIN IMMEDIATE) yazma işlemi."""
    return _write_transaction(conn)


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _user(username) -> str:
    return username if username else SHARED


def _select_value(conn, username: str, name: str):
    row = conn.execute(
        "SELECT value FROM settings WHERE username = ? AND name = ?", (username, name)
    ).fetchone()
    return row[0] if row else None


def _store(conn, username: str, name: str, text: str) -> None:
    old = _select_value(conn, username, name)
    if old == text:
        return
    if old is not None and _keeps_history(name):
        conn.execute(
            "INSERT INTO settings_history (username, name, value) VALUES (?, ?, ?)",
            (username, name, old),
        )
        conn.execute(
            """DELETE FROM settings_history
               WHERE username = ? AND name = ? AND id NOT IN (
                   SELECT id FROM settings_history WHERE username = ? AND name = ?
                   ORDER BY id DESC LIMIT ?)""",
            (username, name, username, name, HISTORY_LIMIT),
        )
    conn.execute(
        """INSERT INTO settings (username, name, value) VALUES (?, ?, ?)
           ON CONFLICT (username, name) DO UPDATE SET
               value = excluded.value,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')""",
        (username, name, text),
    )


def read(name: str, username: str = SHARED, default=None):
    """Kaydı okur. Kayıt yoksa `default`un bir kopyasını döner (çağıran kod
    dönen değeri değiştirse bile varsayılan nesne bozulmaz)."""
    with _connect() as conn:
        text = _select_value(conn, _user(username), name)
    if text is None:
        return copy.deepcopy(default)
    return json.loads(text)


def write(name: str, username: str, value) -> None:
    """Kaydı (username, name) için yazar; varsa üzerine yazar.
    Ortak veriler için username=storage.SHARED (veya None) verin."""
    text = _dumps(value)
    with _connect() as conn, _write_transaction(conn):
        _store(conn, _user(username), name, text)


def update(name: str, username: str, fn, default=None):
    """Oku-değiştir-yaz işlemini tek bir kilitli işlemde yapar ve yeni değeri döner.

    `fn` mevcut değeri (yoksa `default`un kopyasını) alır, yeni değeri döner.
    İşlem boyunca yazma kilidi tutulduğu için aynı kayda aynı anda yazan başka
    bir süreç (örn. arayüz ile trailing stop işi) bu değişikliği ezemez.
    `fn` içinde ağ isteği gibi uzun işler yapmayın; kilit o süre boyunca tutulur.
    """
    username = _user(username)
    with _connect() as conn, _write_transaction(conn):
        text = _select_value(conn, username, name)
        current = json.loads(text) if text is not None else copy.deepcopy(default)
        new_value = fn(current)
        _store(conn, username, name, _dumps(new_value))
    return new_value


def delete(name: str, username: str = SHARED) -> bool:
    """Kaydı siler. Silinecek kayıt varsa True döner. Son değer geçmişe yazılır."""
    username = _user(username)
    with _connect() as conn, _write_transaction(conn):
        old = _select_value(conn, username, name)
        if old is None:
            return False
        if _keeps_history(name):
            conn.execute(
                "INSERT INTO settings_history (username, name, value) VALUES (?, ?, ?)",
                (username, name, old),
            )
        conn.execute("DELETE FROM settings WHERE username = ? AND name = ?", (username, name))
    return True


def exists(name: str, username: str = SHARED) -> bool:
    with _connect() as conn:
        return _select_value(conn, _user(username), name) is not None


def updated_at(name: str, username: str = SHARED):
    """Kaydın son güncellenme zamanı (UTC, ISO 8601 metni) veya kayıt yoksa None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT updated_at FROM settings WHERE username = ? AND name = ?", (_user(username), name)
        ).fetchone()
    return row[0] if row else None


def list_names(username: str = SHARED) -> list:
    """Kullanıcının (veya ortak alanın) kayıt adları, alfabetik."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT name FROM settings WHERE username = ? ORDER BY name", (_user(username),)
        ).fetchall()
    return [r[0] for r in rows]


def list_users() -> list:
    """Kaydı olan kullanıcılar (ortak alan hariç), alfabetik."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT username FROM settings WHERE username != ? ORDER BY username", (SHARED,)
        ).fetchall()
    return [r[0] for r in rows]


def users_with(name: str) -> list:
    """`name` kaydı olan kullanıcılar (ortak alan hariç), alfabetik - örn.
    users_with("takip_fonlari") fon takip listesi olan kullanıcıları verir."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT username FROM settings WHERE name = ? AND username != ? ORDER BY username", (name, SHARED)
        ).fetchall()
    return [r[0] for r in rows]


def history(name: str, username: str = SHARED, limit: int = HISTORY_LIMIT) -> list:
    """Kaydın eski değerleri, en yeniden eskiye: [{"changed_at": ..., "value": ...}, ...].
    `changed_at`, o değerin yerini yenisine bıraktığı zamandır."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT changed_at, value FROM settings_history
               WHERE username = ? AND name = ? ORDER BY id DESC LIMIT ?""",
            (_user(username), name, limit),
        ).fetchall()
    return [{"changed_at": changed_at, "value": json.loads(value)} for changed_at, value in rows]


def backup(dest_path: str) -> None:
    """Veritabanının tutarlı bir kopyasını `dest_path`e yazar. Çalışırken dosyayı
    `cp` ile kopyalamak yarım kalmış bir yazmayı yakalayabilir; SQLite'ın
    yedekleme API'si bunu önler."""
    directory = os.path.dirname(dest_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with _connect() as conn:
        dest = sqlite3.connect(dest_path)
        try:
            conn.backup(dest)
        finally:
            dest.close()


# --------------------------------------------------------------------------
# JSON dosya adı <-> kayıt eşlemesi
#
# Uygulamanın JSON dosyaları "<ad>_<kullanıcı>.json" ya da ortak veriler için
# "<ad>.json" adını taşır. Aşağıdaki listedeki adlar SQLite açıkken dosya yerine
# veritabanından okunup yazılır; listede olmayan bir dosyaya (örn.
# version_info.json) dokunulmaz, eski davranış sürer.
# --------------------------------------------------------------------------

KNOWN_NAMES = frozenset({
    # Arayüzün yazdığı ayarlar
    "selected_tickers", "custom_tickers", "custom_stock_groups", "custom_stock_group_markets",
    "initial_capital", "stop_loss_settings", "bildirim_ayarlari", "takip_fonlari", "alpaca_account_mode",
    # Strateji config'leri (arayüz + işler)
    "portfolio_config", "otomatik_alim_satim_config", "orb_scan_config",
    "relative_strength_config", "ha_intraday_config", "backtest_results",
    # Strateji state'leri (işler)
    "orb_scan_holdings", "relative_strength_holdings", "ha_intraday_holdings",
    "buy_stop_rebuy_state", "bildirim_durumu",
    # Önbellekler
    "alpaca_daily_bars_cache", "alpaca_intraday_bars_cache", "alpaca_realized_pnl_cache",
    "alpaca_position_management_cache", "tefas_fonlari_cache", "kap_portfoy_cache",
    "valuation_cache", "nasdaq_5m_cache", "dtw_results_cache", "hisse_patern_cache",
})

# En uzun ad önce denenir: "custom_stock_group_markets_x" -> "custom_stock_group_markets".
_NAMES_LONGEST_FIRST = sorted(KNOWN_NAMES, key=len, reverse=True)


def key_for_path(path: str):
    """Dosya yolunu (username, name) kaydına çevirir; bilinmeyen dosyalar için None.

        "orb_scan_holdings_berkakar.json" -> ("berkakar", "orb_scan_holdings")
        "tefas_fonlari_cache.json"         -> ("_shared", "tefas_fonlari_cache")
    """
    filename = os.path.basename(path)
    if not filename.endswith(".json"):
        return None
    stem = filename[: -len(".json")]
    for name in _NAMES_LONGEST_FIRST:
        if stem == name:
            return SHARED, name
        if stem.startswith(name + "_") and len(stem) > len(name) + 1:
            return stem[len(name) + 1:], name
    return None


def db_key(path: str):
    """SQLite açıksa ve dosya biliniyorsa kayıt anahtarı, aksi halde None (dosya kullanılır)."""
    if not enabled():
        return None
    return key_for_path(path)


def load_json(path: str, default=None):
    """JSON dosyasını okur: SQLite açıksa ve dosya biliniyorsa veritabanından,
    değilse diskten. Kayıt/dosya yoksa `default`un bir kopyasını döner.
    Dosya modunda bozuk JSON eskisi gibi hata fırlatır."""
    key = db_key(path)
    if key is not None:
        username, name = key
        return read(name, username, default)
    if not os.path.exists(path):
        return copy.deepcopy(default)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, value, indent: int = 2) -> None:
    """JSON dosyasını yazar: SQLite açıksa ve dosya biliniyorsa veritabanına,
    değilse diske (dosyanın repodaki biçimiyle: UTF-8, girintili)."""
    key = db_key(path)
    if key is not None:
        username, name = key
        write(name, username, value)
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=indent)


def json_exists(path: str) -> bool:
    key = db_key(path)
    if key is not None:
        username, name = key
        return exists(name, username)
    return os.path.exists(path)


def update_json(path: str, fn, default=None):
    """Oku-değiştir-yaz. SQLite açıksa storage.update ile kilitli tek işlemde
    (eşzamanlı başka bir iş araya giremez), değilse dosyada. Yeni değeri döner."""
    key = db_key(path)
    if key is not None:
        username, name = key
        return update(name, username, fn, default)
    new_value = fn(load_json(path, default))
    save_json(path, new_value)
    return new_value
