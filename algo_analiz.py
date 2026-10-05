"""Algo Analiz hesaplamaları (eski adıyla İşlem Günlüğü).

Sayfa (trade_journal_page.py) sırasıyla şunları gösterir:
    1. Portföyün son durumu: ilk giriş (yatırılan sermaye), güncel değer, K/Z
    2. Karlılık: açık pozisyonlar şimdi satılırsa / stoplar devreye girerse
       oluşacak K/Z ve kapanan pozisyonlardan gerçekleşen K/Z
    3. Algoritma × stop algoritması bazında karlılık
    4. Hisse hareketleri tablosu
    5. Çıkış sebebi, çıkış ve giriş seans dilimi istatistikleri

Bu modül 2. ve 3. adımın hesaplarını yapar; API çağrısı yapmaz
(tests/test_algo_analiz.py). Kayıtlar trade_journal_analysis.Record'dur.

Stop senaryosu: açık pozisyonun Alpaca'daki açık stop emirleri (sell, stop /
stop_limit) tetiklenirse pozisyon stop seviyesinden kapanır. Açılış kalkanının
geniş "felaket" stopu (shield-...) geçicidir, 09:45 ET'de etiketindeki gerçek
seviyeye döner - senaryoda o gerçek seviye kullanılır (bkz. stop_tags.py).
Stop emriyle korunmayan adet anlık fiyattan değerlenir.

Stop algoritması: emir geçmişinde hangi stop algoritmasıyla yönetildiği
tutulmadığından modüllerin GÜNCEL ayarından çözülür - canlı botun
(alpaca_trailing_stop.resolve_stop_algorithm_for_position) kullandığı sırayla:
RS / ORB / Heikin Ashi modülünün pozisyonu o modülün stop algoritmasıyla,
diğer her şey (Premium Buy Point, Alım-Stop-Alım, elle) PBP portföy ayarıyla
(hisse bazlı override varsa o) yönetilir. Ayar sonradan değiştiyse eski
kapalı işlemler yeni algoritma altında görünür.
"""

from stop_tags import parse_shield_real_stop
from trade_journal_analysis import STATUS_OPEN, Record


def _f(v, default: float | None = 0.0) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def open_stop_levels(open_orders: list[dict]) -> dict[str, list[tuple[float, float]]]:
    """Açık sell stop emirlerinden sembol -> [(etkin stop fiyatı, adet), ...].
    Kısmen dolmuş emirde kalan adet alınır; kalkan stopunda etiketteki gerçek
    seviye etkin stop sayılır."""
    levels: dict[str, list[tuple[float, float]]] = {}
    for o in open_orders:
        if o.get("type") not in ("stop", "stop_limit") or o.get("side") != "sell":
            continue
        price = parse_shield_real_stop(o.get("client_order_id"))
        if price is None:
            price = _f(o.get("stop_price"), None)
        qty = (_f(o.get("qty")) or 0.0) - (_f(o.get("filled_qty")) or 0.0)
        if price is None or qty <= 0:
            continue
        levels.setdefault(o.get("symbol"), []).append((price, qty))
    return levels


def apply_stop_scenario(records: list[Record], levels: dict[str, list[tuple[float, float]]]) -> None:
    """Açık kayıtlara current_stop (adet ağırlıklı etkin stop) ve
    stop_unrealized (stoplar tetiklenirse gerçekleşmemiş K/Z) yazar. Stop
    emri olmayan pozisyonda ikisi de None kalır. Stop adedi pozisyonu aşarsa
    pozisyon adediyle sınırlanır, korunmayan adet anlık fiyattan sayılır."""
    for r in records:
        if r.status != STATUS_OPEN or r.qty <= 0:
            continue
        stops = sorted(levels.get(r.symbol) or [], key=lambda s: -s[0])  # önce en yakın (yüksek) stop
        remaining = r.qty
        covered = 0.0
        value = 0.0
        for price, qty in stops:
            q = min(qty, remaining)
            if q <= 0:
                break
            covered += q
            value += price * q
            remaining -= q
        if covered <= 0:
            r.current_stop = None
            r.stop_unrealized = None
            continue
        r.current_stop = value / covered
        r.stop_unrealized = (value - r.entry_price * covered) + (r.last_price - r.entry_price) * remaining


