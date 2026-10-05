"""İşlem Günlüğü Analizi hesaplamaları.

Soru: "Hangi hisse hangi algoritma ile ne kadar kazandırdı / kaybettirdi?"
Kapanmış işlemler (trade_journal.walk_fills) ile Alpaca'nın canlı
pozisyonları (/positions) tek bir kayıt listesinde birleştirilir:
    - kapanmış işlem  -> gerçekleşmiş K/Z, R, isabet
    - açık pozisyon   -> Alpaca'nın gerçekleşmemiş K/Z'si + (varsa) kısmi
                         satışların gerçekleşmiş K/Z'si
Açık pozisyonun hangi algoritmayla açıldığı, emir geçmişinde pozisyonu 0'dan
açan alım emrinin etiketinden okunur (OpenLot.entry_order).

API çağrısı yapmaz (tests/test_trade_journal_analysis.py). Streamlit
sayfası: trade_journal_page.py (🧠 Algo Analiz), stop senaryosu: algo_analiz.py.
"""

from dataclasses import dataclass
from datetime import datetime

from trade_journal import MANUAL_SOURCE, OpenLot, RoundTrip, entry_source_parts, session_bucket

STATUS_CLOSED = "Kapalı"
STATUS_OPEN = "Açık"
UNKNOWN_SOURCE = "Bilinmiyor (giriş emri bulunamadı)"


@dataclass
class Record:
    symbol: str
    algorithm: str
    timeframe: str | None
    status: str
    realized: float
    unrealized: float
    cost_basis: float
    entry_time: datetime | None
    exit_time: datetime | None = None
    r_multiple: float | None = None
    qty: float = 0.0
    entry_price: float = 0.0
    last_price: float = 0.0
    initial_stop: float | None = None
    exit_reason: str | None = None
    entry_session: str | None = None
    exit_session: str | None = None
    # Algo Analiz (algo_analiz.py) doldurur: pozisyonu yöneten stop algoritması,
    # açık pozisyonun güncel (etkin) stop seviyesi ve stoplar tetiklenirse
    # oluşacak gerçekleşmemiş K/Z. Stopsuz açık pozisyonda stop_unrealized None.
    stop_algorithm: str | None = None
    current_stop: float | None = None
    stop_unrealized: float | None = None

    @property
    def total(self) -> float:
        return self.realized + self.unrealized

    @property
    def stop_total(self) -> float:
        """Stoplar devreye girerse toplam K/Z. Kapalı işlemde gerçekleşenin
        kendisi; stopu olmayan açık pozisyonda anlık K/Z ile aynı kabul edilir."""
        if self.status != STATUS_OPEN or self.stop_unrealized is None:
            return self.total
        return self.realized + self.stop_unrealized

    @property
    def pnl_pct(self) -> float:
        return self.total / self.cost_basis * 100 if self.cost_basis else 0.0

    def algo_label(self, split_timeframe: bool) -> str:
        return f"{self.algorithm} ({self.timeframe})" if split_timeframe and self.timeframe else self.algorithm


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def closed_records(trips: list[RoundTrip]) -> list[Record]:
    records = []
    for t in trips:
        algo, tf = entry_source_parts((t.extra or {}).get("client_order_id"))
        records.append(Record(
            symbol=t.symbol, algorithm=algo, timeframe=tf, status=STATUS_CLOSED,
            realized=t.pnl, unrealized=0.0, cost_basis=t.entry_price * t.qty,
            entry_time=t.entry_time, exit_time=t.exit_time, r_multiple=t.r_multiple,
            qty=t.qty, entry_price=t.entry_price, last_price=t.exit_price, initial_stop=t.initial_stop,
            exit_reason=t.exit_reason, entry_session=t.entry_session, exit_session=t.exit_session,
        ))
    return records


