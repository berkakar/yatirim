"""
Ucuzluk Skoru'nun geçmiş günlerini Yahoo'nun geçmiş bilanço / gelir
tablolarından yeniden hesaplar ve valuation_scores_daily tablosuna
`reconstructed` kaynağıyla yazar (servisin yazdığı günlere dokunmaz).

Değerler (valuation._raw_from_info ile aynı tanımlar):
    Kârlılık (çeyreklik tablolardan, son 4 çeyrek toplamı - 4 çeyrek yoksa
    çeyreğin 4 katı): ROE, ROA, net kâr marjı, brüt kâr marjı. Her çeyreğin
    değeri bilanço AÇIKLANDIĞI günden itibaren geçerlidir (basamak); çeyreklik
    tabloların kapsamadığı eski günlerde yıllık tablo kullanılır.
    Diğerleri (borç / özsermaye, borç / varlık, cari oran, likidite oranı,
    varlık devir hızı, faiz karşılama, EPS ve gelir büyümesi, son 12 ay EPS):
    tablolardaki noktalar (açıklama günü) ve bugünkü değer arasında zamana göre
    doğrusal interpolasyon; ilk noktadan önce ilk değer.
    F/K: günlük kapanış / interpolasyonlu son 12 ay EPS.
    PEG: Yahoo'nun `pegRatio`'su analist büyüme beklentisine dayanır, geçmişi
    yoktur. Bugünkü PEG'in ima ettiği büyüme (F/K / PEG) sabit kabul edilir:
    PEG(gün) = F/K(gün) / (bugünkü F/K / bugünkü PEG).
    Alt sektör ortalama F/K: akranların geçmişi çekilmediği için bugünkü medyan
    sabit; iskonto günlük F/K ile yeniden hesaplanır.
Puan: valuation.score_row (servisle aynı kurallar).

Eğitimde dikkat: interpolasyon iki açıklama arasındaki günlerde bir sonraki
açıklamanın değerini kısmen kullanır (geleceğe sızıntı); kârlılık oranları
basamak olduğu için sızıntısızdır. Satırlar `_reconstructed` alanıyla ve
tabloda source=reconstructed ile işaretlidir.

Kullanım:
    python valuation_history.py --ticker AAPL --years 2
"""

import argparse
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd

MARKET = "NASDAQ 100"

# Açıklama günü bulunamazsa dönem sonundan bu kadar gün sonra kabul edilir
# (SEC: büyük şirketlerde 10-Q 40, 10-K 60 gün).
QUARTER_LAG_DAYS = 45
ANNUAL_LAG_DAYS = 75
# Dönem sonundan sonra bu kadar gün içindeki ilk bilanço açıklaması o döneme ait sayılır.
ANNOUNCE_WINDOW_DAYS = 100

ROWS = {
    "revenue": ("Total Revenue", "Operating Revenue"),
    "gross": ("Gross Profit",),
    "net_income": ("Net Income", "Net Income Common Stockholders",
                   "Net Income From Continuing Operation Net Minority Interest"),
    "eps": ("Diluted EPS", "Basic EPS"),
    "ebitda": ("EBITDA", "Normalized EBITDA"),
    "interest": ("Interest Expense", "Interest Expense Non Operating"),
    "equity": ("Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"),
    "assets": ("Total Assets",),
    "debt": ("Total Debt",),
    "current_assets": ("Current Assets",),
    "current_liabilities": ("Current Liabilities",),
    "inventory": ("Inventory",),
}

PROFITABILITY = ("Öz Sermaye Getirisi (ROE) %", "Varlık Getirisi (ROA) %", "Net Kar Marjı %", "Brüt Kar Marjı %")
INTERPOLATED = ("Borç / Özsermaye", "Borç / Varlık %", "Cari Oran", "Likidite Oranı", "Varlık Devir Hızı",
                "Faiz Karşılama Oranı", "EPS Büyümesi %", "Gelir Büyümesi %")
EPS_TTM = "_eps_ttm"
OUTPUT_ORDER = ("Hisse", "Alt Sektör (İş Modeli)", "Ana Sektör", "Nihai Skor", "Alt Sektör İskontosu %",
                "Alt Sektör Ort. F/K", "F/K", "PEG", "EPS Büyümesi %", "Gelir Büyümesi %") + PROFITABILITY + (
                "Faiz Karşılama Oranı", "Borç / Özsermaye", "Borç / Varlık %", "Cari Oran", "Likidite Oranı",
                "Varlık Devir Hızı")


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ------------------------------------------------------------------------------
# Yahoo
# ------------------------------------------------------------------------------

