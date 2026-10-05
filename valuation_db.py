"""
Değerleme & Ucuzluk Skoru sonuçlarının veritabanı tablosu.

Her piyasa servisi (bkz. valuation_service.py) kendi evrenindeki hisselerin ham
değerleme verisini ve hesaplanan skorunu buraya yazar; arayüz kullanıcının seçtiği
portföyü buradan okur. Tablo, storage.py ile aynı SQLite dosyasındadır
(YATIRIM_DB_PATH; tanımlı değilse repo içindeki data/yatirim.db).

Bir satırın anahtarı (piyasa, hisse) ikilisidir: aynı hisse iki piyasanın
evreninde olabilir ve skoru (alt sektör medyanı) o piyasanın akranlarına göre
hesaplandığı için ayrı tutulur. Yeniden çekimde satırın üzerine yazılır.

Zamanlar UTC, ISO 8601 metni ("2026-10-05T21:30:12Z").
"""

import json
from datetime import datetime, timedelta, timezone

import storage

SOURCE_SERVICE = "service"      # piyasa listesinden veya piyasaya bağlı bir kullanıcı grubundan
SOURCE_ON_DEMAND = "on_demand"  # kullanıcı seçtiğinde veritabanında yoktu, arayüz anlık çekti

_SCHEMA = """
CREATE TABLE IF NOT EXISTS valuation_scores (
    market     TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    raw        TEXT NOT NULL,
    scored     TEXT NOT NULL,
    score      INTEGER,
    fetched_at TEXT NOT NULL,
    scored_at  TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'service',
    PRIMARY KEY (market, ticker)
);
CREATE INDEX IF NOT EXISTS valuation_scores_ticker ON valuation_scores (ticker, fetched_at);
CREATE TABLE IF NOT EXISTS valuation_runs (
    market        TEXT PRIMARY KEY,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    universe_size INTEGER,
    fetched       INTEGER,
    reused        INTEGER,
    failed        INTEGER,
    removed       INTEGER,
    aborted       INTEGER NOT NULL DEFAULT 0,
    note          TEXT
);
CREATE TABLE IF NOT EXISTS valuation_cycles (
    market     TEXT PRIMARY KEY,
    state      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_initialized_paths = set()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text):
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _connect():
    path = storage.db_path()
    if path not in _initialized_paths:
        with storage.connection() as conn:
            conn.executescript(_SCHEMA)
        _initialized_paths.add(path)
    return storage.connection()


def _row_to_dict(row):
    market, ticker, raw, scored, score, fetched_at, scored_at, source = row
    return {
        "market": market,
        "ticker": ticker,
        "raw": json.loads(raw),
        "scored": json.loads(scored),
        "score": score,
        "fetched_at": fetched_at,
        "scored_at": scored_at,
        "source": source,
    }


_COLUMNS = "market, ticker, raw, scored, score, fetched_at, scored_at, source"


def get_rows(market: str, tickers=None) -> dict:
    """Piyasanın satırları: {hisse: satır}. `tickers` verilirse yalnızca onlar."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM valuation_scores WHERE market = ?", (market,)
        ).fetchall()
    result = {r[1]: _row_to_dict(r) for r in rows}
    if tickers is not None:
        wanted = set(tickers)
        result = {t: r for t, r in result.items() if t in wanted}
    return result


def get_recent_raw(tickers, max_age_hours: float, exclude_market=None, now=None) -> dict:
    """Herhangi bir piyasada son `max_age_hours` saat içinde çekilmiş ham veri:
    {hisse: (ham_veri, fetched_at)} - aynı hisse iki piyasanın evrenindeyse Yahoo'ya
    ikinci kez gitmemek için. Birden fazla varsa en yenisi."""
    tickers = list(dict.fromkeys(tickers))
    if not tickers:
        return {}
    cutoff = to_iso((now or utc_now()) - timedelta(hours=max_age_hours))
    result = {}
    with _connect() as conn:
        for start in range(0, len(tickers), 500):  # SQLite parametre sınırı
            chunk = tickers[start:start + 500]
            placeholders = ",".join("?" * len(chunk))
            params = list(chunk) + [cutoff]
            sql = (f"SELECT ticker, raw, fetched_at FROM valuation_scores "
                   f"WHERE ticker IN ({placeholders}) AND fetched_at >= ?")
            if exclude_market is not None:
                sql += " AND market != ?"
                params.append(exclude_market)
            for ticker, raw, fetched_at in conn.execute(sql, params):
                if ticker not in result or fetched_at > result[ticker][1]:
                    result[ticker] = (json.loads(raw), fetched_at)
    return result