def open_records(positions: list[dict], open_lots: dict[str, OpenLot]) -> list[Record]:
    """Alpaca pozisyonları esas alınır (adet, ortalama maliyet, anlık fiyat,
    gerçekleşmemiş K/Z); algoritma, giriş zamanı, ilk stop ve kısmi satış
    K/Z'si emir geçmişinden gelen OpenLot'tan eklenir. Lot yoksa (giriş emri
    geçmişte bulunamadı) algoritma "bilinmiyor" olur."""
    records = []
    for p in positions:
        symbol = p.get("symbol")
        qty = _f(p.get("qty"))
        avg_entry = _f(p.get("avg_entry_price"))
        lot = open_lots.get(symbol)
        if lot is not None:
            algo, tf = entry_source_parts(lot.entry_order.get("client_order_id"))
        else:
            algo, tf = UNKNOWN_SOURCE, None
        records.append(Record(
            symbol=symbol, algorithm=algo, timeframe=tf, status=STATUS_OPEN,
            realized=lot.realized_pnl if lot else 0.0,
            unrealized=_f(p.get("unrealized_pl")),
            cost_basis=abs(_f(p.get("cost_basis"), avg_entry * qty)) + (avg_entry * lot.sold_qty if lot else 0.0),
            entry_time=lot.entry_time if lot else None,
            qty=qty, entry_price=avg_entry, last_price=_f(p.get("current_price")),
            initial_stop=lot.initial_stop if lot else None,
            entry_session=session_bucket(lot.entry_time) if lot else None,
        ))
    return records


def open_r_multiple(rec: Record) -> float | None:
    """Açık pozisyonun anlık R'si (bilgi amaçlı; beklenen değere katılmaz)."""
    if rec.initial_stop is None or rec.initial_stop >= rec.entry_price or rec.qty <= 0:
        return None
    return (rec.last_price - rec.entry_price) / (rec.entry_price - rec.initial_stop)


def filter_records(records: list[Record], since: datetime | None = None, include_manual: bool = True,
                   status: str | None = None) -> list[Record]:
    out = []
    for r in records:
        if since is not None and (r.entry_time is None or r.entry_time < since):
            continue
        if not include_manual and r.algorithm in (MANUAL_SOURCE, UNKNOWN_SOURCE):
            continue
        if status and r.status != status:
            continue
        out.append(r)
    return out


def group_summary(records: list[Record], key) -> list[dict]:
    """`key(record) -> hashable` ile gruplar; her grup için kapanmış işlem
    istatistikleri (isabet, R, profit factor) ve açık pozisyon K/Z'si.
    Toplam K/Z'ye göre büyükten küçüğe sıralı."""
    groups: dict = {}
    for r in records:
        groups.setdefault(key(r), []).append(r)
    rows = []
    for k, recs in groups.items():
        closed = [r for r in recs if r.status == STATUS_CLOSED]
        opened = [r for r in recs if r.status == STATUS_OPEN]
        wins = [r for r in closed if r.realized > 0]
        gross_win = sum(r.realized for r in wins)
        gross_loss = -sum(r.realized for r in closed if r.realized <= 0)
        rs = [r.r_multiple for r in closed if r.r_multiple is not None]
        cost = sum(r.cost_basis for r in recs)
        realized = sum(r.realized for r in recs)
        unrealized = sum(r.unrealized for r in recs)
        rows.append({
            "key": k,
            "closed": len(closed),
            "open": len(opened),
            "wins": len(wins),
            "win_rate": len(wins) / len(closed) * 100 if closed else None,
            "realized": realized,
            "unrealized": unrealized,
            "total": realized + unrealized,
            "stop_total": sum(r.stop_total for r in recs),
            "stopless": sum(1 for r in opened if r.stop_unrealized is None),
            "return_pct": (realized + unrealized) / cost * 100 if cost else 0.0,
            "avg_r": sum(rs) / len(rs) if rs else None,
            "total_r": sum(rs) if rs else None,
            "profit_factor": (gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else None)),
            "best": max(recs, key=lambda r: r.total).symbol if recs else None,
            "worst": min(recs, key=lambda r: r.total).symbol if recs else None,
        })
    rows.sort(key=lambda row: -row["total"])
    return rows


def pnl_matrix(records: list[Record], split_timeframe: bool) -> dict[tuple[str, str], float]:
    """(hisse, algoritma) -> toplam K/Z (gerçekleşmiş + açık)."""
    m: dict[tuple[str, str], float] = {}
    for r in records:
        k = (r.symbol, r.algo_label(split_timeframe))
        m[k] = m.get(k, 0.0) + r.total
    return m


def cumulative_realized(records: list[Record], split_timeframe: bool) -> list[tuple[datetime, str, float]]:
    """Kapanmış işlemlerin çıkış zamanına göre algoritma başına birikimli
    gerçekleşmiş K/Z noktaları: (zaman, algoritma, birikimli K/Z)."""
    running: dict[str, float] = {}
    points = []
    for r in sorted((r for r in records if r.status == STATUS_CLOSED and r.exit_time), key=lambda r: r.exit_time):
        label = r.algo_label(split_timeframe)
        running[label] = running.get(label, 0.0) + r.realized
        points.append((r.exit_time, label, running[label]))
    return points