def resolve_stop_algorithm_id(rec: Record, pbp_config: dict, module_algos: dict[str, str],
                              module_holdings: dict[str, set] | None = None) -> str:
    """Kaydı yöneten stop algoritmasının kimliği (STOP_ALGORITHMS anahtarı).

    module_algos: modül etiketi (trade_journal.MODULE_LABELS değeri: "ORB",
    "Relative Strength", "Heikin Ashi Gün İçi") -> o modülün stop algoritması.
    module_holdings: aynı etiket -> modülün şu an tuttuğu semboller; açık
    pozisyonlarda canlı bot gibi önce buna bakılır (giriş emri bulunamasa da
    doğru modül bulunur)."""
    from alpaca_trailing_stop import resolve_stop_algorithm  # bkz. stop_loss_settings._live_stop_usage notu

    if rec.status == STATUS_OPEN and module_holdings:
        for label, symbols in module_holdings.items():
            if rec.symbol in symbols and label in module_algos:
                return module_algos[label]
    if rec.algorithm in module_algos:
        return module_algos[rec.algorithm]
    return resolve_stop_algorithm(pbp_config or {}, rec.symbol)


def scenario_totals(records: list[Record]) -> dict:
    """Karlılık bölümünün toplamları."""
    opened = [r for r in records if r.status == STATUS_OPEN]
    closed = [r for r in records if r.status != STATUS_OPEN]
    with_stop = [r for r in opened if r.stop_unrealized is not None]
    open_cost = sum(r.entry_price * r.qty for r in opened)
    unrealized = sum(r.unrealized for r in opened)
    stop_unrealized = sum(r.stop_unrealized if r.stop_unrealized is not None else r.unrealized for r in opened)
    wins = [r for r in closed if r.realized > 0]
    rs = [r.r_multiple for r in closed if r.r_multiple is not None]
    return {
        "open_count": len(opened),
        "open_cost": open_cost,
        "open_value": sum(r.last_price * r.qty for r in opened),
        "unrealized": unrealized,
        "unrealized_pct": unrealized / open_cost * 100 if open_cost else 0.0,
        "stop_unrealized": stop_unrealized,
        "stop_unrealized_pct": stop_unrealized / open_cost * 100 if open_cost else 0.0,
        # Stoplar tetiklenirse şu anki K/Z'den geri verilecek tutar (negatif).
        "stop_giveback": stop_unrealized - unrealized,
        "stopless_count": len(opened) - len(with_stop),
        "stopless_symbols": sorted(r.symbol for r in opened if r.stop_unrealized is None),
        # Açık pozisyonlardaki kısmi satışlar da gerçekleşmiştir.
        "partial_realized": sum(r.realized for r in opened),
        "closed_count": len(closed),
        "closed_realized": sum(r.realized for r in closed),
        "win_rate": len(wins) / len(closed) * 100 if closed else None,
        "total_r": sum(rs) if rs else None,
        "expectancy_r": sum(rs) / len(rs) if rs else None,
    }


def portfolio_snapshot(account: dict, initial_capital: float | None) -> dict:
    """Alpaca /account özetinden portföyün son durumu."""
    equity = _f(account.get("equity")) or 0.0
    last_equity = _f(account.get("last_equity"), None)
    pl = equity - initial_capital if initial_capital else None
    return {
        "equity": equity,
        "cash": _f(account.get("cash")) or 0.0,
        "long_value": _f(account.get("long_market_value")) or 0.0,
        "initial_capital": initial_capital,
        "pl": pl,
        "pl_pct": pl / initial_capital * 100 if pl is not None else None,
        "day_pl": equity - last_equity if last_equity else None,
        "day_pl_pct": (equity / last_equity - 1) * 100 if last_equity else None,
    }


def count_by(records: list[Record], attr: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in records:
        v = getattr(r, attr)
        if v:
            counts[v] = counts.get(v, 0) + 1
    return counts
