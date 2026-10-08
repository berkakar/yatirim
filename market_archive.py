"""
Piyasa Duyarlılığı ve ABD sektör ETF'lerinin günlük arşivi - yapay zeka /
makine öğrenmesi eğitimi için tasarlanmış, tarih bazlı düz tablolar.

market_sentiment.py'nin `market_sentiment_cache` / `market_sector_etfs`
kayıtları yalnızca son durumu tutar (her çalıştırmada üzerine yazılır). Bu modül
aynı SQLite dosyasında (storage.db_path(); YATIRIM_DB_PATH) iki tablo tutar:

    sentiment_daily   (market, date)  bir piyasanın o günkü bileşik skoru,
                                      5 bileşen skoru, ham değerleri, endeks
                                      kapanışı, put/call (yalnızca canlı satırda)
    sector_etf_daily  (symbol, date)  ETF kapanışı ve 1 / 5 / 21 işlem günlük
                                      % getiri, 5 günlük getirinin SPY'ye göre
                                      farkı (SPY de satır olarak tutulur)

Her satır bir (anahtar, işlem günü) - pandas ile doğrudan okunur, pivotlanır
ve tarih üzerinden birleştirilir (bkz. load_frame / feature_frame / export).

Satır kaynakları (`source` sütunu):
    live      Günlük servis o gün ne hesapladıysa / arayüzde ne gösterildiyse.
              Üzerine yalnızca yine canlı servis (aynı gün tekrar çalışırsa) yazar.
    backfill  Geçmiş fiyatlardan aynı formülle yeniden hesaplanan gün. Günlük
              servis her çalıştırmada eksik günleri bu şekilde doldurur (servis
              bir gün çalışmazsa boşluk kalmaz); `backfill` komutu 2 (veya N)
              yıllık geçmişi tek Yahoo indirmesiyle doldurur. Var olan satırlar
              değişmez - formül değiştiyse `--rebuild` yalnızca backfill
              satırlarını yeniden yazar, canlı satırlara dokunmaz.

Eğitimde dikkat:
- Geriye dönük genişlik (breadth, yeni zirve/dip) bugünkü hisse listesiyle
  hesaplanır (hayatta kalma yanlılığı); canlı satırlar o günün listesiyledir.
  `universe_size` ve `source` sütunları bunu ayırmak için tutulur.
- Bileşen skorları yüzdelik sıra olduğundan ilk ~1 yıl (252 gün) için boş
  olabilir; backfill indirmesi bu yüzden istenen yıldan 2 yıl uzun tutulur.
- `formula_version` hesaplama yöntemi değiştiğinde artırılır (bkz.
  market_sentiment.FORMULA_VERSION) - farklı sürümleri karıştırmamak için.
- Satırdaki değerler o günün kapanışıyla hesaplanır; ertesi günü tahmin eden
  bir modelde hedef değişken bir gün ileri kaydırılmalıdır (sızıntı olmasın).

Kullanım:
    python market_archive.py backfill                 # 2 yıl, tüm piyasalar + sektörler
    python market_archive.py backfill --years 3 --rebuild
    python market_archive.py export --out data/ml     # CSV (pyarrow varsa --format parquet)
    python market_archive.py status
"""

import argparse
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import storage

SOURCE_LIVE = "live"
SOURCE_BACKFILL = "backfill"
DEFAULT_BACKFILL_YEARS = 2

SENTIMENT_TABLE = "sentiment_daily"
SECTOR_TABLE = "sector_etf_daily"