def fetch_fundamentals(ticker: str) -> dict:
    """Çeyreklik / yıllık gelir tablosu ve bilanço + bilanço açıklama günleri."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    out = {
        "quarterly_income": t.quarterly_income_stmt,
        "quarterly_balance": t.quarterly_balance_sheet,
        "annual_income": t.income_stmt,
        "annual_balance": t.balance_sheet,
        "earnings_dates": [],
    }
    try:
        ed = t.get_earnings_dates(limit=40)
        if ed is not None and not ed.empty:
            idx = pd.to_datetime(ed.index)
            if idx.tz is not None:
                idx = idx.tz_convert("America/New_York").tz_localize(None)
            out["earnings_dates"] = sorted(set(idx.normalize()))
    except Exception:  # açıklama günü yoksa dönem sonu + gecikme kullanılır
        pass
    return out


# ------------------------------------------------------------------------------
# Tablolardan oranlar (saf fonksiyonlar)
# ------------------------------------------------------------------------------

def _statement(df) -> pd.DataFrame:
    """yfinance tablosu (satır: kalem, sütun: dönem sonu) -> satır: dönem sonu (eskiden yeniye)."""
    if df is None or getattr(df, "empty", True):
        return pd.DataFrame()
    t = df.T.copy()
    t.index = pd.to_datetime(t.index).tz_localize(None).normalize()
    t = t[~t.index.duplicated(keep="first")].sort_index()
    return t.apply(pd.to_numeric, errors="coerce")


def _item(df: pd.DataFrame, key: str) -> pd.Series:
    for name in ROWS[key]:
        if name in df.columns and df[name].notna().any():
            return df[name].astype(float)
    return pd.Series(np.nan, index=df.index, dtype=float)


def _div(a: pd.Series, b: pd.Series, scale: float = 1.0) -> pd.Series:
    return a / b.where(b != 0) * scale


def period_metrics(income, balance, periods_per_year: int) -> pd.DataFrame:
    """Her dönem sonu için oranlar. Akım kalemleri (gelir, kâr...) son 12 aya
    çevrilir: çeyreklikte son 4 çeyrek toplamı (yoksa çeyrek x 4)."""
    inc, bal = _statement(income), _statement(balance)
    idx = inc.index.union(bal.index)
    if idx.empty:
        return pd.DataFrame()
    inc, bal = inc.reindex(idx), bal.reindex(idx)

    def ttm(s):
        if periods_per_year == 1:
            return s
        return s.rolling(periods_per_year, min_periods=periods_per_year).sum().fillna(s * periods_per_year)

    revenue, net = _item(inc, "revenue"), _item(inc, "net_income")
    rev_ttm, net_ttm = ttm(revenue), ttm(net)
    equity, assets = _item(bal, "equity"), _item(bal, "assets")
    debt = _item(bal, "debt")
    cur_a, cur_l = _item(bal, "current_assets"), _item(bal, "current_liabilities")
    inventory = _item(bal, "inventory").fillna(0)
    eps = _item(inc, "eps")
    eps_ttm = eps.rolling(periods_per_year, min_periods=periods_per_year).sum() if periods_per_year > 1 else eps

    def growth(s):
        prev = s.shift(periods_per_year)
        return ((s / prev - 1) * 100).where(prev > 0)

    out = pd.DataFrame(index=idx)
    out["Öz Sermaye Getirisi (ROE) %"] = _div(net_ttm, equity, 100)
    out["Varlık Getirisi (ROA) %"] = _div(net_ttm, assets, 100)
    out["Net Kar Marjı %"] = _div(net_ttm, rev_ttm, 100)
    out["Brüt Kar Marjı %"] = _div(ttm(_item(inc, "gross")), rev_ttm, 100)
    out["Borç / Özsermaye"] = _div(debt, equity)
    out["Borç / Varlık %"] = _div(debt, assets, 100)
    out["Cari Oran"] = _div(cur_a, cur_l)
    out["Likidite Oranı"] = _div(cur_a - inventory, cur_l)
    out["Varlık Devir Hızı"] = _div(rev_ttm, assets)
    out["Faiz Karşılama Oranı"] = _div(_item(inc, "ebitda"), _item(inc, "interest").abs())
    out["EPS Büyümesi %"] = growth(eps)
    out["Gelir Büyümesi %"] = growth(revenue)
    out[EPS_TTM] = eps_ttm
    return out.replace([np.inf, -np.inf], np.nan)


def announce_dates(period_ends, earnings_dates, lag_days: int) -> pd.DatetimeIndex:
    """Her dönemin değerinin geçerli olduğu ilk gün: dönem sonundan sonraki ilk
    bilanço açıklamasının ertesi günü (açıklama çoğunlukla kapanıştan sonra);
    yoksa dönem sonu + lag_days."""
    ed = pd.DatetimeIndex(sorted(earnings_dates)) if earnings_dates is not None and len(earnings_dates) \
        else pd.DatetimeIndex([])
    out = []
    for pe in pd.DatetimeIndex(period_ends):
        after = ed[(ed > pe) & (ed <= pe + pd.Timedelta(days=ANNOUNCE_WINDOW_DAYS))]
        out.append(after[0] + pd.Timedelta(days=1) if len(after) else pe + pd.Timedelta(days=lag_days))
    return pd.DatetimeIndex(out)


def anchor_points(fundamentals: dict) -> pd.DataFrame:
    """Oranların geçerli olduğu günler (indeks) - çeyreklik değerler; çeyreklik
    tabloların başladığı günden önce yıllık değerler."""
    ed = fundamentals.get("earnings_dates")
    frames = []
    q = period_metrics(fundamentals.get("quarterly_income"), fundamentals.get("quarterly_balance"), 4)
    if not q.empty:
        q.index = announce_dates(q.index, ed, QUARTER_LAG_DAYS)
        frames.append(q)
    a = period_metrics(fundamentals.get("annual_income"), fundamentals.get("annual_balance"), 1)
    if not a.empty:
        a.index = announce_dates(a.index, ed, ANNUAL_LAG_DAYS)
        if frames:
            a = a[a.index < frames[0].index.min()]
        frames.insert(0, a)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames).sort_index()
    return out[~out.index.duplicated(keep="last")]


def _step(points: pd.Series, days: pd.DatetimeIndex) -> pd.Series:
    """Her güne o gün veya öncesindeki son nokta; ilk noktadan önce ilk nokta."""
    points = points.dropna()
    if points.empty:
        return pd.Series(np.nan, index=days)
    return points.reindex(points.index.union(days)).ffill().bfill().reindex(days)


def _interp(points: pd.Series, days: pd.DatetimeIndex) -> pd.Series:
    """Noktalar arasında zamana göre doğrusal; uçlarda en yakın nokta."""
    points = points.dropna()
    points = points[~points.index.duplicated(keep="last")]
    if points.empty:
        return pd.Series(np.nan, index=days)
    full = points.reindex(points.index.union(days))
    return full.interpolate(method="time").ffill().bfill().reindex(days)


def _f(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def daily_metrics(closes: pd.Series, anchors: pd.DataFrame, current_raw: dict) -> pd.DataFrame:
    """Günlük oranlar. current_raw: hissenin bugünkü ham değerleme verisi
    (valuation_scores.raw) - interpolasyonun son noktası ve tablo verisi
    olmayan oranlar için sabit değer."""
    closes = closes.dropna()
    days = pd.DatetimeIndex(closes.index)
    today = days.max()
    anchors = anchors if anchors is not None else pd.DataFrame()
    out = pd.DataFrame(index=days)

    def points(col):
        s = anchors[col] if col in anchors else pd.Series(dtype=float)
        return s.dropna()

    for col in PROFITABILITY:
        p = points(col)
        out[col] = _step(p, days) if not p.empty else _f(current_raw.get(col))
    for col in INTERPOLATED:
        p = points(col)
        now = _f(current_raw.get(col))
        if now is not None:
            p = pd.concat([p, pd.Series([now], index=[today])])
        out[col] = _interp(p, days)

    # Son 12 ay EPS: tablolar + bugünkü (kapanış / F/K); F/K günlük.
    eps = points(EPS_TTM)
    pe_now = _f(current_raw.get("F/K"))
    if pe_now:
        eps = pd.concat([eps, pd.Series([closes.iloc[-1] / pe_now], index=[today])])
    eps_daily = _interp(eps, days)
    out["F/K"] = (closes / eps_daily).where(eps_daily > 0)

    peg_now = _f(current_raw.get("PEG"))
    if peg_now and pe_now:
        implied_growth = pe_now / peg_now
        out["PEG"] = (out["F/K"] / implied_growth).where(out["F/K"] > 0)
    else:
        out["PEG"] = np.nan
    return out.astype(float)


def score_days(ticker: str, metrics: pd.DataFrame, current_scored: dict) -> list:
    """Günlük oranlardan skor tablosu satırları: [(gün, satır)]. Alt sektör
    ortalama F/K bugünkü değerinde sabit; az hisseli alt sektörde iskonto 1."""
    import valuation

    sector_pe = _f(current_scored.get("Alt Sektör Ort. F/K"))
    small = bool(current_scored.get("_az_hisseli"))
    rows = []
    for day, rec in metrics.iterrows():
        row = {k: (None if pd.isna(v) else round(float(v), 2)) for k, v in rec.items()}
        pe = row.get("F/K")
        if pe is not None and sector_pe and sector_pe > 0:
            row["Alt Sektör İskontosu %"] = (valuation.SMALL_SECTOR_DISCOUNT if small
                                             else round((sector_pe - pe) / sector_pe * 100, 1))
        else:
            row["Alt Sektör İskontosu %"] = None
        row.update({
            "Hisse": ticker, "Alt Sektör Ort. F/K": sector_pe, "_az_hisseli": small,
            "Ana Sektör": current_scored.get("Ana Sektör"),
            "Alt Sektör (İş Modeli)": current_scored.get("Alt Sektör (İş Modeli)"),
        })
        row["Nihai Skor"] = valuation.score_row(row)
        ordered = {k: row.get(k) for k in OUTPUT_ORDER}
        ordered.update({"_az_hisseli": small, "_reconstructed": True})
        rows.append((pd.Timestamp(day).strftime("%Y-%m-%d"), ordered))
    return rows


# ------------------------------------------------------------------------------
# Çalıştırma
# ------------------------------------------------------------------------------

def reconstruct(ticker: str, closes: pd.Series, market: str = MARKET, fundamentals=None,
                fetcher=fetch_fundamentals, start: str | None = None) -> dict:
    """Hissenin `closes` günleri için geçmiş skoru hesaplayıp yazar. Bugünkü
    değerleme satırı (valuation_scores) gerekir. Döner: özet."""
    import valuation_db

    row = valuation_db.get_rows(market, [ticker]).get(ticker)
    if row is None:
        raise KeyError(f"{ticker}: {market} değerleme skoru yok - önce Değerleme modülünden / servisten çekin")
    if row["scored"].get("_excluded"):
        raise ValueError(f"{ticker}: ETF - değerleme skoru hesaplanmaz")
    if fundamentals is None:
        fundamentals = fetcher(ticker)
    closes = closes.dropna()
    if start:
        closes = closes[closes.index >= pd.Timestamp(start)]
    anchors = anchor_points(fundamentals)
    metrics = daily_metrics(closes, anchors, row["raw"])
    days = score_days(ticker, metrics, row["scored"])
    written = valuation_db.upsert_reconstructed(market, ticker, days)
    return {
        "ticker": ticker,
        "days": len(days),
        "written": written,
        "anchors": int(len(anchors)),
        "first_anchor": anchors.index.min().strftime("%Y-%m-%d") if len(anchors) else None,
        "quarterly_from": _first_quarter(fundamentals),
    }


def _first_quarter(fundamentals):
    q = _statement(fundamentals.get("quarterly_income"))
    return q.index.min().strftime("%Y-%m-%d") if not q.empty else None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Ucuzluk Skoru geçmişini bilançolardan yeniden hesaplar")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--market", default=MARKET)
    args = parser.parse_args(argv)
    import ai_dataset

    ticker = args.ticker.strip().upper()
    end = pd.Timestamp(datetime.now(timezone.utc).date())
    closes = ai_dataset.download_ohlcv(ticker, (end - pd.DateOffset(years=args.years)).strftime("%Y-%m-%d"))["close"]
    summary = reconstruct(ticker, closes, args.market)
    log(f"{ticker}: {summary['days']} gün hesaplandı, {summary['written']} satır yazıldı "
        f"({summary['anchors']} bilanço noktası, çeyreklik tablolar {summary['quarterly_from']} itibarıyla)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
