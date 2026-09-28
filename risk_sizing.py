"""R bazlı pozisyon büyüklüğü ve portföy ısısı - [2026-09-28 · Öneri 5].

Neden: Eskiden adet = (bütçe x ağırlık%) / fiyat idi; stop mesafesi hesaba hiç
girmiyordu. Stop sabit %1.5 olduğu için işlem başına risk doğrudan pozisyonun
dolar büyüklüğüne bağlıydı ve bütçe/ağırlık her değiştiğinde oynuyordu:
2026-09-28 analizinde MU (3 Eyl) ~14$, MSFT (18 Eyl) ~282$ riskle açılmıştı -
20 kat fark. En iyi iki işlem (MU +4.3R, SKHY +7.9R) en küçük riskle açıldığı
için hesaba neredeyse hiç yansımadı; 17 işlem eşit riskle açılsaydı toplam
+7.7R (işlem başına 500$ riskle ~+3.800$) olurdu, gerçekleşen -123$.

Nasıl:
    risk$  = özsermaye x risk_per_trade_pct
    1R     = giriş - stop (hisse başına)
    adet   = floor(risk$ / 1R)
Tavanlar (hangisi küçükse):
    - Sembolün ağırlık bütçesi (bütçe x ağırlık%) - eski hesap artık TAVAN.
    - Tek pozisyon özsermayenin max_position_pct'inden büyük olamaz.
    - Portföy ısısı: açık pozisyonların (ortalama maliyet - stop) x adet
      toplamı özsermayenin max_portfolio_risk_pct'ini aşamaz. Breakeven'e
      çekilmiş pozisyonun riski 0'dır ve yer açar.

Bu dosya API çağrısı yapmaz (saf fonksiyonlar) - tests/test_risk_sizing.py.
"""

import math
from dataclasses import dataclass

RISK_DEFAULTS = {
    "risk_sizing_enabled": True,
    "risk_per_trade_pct": 0.5,
    "max_position_pct": 20.0,
    "max_portfolio_risk_pct": 5.0,
}

# Stopu olmayan (ya da bilinmeyen) bir pozisyonun ısıya katkısı için
# varsayılan risk oranı - bilinmeyen riski sıfır saymamak için.
UNKNOWN_STOP_RISK_PCT = 3.0


def load_risk_settings(config: dict) -> dict:
    saved = config.get("risk_sizing") or {}
    return {**RISK_DEFAULTS, **{k: v for k, v in saved.items() if v is not None}}


@dataclass
class SizingResult:
    qty: int
    risk_per_share: float
    risk_dollars: float
    explanation: str


def risk_based_qty(
    equity: float, entry_price: float, stop_price: float, risk_per_trade_pct: float,
    max_position_pct: float, remaining_portfolio_risk: float | None = None,
) -> SizingResult:
    """Long pozisyon için risk bazlı adet. stop_price >= entry_price ise 0."""
    per_share = entry_price - stop_price
    if per_share <= 0 or entry_price <= 0 or equity <= 0:
        return SizingResult(0, max(per_share, 0.0), 0.0, "stop girişin altında değil - adet hesaplanamadı")
    risk_dollars = equity * risk_per_trade_pct / 100
    limit_note = f"risk {risk_dollars:,.0f}$ (%{risk_per_trade_pct:g})"
    if remaining_portfolio_risk is not None and remaining_portfolio_risk < risk_dollars:
        risk_dollars = max(0.0, remaining_portfolio_risk)
        limit_note = f"portföy ısısı sınırı - kalan risk {risk_dollars:,.0f}$"
    qty = math.floor(risk_dollars / per_share)
    cap_qty = math.floor(equity * max_position_pct / 100 / entry_price)
    if cap_qty < qty:
        qty = cap_qty
        limit_note += f", tek pozisyon tavanı %{max_position_pct:g}"
    explanation = f"1R={per_share:.2f}$/hisse, {limit_note} -> {qty} adet"
    return SizingResult(qty, per_share, qty * per_share, explanation)


def position_risk(avg_entry: float, qty: float, stop_price: float | None) -> float:
    """Tek bir long pozisyonun stopa kadar olan riski ($); stop giriş
    üstündeyse 0. Stop bilinmiyorsa UNKNOWN_STOP_RISK_PCT varsayılır."""
    if stop_price is None:
        return abs(qty) * avg_entry * UNKNOWN_STOP_RISK_PCT / 100
    return max(0.0, (avg_entry - stop_price) * abs(qty))


def remaining_portfolio_risk(equity: float, max_portfolio_risk_pct: float, open_risk: float) -> float:
    return max(0.0, equity * max_portfolio_risk_pct / 100 - open_risk)


def top_up_qty_cap(
    equity: float, risk_per_trade_pct: float, avg_entry: float, qty: float, stop_price: float,
    add_price: float, remaining_risk: float | None = None,
) -> int:
    """İlave alımda eklenebilecek en fazla adet: pozisyonun toplam riski
    (ortalama - stop) x adet, işlem başına risk$'ı (ve kalan portföy ısısını)
    aşmasın. add_price stopun altındaysa/eşitse 0."""
    per_share = add_price - stop_price
    if per_share <= 0:
        return 0
    room = equity * risk_per_trade_pct / 100 - position_risk(avg_entry, qty, stop_price)
    if remaining_risk is not None:
        room = min(room, remaining_risk)
    return max(0, math.floor(room / per_share))


def apply_risk_cap(risk: dict | None, qty: int, entry_price: float, stop_price: float) -> tuple[int, str | None]:
    """Modüllerin (ORB, RS, Heikin Ashi) market girişleri için ortak tavan:
    kendi nakit payına göre hesaplanmış `qty`'yi risk bazlı adetle sınırlar
    ve kullanılan riski risk["remaining"]'den düşer. risk None ise (risk
    bazlı büyüklük kapalı ya da bağlam alınamadı) qty aynen döner.
    Dönüş: (adet, adet kısıldıysa açıklama yoksa None)."""
    if risk is None or qty <= 0:
        return qty, None
    sizing = risk_based_qty(
        risk["equity"], entry_price, stop_price, risk["risk_per_trade_pct"],
        risk["max_position_pct"], risk["remaining"],
    )
    note = None
    if sizing.qty < qty:
        note = f"risk tavanı {qty} -> {sizing.qty} adet ({sizing.explanation})"
        qty = sizing.qty
    if qty > 0:
        risk["remaining"] -= qty * sizing.risk_per_share
    return qty, note
