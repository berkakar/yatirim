"""İşlem Günlüğü hesaplamaları - [2026-09-28 · Öneri 6].

Neden: 2026-09-28 emir analizini yapabilmek için fill önbelleği, git geçmişi
ve bar önbellekleri elle birleştirilmek zorunda kalındı; çıkış sebebi hiçbir
yerde tutulmuyordu. Bu modül Alpaca'nın kendi emir geçmişinden (tüm modüller,
tüm semboller) kapanmış işlemleri (round-trip) kurar ve her biri için:
    - giriş kaynağı (algo-<algoritma>-<periyot>, orb, rs, hai, rebuy, elle)
    - ilk stop ve R çarpanı: (çıkış - giriş) / (giriş - ilk stop)
    - çıkış sebebi (stop emrinin etiketi - bkz. stop_tags.py; seans dışı
      guard limiti; açılış kalkanı çıkışı; modül market çıkışı)
    - giriş/çıkışın seans dilimi (açılışın ilk 15 dk'sı, seans içi, seans dışı)
çıkarır; özet istatistikler (isabet, ortalama R, beklenen değer) verir.

API çağrısı yapmaz; girdi alpaca_client.get_recent_orders() çıktısıdır
(tests/test_trade_journal.py). Streamlit sayfası: trade_journal_page.py.
"""

from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from stop_tags import REASON_LABELS, parse_shield_real_stop, tag_kind

ET = ZoneInfo("America/New_York")
OPENING_WINDOW_MINUTES = 15

SESSION_OPENING = "Açılış (ilk 15 dk)"
SESSION_REGULAR = "Seans içi"
SESSION_EXTENDED = "Seans dışı"

MODULE_LABELS = {"orb": "ORB", "rs": "Relative Strength", "hai": "Heikin Ashi Gün İçi", "rebuy": "Alım-Stop-Alım"}


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def session_bucket(ts: datetime) -> str:
    et = ts.astimezone(ET)
    t = et.time()
    if et.weekday() >= 5 or t < dtime(9, 30) or t >= dtime(16, 0):
        return SESSION_EXTENDED
    if t < dtime(9, 30 + OPENING_WINDOW_MINUTES):
        return SESSION_OPENING
    return SESSION_REGULAR


MANUAL_SOURCE = "Elle / bilinmiyor"
REBUY_LABEL = "Alım-Stop-Alım"


def entry_source_parts(client_order_id: str | None) -> tuple[str, str | None]:
    """(algoritma etiketi, periyot) - periyot sadece PBP ve Alım-Stop-Alım
    emirlerinde vardır. Etiket biçimleri: algo-<algo>-<periyot>-<sembol>-<ts>,
    rebuy-<algo>-<periyot>-<sembol>-<ts>, orb-/rs-/hai-..."""
    parts = (client_order_id or "").split("-")
    if not parts or not parts[0]:
        return MANUAL_SOURCE, None
    if parts[0] == "algo" and len(parts) >= 4:
        return f"PBP: {parts[1]}", (parts[2] if len(parts) >= 5 else None)
    if parts[0] == "rebuy" and len(parts) >= 5:
        return f"{REBUY_LABEL}: {parts[1]}", parts[2]
    return MODULE_LABELS.get(parts[0], MANUAL_SOURCE), None


def entry_source(client_order_id: str | None) -> str:
    algo, timeframe = entry_source_parts(client_order_id)
    return f"{algo} ({timeframe})" if timeframe else algo


def exit_reason(order: dict) -> str:
    """Kapanış emrinin (sell) türünden ve etiketinden çıkış sebebi."""
    kind = tag_kind(order.get("client_order_id"))
    order_type = order.get("type")
    if kind == "shieldexit":
        return REASON_LABELS["shieldexit"]
    if order_type in ("stop", "stop_limit"):
        if kind in REASON_LABELS:
            return f"Stop: {REASON_LABELS[kind]}"
        return "Stop (etiketsiz - 28.09 öncesi)"
    if order_type == "limit" and order.get("extended_hours"):
        return "Seans dışı guard (acil limit)"
    prefix = (order.get("client_order_id") or "").split("-")[0]
    if prefix in MODULE_LABELS:
        return f"{MODULE_LABELS[prefix]} çıkışı (market)"
    return "Market / elle"