def delete_rows(market: str, tickers) -> int:
    tickers = list(tickers)
    if not tickers:
        return 0
    with _connect() as conn, storage.write_transaction(conn):
        deleted = 0
        for start in range(0, len(tickers), 500):
            chunk = tickers[start:start + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = conn.execute(
                f"DELETE FROM valuation_scores WHERE market = ? AND ticker IN ({placeholders})",
                [market] + chunk,
            )
            deleted += cur.rowcount
    return deleted


def upsert_rows(rows) -> None:
    """Satırları tek bir işlemde yazar; (piyasa, hisse) varsa üzerine yazar.
    Her satır: market, ticker, raw, scored, fetched_at, scored_at, source."""
    rows = list(rows)
    if not rows:
        return
    params = [
        (
            r["market"], r["ticker"],
            json.dumps(r["raw"], ensure_ascii=False),
            json.dumps(r["scored"], ensure_ascii=False),
            r["scored"].get("Nihai Skor"),
            r["fetched_at"], r["scored_at"], r.get("source", SOURCE_SERVICE),
        )
        for r in rows
    ]
    with _connect() as conn, storage.write_transaction(conn):
        conn.executemany(
            f"""INSERT INTO valuation_scores ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (market, ticker) DO UPDATE SET
                    raw = excluded.raw, scored = excluded.scored, score = excluded.score,
                    fetched_at = excluded.fetched_at, scored_at = excluded.scored_at,
                    source = excluded.source""",
            params,
        )


def record_run(market: str, **info) -> None:
    cols = ["market", "started_at", "finished_at", "universe_size", "fetched",
            "reused", "failed", "removed", "aborted", "note"]
    values = [market] + [info.get(c) for c in cols[1:]]
    values[cols.index("aborted")] = 1 if info.get("aborted") else 0
    with _connect() as conn, storage.write_transaction(conn):
        conn.execute(
            f"INSERT OR REPLACE INTO valuation_runs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            values,
        )


def get_run(market: str):
    with _connect() as conn:
        cur = conn.execute("SELECT * FROM valuation_runs WHERE market = ?", (market,))
        row = cur.fetchone()
        if row is None:
            return None
        names = [d[0] for d in cur.description]
    return dict(zip(names, row))


# --------------------------------------------------------------------------
# Paket paket ilerleyen (örn. Russell 2000: saatte 1 paket) servis döngüsünün
# ara durumu: evren, bekleyen hisseler ve o ana kadar çekilen ham veriler.
# Skorlar döngü bitince hesaplanıp valuation_scores'a yazılır, kayıt silinir.
# --------------------------------------------------------------------------

def get_cycle(market: str):
    with _connect() as conn:
        row = conn.execute("SELECT state FROM valuation_cycles WHERE market = ?", (market,)).fetchone()
    return json.loads(row[0]) if row else None


def save_cycle(market: str, state: dict, now=None) -> None:
    with _connect() as conn, storage.write_transaction(conn):
        conn.execute(
            "INSERT OR REPLACE INTO valuation_cycles (market, state, updated_at) VALUES (?, ?, ?)",
            (market, json.dumps(state, ensure_ascii=False), to_iso(now or utc_now())),
        )


def delete_cycle(market: str) -> None:
    with _connect() as conn, storage.write_transaction(conn):
        conn.execute("DELETE FROM valuation_cycles WHERE market = ?", (market,))