# compute_components çıktısından arşivlenen sütunlar (aynı adla).
SENTIMENT_SERIES_COLS = (
    "score", "momentum", "volatility", "breadth", "highs_lows", "safe_haven",
    "momentum_raw", "volatility_raw", "volatility_vs_avg", "breadth200", "safe_haven_raw", "index_close",
)
SENTIMENT_COLS = SENTIMENT_SERIES_COLS + ("put_call_ratio", "put_call_score", "universe_size")
SECTOR_COLS = ("name", "close", "ret_1d", "ret_5d", "ret_21d", "rel_5d_vs_benchmark")

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {SENTIMENT_TABLE} (
    market            TEXT NOT NULL,
    date              TEXT NOT NULL,
    score             REAL,
    momentum          REAL,
    volatility        REAL,
    breadth           REAL,
    highs_lows        REAL,
    safe_haven        REAL,
    momentum_raw      REAL,
    volatility_raw    REAL,
    volatility_vs_avg REAL,
    breadth200        REAL,
    safe_haven_raw    REAL,
    index_close       REAL,
    put_call_ratio    REAL,
    put_call_score    REAL,
    universe_size     INTEGER,
    source            TEXT NOT NULL,
    formula_version   INTEGER NOT NULL,
    recorded_at       TEXT NOT NULL,
    PRIMARY KEY (market, date)
);
CREATE INDEX IF NOT EXISTS {SENTIMENT_TABLE}_date ON {SENTIMENT_TABLE} (date);
CREATE TABLE IF NOT EXISTS {SECTOR_TABLE} (
    symbol              TEXT NOT NULL,
    date                TEXT NOT NULL,
    name                TEXT,
    close               REAL,
    ret_1d              REAL,
    ret_5d              REAL,
    ret_21d             REAL,
    rel_5d_vs_benchmark REAL,
    source              TEXT NOT NULL,
    formula_version     INTEGER NOT NULL,
    recorded_at         TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS {SECTOR_TABLE}_date ON {SECTOR_TABLE} (date);
"""

_KEYS = {SENTIMENT_TABLE: ("market", "date"), SECTOR_TABLE: ("symbol", "date")}
_VALUE_COLS = {SENTIMENT_TABLE: SENTIMENT_COLS, SECTOR_TABLE: SECTOR_COLS}

_initialized_paths = set()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect():
    path = storage.db_path()
    if path not in _initialized_paths:
        with storage.connection() as conn:
            conn.executescript(_SCHEMA)
        _initialized_paths.add(path)
    return storage.connection()


def _clean(v):
    """NaN / inf / numpy tiplerini SQLite'a uygun hale getirir."""
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return float(v) if np.isfinite(v) else None
    return v


def _upsert(table: str, rows: list[dict], source: str, formula_version: int, overwrite_backfill=False) -> int:
    """Satırları yazar. live: aynı anahtardaki her satırın üzerine yazar.
    backfill: yalnızca eksik satırları ekler; overwrite_backfill=True ise var
    olan backfill satırlarını da günceller (canlı satırlar hiçbir zaman)."""
    if not rows:
        return 0
    keys, values = _KEYS[table], _VALUE_COLS[table]
    cols = keys + values + ("source", "formula_version", "recorded_at")
    placeholders = ", ".join("?" for _ in cols)
    update = ", ".join(f"{c} = excluded.{c}" for c in values + ("source", "formula_version", "recorded_at"))
    if source == SOURCE_LIVE:
        conflict = f"DO UPDATE SET {update}"
    elif overwrite_backfill:
        conflict = f"DO UPDATE SET {update} WHERE {table}.source != '{SOURCE_LIVE}'"
    else:
        conflict = "DO NOTHING"
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
           f"ON CONFLICT ({', '.join(keys)}) {conflict}")
    now = _now()
    params = [tuple(_clean(r.get(c)) for c in keys + values) + (source, int(formula_version), now) for r in rows]
    with _connect() as conn:
        with storage.write_transaction(conn):
            before = conn.total_changes
            conn.executemany(sql, params)
            return conn.total_changes - before


# ------------------------------------------------------------------------------
# Satır üretimi (saf fonksiyonlar)
# ------------------------------------------------------------------------------

def sentiment_rows(market: str, comp: pd.DataFrame, universe_size=None, put_call=None,
                   only_last=False) -> list[dict]:
    """compute_components çıktısından satırlar - skoru olmayan (ısınma) günler
    atlanır. put_call yalnızca son güne yazılır (geçmişi yok)."""
    valid = comp.dropna(subset=["score"])
    if only_last:
        valid = valid.tail(1)
    rows = []
    last_date = valid.index[-1] if len(valid) else None
    for date, rec in valid.iterrows():
        row = {"market": market, "date": pd.Timestamp(date).strftime("%Y-%m-%d"),
               "universe_size": universe_size}
        for c in SENTIMENT_SERIES_COLS:
            row[c] = rec.get(c)
        if put_call and date == last_date:
            row["put_call_ratio"] = put_call.get("ratio")
            row["put_call_score"] = put_call.get("score")
        rows.append(row)
    return rows