def _is_filled(o: dict) -> bool:
    return o.get("status") == "filled" and bool(o.get("filled_avg_price")) and bool(o.get("filled_at"))


def _flatten(orders: list[dict]) -> list[dict]:
    """nested=true ile çekilen emirlerde OTO/bracket bacakları ana emrin
    "legs" alanında gelir - dolan stop bacağı (çıkış) da orada. Hepsi tek
    listeye açılır; aynı id iki kez gelirse bir kez sayılır."""
    seen: set[str] = set()
    result = []
    for o in orders:
        for item in [o] + [{**leg, "symbol": leg.get("symbol") or o.get("symbol")} for leg in o.get("legs") or []]:
            key = item.get("id") or id(item)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
    return result


def _stop_orders(orders: list[dict]) -> list[dict]:
    return [o for o in orders if o.get("type") in ("stop", "stop_limit")]


@dataclass
class RoundTrip:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    qty: float
    entry_price: float
    exit_price: float
    entry_source: str
    exit_reason: str
    entry_session: str
    exit_session: str
    initial_stop: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.qty

    @property
    def pnl_pct(self) -> float:
        return (self.exit_price / self.entry_price - 1) * 100 if self.entry_price else 0.0

    @property
    def r_multiple(self) -> float | None:
        if self.initial_stop is None or self.initial_stop >= self.entry_price:
            return None
        return (self.exit_price - self.entry_price) / (self.entry_price - self.initial_stop)

    @property
    def holding_hours(self) -> float:
        return (self.exit_time - self.entry_time).total_seconds() / 3600


@dataclass
class OpenLot:
    """Emir geçmişine göre hâlâ açık olan pozisyon: 0'dan açıldı, henüz 0'a
    dönmedi. Kısmi satış yapıldıysa gerçekleşmiş kısmı realized_pnl'dedir;
    kalan adet ve gerçekleşmemiş K/Z için Alpaca'nın canlı pozisyonu esas
    alınır (bkz. trade_journal_analysis.build_open_positions)."""
    symbol: str
    entry_time: datetime
    entry_order: dict
    qty: float
    avg_entry: float
    realized_pnl: float
    sold_qty: float
    initial_stop: float | None = None


