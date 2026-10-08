"""
Değerleme & Ucuzluk Skoru sonuçlarının veritabanı tablosu.

Her piyasa servisi (bkz. valuation_service.py) kendi evrenindeki hisselerin ham
değerleme verisini ve hesaplanan skorunu buraya yazar; arayüz kullanıcının seçtiği
portföyü buradan okur. Tablo, storage.py ile aynı SQLite dosyasındadır
(YATIRIM_DB_PATH; tanımlı değilse repo içindeki data/yatirim.db).

Bir satırın anahtarı (piyasa, hisse) ikilisidir: aynı hisse iki piyasanın
evreninde olabilir ve skoru (alt sektör medyanı) o piyasanın akranlarına göre
hesaplandığı için ayrı tutulur. Yeniden çekimde satırın üzerine yazılır.

Günlük geçmiş (valuation_scores_daily): upsert_rows her yazımda skoru ayrıca
(piyasa, hisse, gün) anahtarıyla bu tabloya da yazar - aynı gün tekrar
skorlanırsa o günün satırı güncellenir, önceki günler değişmez. Gün, skorun
hesaplandığı anın piyasanın yerel saatindeki tarihidir (ABD servisleri
kapanıştan sonra çalıştığı için o işlem günü). Evrenden çıkan hissenin geçmişi
silinmez. Tablo ilk oluşturulduğunda valuation_scores'taki mevcut skorlar ilk
gün olarak aktarılır. Yahoo geçmiş temel veriyi vermediği için geçmiş, servis
çalıştıkça birikir (yapay zeka veri seti bkz. ai_dataset.py). Servisin
yazmadığı geçmiş günler valuation_history.py ile Yahoo'nun geçmiş bilanço
tablolarından yeniden hesaplanıp `reconstructed` kaynağıyla yazılabilir; bu
satırlar servis / seed satırlarının üzerine hiçbir zaman yazılmaz, servis ise
aynı günü skorlarsa reconstructed satırın üzerine yazar.

Zamanlar UTC, ISO 8601 metni ("2026-10-05T21:30:12Z").
"""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import storage

SOURCE_SERVICE = "service"      # piyasa listesinden veya piyasaya bağlı bir kullanıcı grubundan
SOURCE_ON_DEMAND = "on_demand"  # kullanıcı seçtiğinde veritabanında yoktu, arayüz anlık çekti
SOURCE_SEED = "seed"            # günlük geçmiş tablosu oluşturulurken valuation_scores'tan aktarıldı
SOURCE_RECONSTRUCTED = "reconstructed"  # geçmiş bilançolardan yeniden hesaplandı (valuation_history.py)

DAILY_TABLE = "valuation_scores_daily"
# Günlük geçmişin tarihi bu saat diliminde alınır (listede yoksa New York).
MARKET_TZ = {"BIST 100": "Europe/Istanbul"}
DEFAULT_MARKET_TZ = "America/New_York"

# Geçmiş tablosunda ayrı sütun olarak tutulan skor tablosu alanları (geri kalanı
# `scored` JSON'unda).
DAILY_FIELDS = {
    "score": "Nihai Skor",
    "sector_discount_pct": "Alt Sektör İskontosu %",
    "pe": "F/K",
    "sector_pe": "Alt Sektör Ort. F/K",
    "peg": "PEG",
    "sector": "Ana Sektör",
    "sub_sector": "Alt Sektör (İş Modeli)",
}

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
CREATE TABLE IF NOT EXISTS valuation_scores_daily (
    market              TEXT NOT NULL,
    ticker              TEXT NOT NULL,
    date                TEXT NOT NULL,
    score               INTEGER,
    sector_discount_pct REAL,
    pe                  REAL,
    sector_pe           REAL,
    peg                 REAL,
    sector              TEXT,
    sub_sector          TEXT,
    scored              TEXT NOT NULL,
    fetched_at          TEXT NOT NULL,
    scored_at           TEXT NOT NULL,
    source              TEXT NOT NULL,
    PRIMARY KEY (market, ticker, date)
);
CREATE INDEX IF NOT EXISTS valuation_scores_daily_ticker ON valuation_scores_daily (ticker, date);
CREATE TABLE IF NOT EXISTS valuation_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
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
            _rescore(conn)
            _seed_daily(conn)
        _initialized_paths.add(path)
    return storage.connection()


def market_date(market: str, iso_text: str) -> str:
    """UTC zaman damgasının piyasanın yerel saatindeki tarihi (YYYY-MM-DD)."""
    dt = parse_iso(iso_text) or utc_now()
    return dt.astimezone(ZoneInfo(MARKET_TZ.get(market, DEFAULT_MARKET_TZ))).strftime("%Y-%m-%d")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _daily_params(market, ticker, scored: dict, fetched_at, scored_at, source, date=None):
    """Günlük geçmiş satırı - ETF / skorlanmayan kayıt için None."""
    if scored.get("_excluded") or scored.get("Nihai Skor") is None:  # valuation.EXCLUDED_KEY
        return None
    values = []
    for col, key in DAILY_FIELDS.items():
        v = scored.get(key)
        values.append(v if col in ("sector", "sub_sector") else _num(v))
    return (market, ticker, date or market_date(market, scored_at), *values,
            json.dumps(scored, ensure_ascii=False), fetched_at, scored_at, source)


_DAILY_COLS = ("market", "ticker", "date") + tuple(DAILY_FIELDS) + ("scored", "fetched_at", "scored_at", "source")
_DAILY_INSERT = f"INSERT INTO {DAILY_TABLE} ({', '.join(_DAILY_COLS)}) VALUES ({', '.join('?' * len(_DAILY_COLS))})"
# Aynı gün tekrar skorlanırsa günün satırı son skorla güncellenir.
_DAILY_UPSERT = (f"{_DAILY_INSERT} ON CONFLICT (market, ticker, date) DO UPDATE SET "
                 + ", ".join(f"{c} = excluded.{c}" for c in _DAILY_COLS[3:]))


