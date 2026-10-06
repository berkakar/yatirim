"""Algoritmik modüllerin (Relative Strength, ORB, Heikin Ashi Gün İçi) nakit
payı - [2026-10-06].

Neden: Eskiden her modülün payı `canlı nakit x cash_allocation_pct` idi.
Alımlar başladıkça Alpaca'daki nakit azaldığı için pay da küçülüyordu:
100.000$ nakitle %10 pay 10.000$ ile başlıyor, nakit 60.000$'a inince modüle
6.000$ düşüyordu - modülün kendi aldığı hisseler hesaba hiç girmiyordu.

Nasıl:
    bütçe  = hesap değeri (equity = nakit + pozisyonlar) x cash_allocation_pct
    kullanılan = modülün elindeki (canlı) pozisyonların alış maliyeti
    kalan  = bütçe - kullanılan - modülün bekleyen buy-limit emirleri
Kalan, gerçek nakdin üstüne çıkamaz. Premium Buy Point de modüllerin henüz
harcanmamış paylarını (bütçe - kullanılan) nakitten düşer - böylece
modüllerin parası başka bir sistem tarafından harcanmaz.
"""

from alpaca_client import AlpacaClient


def module_budget(account: dict, cash_allocation_pct: float) -> float:
    """Modülün toplam payı - pozisyon açıldıkça değişmeyen referans."""
    return max(0.0, float(account.get("equity") or 0.0)) * cash_allocation_pct / 100


def module_used_cash(positions: list[dict], symbols) -> float:
    """Modülün elindeki sembollerin (Alpaca'da hâlâ açık olanların) alış
    maliyeti. Stopu tetiklenmiş, artık pozisyonu olmayan semboller sayılmaz."""
    symbols = set(symbols)
    used = 0.0
    for p in positions:
        if p.get("symbol") not in symbols:
            continue
        cost = p.get("cost_basis")
        if cost is None:
            cost = float(p.get("qty") or 0) * float(p.get("avg_entry_price") or 0)
        used += abs(float(cost))
    return used


def reserved_buy_orders(open_orders: list[dict], tag_prefix: str) -> float:
    """`tag_prefix-` etiketli, hâlâ bekleyen buy-limit emirlerinin tutarı."""
    return sum(
        float(o["qty"]) * float(o["limit_price"])
        for o in open_orders
        if o.get("type") == "limit" and o.get("side") == "buy"
        and (o.get("client_order_id") or "").startswith(f"{tag_prefix}-")
    )


def module_available_cash(
    client: AlpacaClient, cash_allocation_pct: float, held_symbols, tag_prefix: str,
) -> tuple[float, float]:
    """(bütçe, kalan) - kalan = bütçe - elde tutulanların maliyeti - bekleyen
    emirler; gerçek nakitle sınırlı."""
    account = client.get_account()
    budget = module_budget(account, cash_allocation_pct)
    used = module_used_cash(client.get_all_positions(), held_symbols) if held_symbols else 0.0
    reserved = reserved_buy_orders(client.get_open_orders(), tag_prefix)
    cash = float(account.get("cash") or 0.0)
    return budget, max(0.0, min(cash, budget - used - reserved))


def unspent_module_reserve(account: dict, positions: list[dict], modules: list[tuple[float, set]]) -> float:
    """Premium Buy Point için: modüllerin henüz harcanmamış payları toplamı.
    modules: [(cash_allocation_pct, elde tutulan semboller), ...]"""
    return sum(
        max(0.0, module_budget(account, pct) - module_used_cash(positions, symbols))
        for pct, symbols in modules if pct > 0
    )


def module_cash_caption(client: AlpacaClient, cash_allocation_pct: float, held_symbols, slots: int | None = None) -> str | None:
    """Ayar sayfaları için: modülün payı, alınan hisselerin maliyeti ve kalan.
    Alpaca'ya ulaşılamazsa None."""
    try:
        account = client.get_account()
        used = module_used_cash(client.get_all_positions(), held_symbols) if held_symbols else 0.0
    except Exception:
        return None
    budget = module_budget(account, cash_allocation_pct)
    text = (f"Bu ayarla modüle düşen tutar (hesap değeri ${float(account.get('equity') or 0):,.2f} × "
            f"%{cash_allocation_pct:g}): **${budget:,.2f}** · alınan hisseler: **${used:,.2f}** · "
            f"kalan: **${max(0.0, budget - used):,.2f}**")
    if slots:
        text += f" · pozisyon başına: **${budget / slots:,.2f}**"
    return text