def walk_fills(orders: list[dict], now: datetime | None = None) -> tuple[list[RoundTrip], dict[str, OpenLot]]:
    """Sembol bazında dolan emirleri zaman sırasıyla gezer: pozisyon 0'dan
    açıldığında işlem başlar, 0'a döndüğünde kapanır (arada ilave alımlar
    ortalamaya katılır, kısmi satışlar ağırlıklı çıkış fiyatına). Sadece
    long. Penceredeki ilk emir bir satışsa (alışı pencere dışında kalmış)
    o satış atlanır. Döngü sonunda kapanmamış pozisyonlar OpenLot olarak
    döner."""
    orders = _flatten(orders)
    stops_by_symbol: dict[str, list[dict]] = {}
    for s in _stop_orders(orders):
        stops_by_symbol.setdefault(s["symbol"], []).append(s)

    fills_by_symbol: dict[str, list[dict]] = {}
    for o in orders:
        if _is_filled(o):
            fills_by_symbol.setdefault(o["symbol"], []).append(o)

    trips: list[RoundTrip] = []
    open_lots: dict[str, OpenLot] = {}
    for symbol, fills in fills_by_symbol.items():
        fills.sort(key=lambda o: o["filled_at"])
        qty = 0.0
        cost = 0.0
        bought = 0.0
        entry_order = None
        exit_qty = 0.0
        exit_value = 0.0
        for o in fills:
            fq = float(o["filled_qty"])
            fp = float(o["filled_avg_price"])
            if o["side"] == "buy":
                if qty <= 1e-9:
                    entry_order, cost, bought, exit_qty, exit_value = o, 0.0, 0.0, 0.0, 0.0
                qty += fq
                bought += fq
                cost += fq * fp
                continue
            if qty <= 1e-9 or entry_order is None:
                continue  # alışı pencere dışında kalmış satış
            sold = min(fq, qty)
            exit_qty += sold
            exit_value += sold * fp
            qty -= sold
            if qty > 1e-9:
                continue
            entry_time = _parse(entry_order["filled_at"])
            exit_time = _parse(o["filled_at"])
            avg_entry = cost / exit_qty if exit_qty else fp
            trips.append(RoundTrip(
                symbol=symbol, entry_time=entry_time, exit_time=exit_time, qty=exit_qty,
                entry_price=avg_entry, exit_price=exit_value / exit_qty,
                entry_source=entry_source(entry_order.get("client_order_id")),
                exit_reason=exit_reason(o),
                entry_session=session_bucket(entry_time), exit_session=session_bucket(exit_time),
                initial_stop=_initial_stop(stops_by_symbol.get(symbol, []), entry_order, exit_time),
                extra={"client_order_id": entry_order.get("client_order_id")},
            ))
            qty, cost, bought, entry_order = 0.0, 0.0, 0.0, None
        if qty > 1e-9 and entry_order is not None:
            avg_entry = cost / bought
            upper = now or datetime.now(ET)
            open_lots[symbol] = OpenLot(
                symbol=symbol, entry_time=_parse(entry_order["filled_at"]), entry_order=entry_order,
                qty=qty, avg_entry=avg_entry, realized_pnl=exit_value - avg_entry * exit_qty, sold_qty=exit_qty,
                initial_stop=_initial_stop(stops_by_symbol.get(symbol, []), entry_order, upper),
            )
    trips.sort(key=lambda t: t.exit_time)
    return trips, open_lots


def build_round_trips(orders: list[dict]) -> list[RoundTrip]:
    """Kapanmış işlemler (bkz. walk_fills)."""
    return walk_fills(orders)[0]


def _initial_stop(stops: list[dict], entry_order: dict, exit_time: datetime) -> float | None:
    """Giriş emri oluşturulduktan sonra (OTO bacağı girişle aynı anda
    oluşur) ve çıkıştan önce kurulan EN ESKİ stop emrinin seviyesi."""
    lower = _parse(entry_order.get("created_at")) or _parse(entry_order.get("filled_at"))
    candidates = []
    for s in stops:
        created = _parse(s.get("created_at"))
        if created is None or s.get("stop_price") in (None, ""):
            continue
        if lower is not None and created < lower:
            continue
        if created > exit_time:
            continue
        candidates.append((created, s))
    if not candidates:
        return None
    _, first = min(candidates, key=lambda c: c[0])
    real = parse_shield_real_stop(first.get("client_order_id"))
    return real if real is not None else float(first["stop_price"])


def summarize(trips: list[RoundTrip]) -> dict:
    n = len(trips)
    wins = [t for t in trips if t.pnl > 0]
    losses = [t for t in trips if t.pnl <= 0]
    rs = [t.r_multiple for t in trips if t.r_multiple is not None]
    by_session: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for t in trips:
        by_session[t.exit_session] = by_session.get(t.exit_session, 0) + 1
        by_reason[t.exit_reason] = by_reason.get(t.exit_reason, 0) + 1
    return {
        "trades": n,
        "wins": len(wins),
        "win_rate": len(wins) / n * 100 if n else 0.0,
        "total_pnl": sum(t.pnl for t in trips),
        "avg_win": sum(t.pnl for t in wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(t.pnl for t in losses) / len(losses) if losses else 0.0,
        "avg_win_pct": sum(t.pnl_pct for t in wins) / len(wins) if wins else 0.0,
        "avg_loss_pct": sum(t.pnl_pct for t in losses) / len(losses) if losses else 0.0,
        "r_count": len(rs),
        "total_r": sum(rs),
        "expectancy_r": sum(rs) / len(rs) if rs else None,
        "exits_by_session": by_session,
        "exits_by_reason": by_reason,
    }