def sector_frame(closes: pd.DataFrame, names: dict, benchmark: str) -> pd.DataFrame:
    """Her ETF (ve benchmark) için günlük kapanış ve 1 / 5 / 21 işlem günlük %
    getiri, 5 günlük getirinin benchmark'a göre farkı - uzun (symbol, date)
    biçimde."""
    symbols = [s for s in list(names) + [benchmark] if s in closes.columns]
    frames = []
    bench_5d = closes[benchmark].dropna().pct_change(5) * 100 if benchmark in closes else None
    for sym in symbols:
        s = closes[sym].dropna()
        if s.empty:
            continue
        df = pd.DataFrame({"close": s})
        df["ret_1d"] = s.pct_change(1) * 100
        df["ret_5d"] = s.pct_change(5) * 100
        df["ret_21d"] = s.pct_change(21) * 100
        df["rel_5d_vs_benchmark"] = (df["ret_5d"] - bench_5d.reindex(df.index)) if bench_5d is not None else np.nan
        df["symbol"] = sym
        df["name"] = names.get(sym, "S&P 500 (karşılaştırma)" if sym == benchmark else sym)
        df["date"] = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in df.index]
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=("symbol", "date") + SECTOR_COLS)
    return pd.concat(frames, ignore_index=True)


def sector_rows(frame: pd.DataFrame, only_last=False) -> list[dict]:
    if frame.empty:
        return []
    if only_last:
        frame = frame[frame["date"] == frame["date"].max()]
    return frame.to_dict("records")


# ------------------------------------------------------------------------------
# Yazma
# ------------------------------------------------------------------------------

def record_sentiment(market: str, comp: pd.DataFrame, universe_size, put_call, formula_version: int) -> dict:
    """Günlük servis: son gün canlı satır (üzerine yazar), önceki günler eksikse backfill."""
    live = _upsert(SENTIMENT_TABLE, sentiment_rows(market, comp, universe_size, put_call, only_last=True),
                   SOURCE_LIVE, formula_version)
    filled = _upsert(SENTIMENT_TABLE, sentiment_rows(market, comp, universe_size), SOURCE_BACKFILL, formula_version)
    return {"live": live, "backfill": filled}


def record_sectors(frame: pd.DataFrame, formula_version: int) -> dict:
    live = _upsert(SECTOR_TABLE, sector_rows(frame, only_last=True), SOURCE_LIVE, formula_version)
    filled = _upsert(SECTOR_TABLE, sector_rows(frame), SOURCE_BACKFILL, formula_version)
    return {"live": live, "backfill": filled}


def backfill_sentiment(market: str, comp: pd.DataFrame, universe_size, formula_version: int,
                       since: str | None = None, rebuild=False) -> int:
    rows = [r for r in sentiment_rows(market, comp, universe_size) if since is None or r["date"] >= since]
    return _upsert(SENTIMENT_TABLE, rows, SOURCE_BACKFILL, formula_version, overwrite_backfill=rebuild)


def backfill_sectors(frame: pd.DataFrame, formula_version: int, since: str | None = None, rebuild=False) -> int:
    rows = [r for r in sector_rows(frame) if since is None or r["date"] >= since]
    return _upsert(SECTOR_TABLE, rows, SOURCE_BACKFILL, formula_version, overwrite_backfill=rebuild)


# ------------------------------------------------------------------------------
# Okuma / eğitim verisi
# ------------------------------------------------------------------------------

def load_frame(table: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Tablonun tamamı (veya tarih aralığı) - date sütunu datetime."""
    if table not in _KEYS:
        raise ValueError(f"bilinmeyen tablo: {table}")
    where, params = [], []
    if start:
        where.append("date >= ?")
        params.append(start)
    if end:
        where.append("date <= ?")
        params.append(end)
    sql = f"SELECT * FROM {table}" + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY date, " + _KEYS[table][0]
    with _connect() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    df["date"] = pd.to_datetime(df["date"])
    return df


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text.lower()).strip("_")


def feature_frame(start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Eğitim için geniş tablo: her satır bir işlem günü, sütunlar
    <piyasa>__<özellik> (ör. nasdaq_100__score) ve <etf>__<özellik>
    (ör. xlk__ret_5d). Piyasaların tatil günleri farklı olabileceğinden
    eksik hücreler NaN kalır."""
    parts = []
    sent = load_frame(SENTIMENT_TABLE, start, end)
    if not sent.empty:
        feats = [c for c in SENTIMENT_COLS if c != "universe_size"]
        wide = sent.pivot(index="date", columns="market", values=feats)
        wide.columns = [f"{_slug(m)}__{f}" for f, m in wide.columns]
        parts.append(wide)
    sec = load_frame(SECTOR_TABLE, start, end)
    if not sec.empty:
        feats = [c for c in SECTOR_COLS if c != "name"]
        wide = sec.pivot(index="date", columns="symbol", values=feats)
        wide.columns = [f"{_slug(s)}__{f}" for f, s in wide.columns]
        parts.append(wide)
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, axis=1, sort=True)
    out.index.name = "date"
    return out[sorted(out.columns)]