def _rescore(conn) -> None:
    """Puanlama kuralları değiştiyse (valuation_rules.SCORE_VERSION) kayıtlı
    skorları saklanan kriter değerlerinden bir kez yeniden hesaplar - hem anlık
    tabloda hem günlük geçmişte. Ham veri (raw) değişmez."""
    import valuation_rules

    row = conn.execute("SELECT value FROM valuation_meta WHERE key = 'score_version'").fetchone()
    if row and int(row[0]) >= valuation_rules.SCORE_VERSION:
        return
    with storage.write_transaction(conn):
        for table, keys in (("valuation_scores", ("market", "ticker")),
                            (DAILY_TABLE, ("market", "ticker", "date"))):
            updates = []
            for rec in conn.execute(f"SELECT {', '.join(keys)}, scored FROM {table}").fetchall():
                scored = json.loads(rec[-1])
                if scored.get("_excluded") or scored.get("Nihai Skor") is None:
                    continue
                scored["Nihai Skor"] = valuation_rules.score_row(scored)
                updates.append((scored["Nihai Skor"], json.dumps(scored, ensure_ascii=False)) + tuple(rec[:-1]))
            where = " AND ".join(f"{k} = ?" for k in keys)
            conn.executemany(f"UPDATE {table} SET score = ?, scored = ? WHERE {where}", updates)
        conn.execute("INSERT OR REPLACE INTO valuation_meta (key, value) VALUES ('score_version', ?)",
                     (str(valuation_rules.SCORE_VERSION),))


def _seed_daily(conn) -> None:
    """Geçmiş tablosu boşsa valuation_scores'taki mevcut skorları ilk gün olarak aktarır."""
    if conn.execute(f"SELECT 1 FROM {DAILY_TABLE} LIMIT 1").fetchone():
        return
    rows = conn.execute("SELECT market, ticker, scored, fetched_at, scored_at FROM valuation_scores").fetchall()
    params = [p for p in (_daily_params(m, t, json.loads(sc), f, sa, SOURCE_SEED) for m, t, sc, f, sa in rows) if p]
    if params:
        with storage.write_transaction(conn):
            conn.executemany(f"{_DAILY_INSERT} ON CONFLICT (market, ticker, date) DO NOTHING", params)


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
    daily = [p for p in (_daily_params(r["market"], r["ticker"], r["scored"], r["fetched_at"], r["scored_at"],
                                       r.get("source", SOURCE_SERVICE)) for r in rows) if p]
    with _connect() as conn, storage.write_transaction(conn):
        conn.executemany(_DAILY_UPSERT, daily)
        conn.executemany(
            f"""INSERT INTO valuation_scores ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (market, ticker) DO UPDATE SET
                    raw = excluded.raw, scored = excluded.scored, score = excluded.score,
                    fetched_at = excluded.fetched_at, scored_at = excluded.scored_at,
                    source = excluded.source""",
            params,
        )


def upsert_reconstructed(market: str, ticker: str, days) -> int:
    """Yeniden hesaplanmış günler: [(gün 'YYYY-MM-DD', skor tablosu satırı)].
    Yalnızca boş günlere ya da yine reconstructed olan günlere yazar. Yazılan
    satır sayısını döner."""
    now = to_iso(utc_now())
    params = [p for p in (_daily_params(market, ticker, scored, now, now, SOURCE_RECONSTRUCTED, date=day)
                          for day, scored in days) if p]
    if not params:
        return 0
    sql = (_DAILY_UPSERT + f" WHERE {DAILY_TABLE}.source = '{SOURCE_RECONSTRUCTED}'")
    with _connect() as conn, storage.write_transaction(conn):
        before = conn.total_changes
        conn.executemany(sql, params)
        return conn.total_changes - before


def get_daily_history(market: str, ticker: str, start: str | None = None, end: str | None = None) -> list[dict]:
    """Hissenin günlük skor geçmişi (eskiden yeniye). Her satır: date, score,
    sector_discount_pct, pe, sector_pe, peg, sector, sub_sector, scored (tam
    skor tablosu satırı), fetched_at, scored_at, source."""
    where, params = ["market = ?", "ticker = ?"], [market, ticker]
    if start:
        where.append("date >= ?")
        params.append(start)
    if end:
        where.append("date <= ?")
        params.append(end)
    with _connect() as conn:
        rows = conn.execute(f"SELECT {', '.join(_DAILY_COLS[2:])} FROM {DAILY_TABLE} "
                            f"WHERE {' AND '.join(where)} ORDER BY date", params).fetchall()
    out = []
    for row in rows:
        rec = dict(zip(_DAILY_COLS[2:], row))
        rec["scored"] = json.loads(rec["scored"])
        out.append(rec)
    return out


def daily_summary(market: str | None = None) -> list[dict]:
    """Piyasa bazında geçmiş: hisse sayısı, gün sayısı, ilk / son gün."""
    sql = (f"SELECT market, COUNT(DISTINCT ticker), COUNT(DISTINCT date), MIN(date), MAX(date) FROM {DAILY_TABLE}"
           + (" WHERE market = ?" if market else "") + " GROUP BY market ORDER BY market")
    with _connect() as conn:
        rows = conn.execute(sql, (market,) if market else ()).fetchall()
    return [{"market": m, "tickers": t, "days": d, "first": f, "last": la} for m, t, d, f, la in rows]


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