def export(out_dir: str, fmt: str = "csv", start: str | None = None, end: str | None = None) -> list[str]:
    """sentiment_daily, sector_etf_daily (uzun) ve features_daily (geniş)
    dosyalarını yazar. Yazılan yolları döner."""
    if fmt == "parquet":
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            raise RuntimeError("parquet için pyarrow gerekli (pip install pyarrow) - ya da --format csv")
    os.makedirs(out_dir, exist_ok=True)
    frames = {
        SENTIMENT_TABLE: load_frame(SENTIMENT_TABLE, start, end),
        SECTOR_TABLE: load_frame(SECTOR_TABLE, start, end),
        "features_daily": feature_frame(start, end).reset_index(),
    }
    paths = []
    for name, df in frames.items():
        path = os.path.join(out_dir, f"{name}.{fmt}")
        if fmt == "parquet":
            df.to_parquet(path, index=False)
        else:
            df.to_csv(path, index=False, date_format="%Y-%m-%d")
        paths.append(path)
    return paths


def summary() -> list[dict]:
    """Tablo bazında satır sayısı, tarih aralığı ve kaynak dağılımı."""
    out = []
    with _connect() as conn:
        for table, keys in _KEYS.items():
            for key, n, first, last, live in conn.execute(
                    f"SELECT {keys[0]}, COUNT(*), MIN(date), MAX(date), "
                    f"SUM(source = '{SOURCE_LIVE}') FROM {table} GROUP BY {keys[0]} ORDER BY {keys[0]}"):
                out.append({"table": table, "key": key, "rows": n, "first": first, "last": last, "live": live})
    return out


# ------------------------------------------------------------------------------
# Komut satırı
# ------------------------------------------------------------------------------

def main(argv=None):
    import market_sentiment as ms

    parser = argparse.ArgumentParser(description="Piyasa duyarlılığı / sektör ETF günlük arşivi")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill", help="geçmişi Yahoo'dan tek seferde hesaplayıp doldurur")
    b.add_argument("--years", type=int, default=DEFAULT_BACKFILL_YEARS)
    b.add_argument("--market", choices=sorted(ms.MARKETS), action="append",
                   help="yalnızca bu piyasa (tekrarlanabilir); verilmezse hepsi")
    b.add_argument("--no-sectors", action="store_true", help="sektör ETF'lerini atla")
    b.add_argument("--rebuild", action="store_true",
                   help="var olan backfill satırlarını da yeniden yaz (canlı satırlar korunur)")
    e = sub.add_parser("export", help="eğitim için CSV / Parquet dosyaları yazar")
    e.add_argument("--out", default=os.path.join("data", "ml"))
    e.add_argument("--format", choices=("csv", "parquet"), default="csv")
    e.add_argument("--start")
    e.add_argument("--end")
    sub.add_parser("status", help="arşivdeki satır sayıları ve tarih aralıkları")
    args = parser.parse_args(argv)

    if args.cmd == "backfill":
        failures = ms.backfill(args.market or list(ms.MARKETS), years=args.years,
                               sectors=not args.no_sectors, rebuild=args.rebuild)
        return 1 if failures else 0
    if args.cmd == "export":
        for path in export(args.out, args.format, args.start, args.end):
            print(path)
        return 0
    rows = summary()
    if not rows:
        print("Arşiv boş - önce: python market_archive.py backfill")
    for r in rows:
        print(f"{r['table']:<17} {r['key']:<11} {r['rows']:>5} satır  {r['first']} → {r['last']}  "
              f"(canlı: {r['live']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
