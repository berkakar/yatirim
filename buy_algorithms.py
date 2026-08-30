"""
Alpaca "premium buy point" hesaplaması için birden fazla, birbirinden bağımsız
algoritma. Her algoritma ortak bir BuySignal döner, böylece hepsi aynı arayüzde
(karşılaştırma tablosu + tek, tüm hisseler için ortak bir "aktif algoritma"
seçimi) kullanılabilir - bkz. premium_buy_portfolio.py (görüntüleme) ve
alpaca_buy_points.py (gerçek emri veren GitHub Action).
"""

from dataclasses import dataclass
from datetime import date, timedelta

from demand_zones import find_buy_point
from indicators import atr, ema
from structure import Bar


@dataclass(frozen=True)
class BuySignal:
    algorithm: str
    price: float
    reason: str
    # "pullback": destek/geri çekilme sinyali - güncel fiyatın üzerinde bir emir
    #   anlamsızdır (anında marketable olur), böyle bir durumda sinyal geçersiz sayılır.
    # "breakout": kırılım sinyali - tanım gereği güncel fiyata yakın/üzerinde oluşur,
    #   bu kontrole tabi değildir.
    style: str


def _bar_date(bar: Bar) -> str:
    return bar.t[:10]


def _session_vwap(bars: list[Bar]) -> float | None:
    """Son barın ait olduğu işlem gününün, gün başından o bara kadarki hacim
    ağırlıklı ortalama fiyatı (VWAP)."""
    if not bars:
        return None
    last_date = _bar_date(bars[-1])
    cum_pv = cum_v = 0.0
    for b in bars:
        if _bar_date(b) != last_date:
            continue
        typical = (b.h + b.l + b.c) / 3
        cum_pv += typical * b.v
        cum_v += b.v
    return cum_pv / cum_v if cum_v > 0 else None


def demand_zone_signal(bars: list[Bar], daily_closes: list[float] | None = None,
                        impulse_pct: float = 0.03, impulse_bars: int = 3, max_taps: int = 2) -> BuySignal | None:
    """Mevcut yöntem: son güçlü yükseliş hareketinden önceki mumun oluşturduğu
    talep bölgesinin üst sınırı (bkz. demand_zones.py)."""
    zone = find_buy_point(bars, impulse_pct, impulse_bars, max_taps)
    if zone is None:
        return None
    return BuySignal(
        algorithm="demand_zone", price=round(zone.top, 2),
        reason=f"Talep bölgesi (dokunuş: {zone.tap_count})", style="pullback",
    )


def trend_pullback_signal(bars: list[Bar], daily_closes: list[float] | None = None,
                           daily_sma_period: int = 200, ema_period: int = 20) -> BuySignal | None:
    """Trend İçi Dinamik Düzeltme: günlük kapanış günlük SMA(200) üzerindeyken
    (ana trend yükselişte), 30 dakikalık grafikte bir barın düşüğü EMA(20) veya
    o günün VWAP'ına değip barın yeşil kapanmasıyla (kapanış seviyenin üzerinde)
    oluşan geri çekilme alım fırsatı."""
    if not daily_closes or len(daily_closes) < daily_sma_period:
        return None
    daily_sma = sum(daily_closes[-daily_sma_period:]) / daily_sma_period
    if daily_closes[-1] <= daily_sma:
        return None  # ana trend yükselişte değil

    closes = [b.c for b in bars]
    if len(closes) < ema_period:
        return None

    last = bars[-1]
    if last.c <= last.o:
        return None  # konfirmasyon mumu yeşil değil

    ema_value = ema(closes, ema_period)
    vwap_value = _session_vwap(bars)

    touched_ema = ema_value is not None and last.l <= ema_value <= last.c
    touched_vwap = vwap_value is not None and last.l <= vwap_value <= last.c
    if not (touched_ema or touched_vwap):
        return None

    level = "EMA20" if touched_ema else "VWAP"
    return BuySignal(
        algorithm="trend_pullback", price=round(last.c, 2),
        reason=f"Günlük trend yükselişte (SMA200 üzeri), 30dk {level} desteğine değip yeşil kapandı",
        style="pullback",
    )


def volatility_support_signal(bars: list[Bar], daily_closes: list[float] | None = None,
                               window_days: int = 7, sma_period: int = 20, atr_period: int = 14,
                               atr_mult: float = 1.5, bb_stddev: float = 2.0) -> BuySignal | None:
    """Oynaklığa Duyarlı Dinamik Destek: son window_days günlük 30 dakikalık
    barlarla hesaplanan SMA(20) - 1.5×ATR(14) seviyesinin altına inme veya
    Bollinger (SMA20 ± 2×std) alt bandına değme."""
    if not bars:
        return None
    cutoff_date = (date.fromisoformat(_bar_date(bars[-1])) - timedelta(days=window_days)).isoformat()
    recent = [b for b in bars if _bar_date(b) >= cutoff_date]

    closes = [b.c for b in recent]
    if len(closes) < sma_period:
        return None

    window_closes = closes[-sma_period:]
    sma20 = sum(window_closes) / sma_period
    variance = sum((c - sma20) ** 2 for c in window_closes) / sma_period
    stddev = variance ** 0.5
    lower_band = sma20 - bb_stddev * stddev

    atr_value = atr(recent, atr_period)
    last = recent[-1]

    atr_trigger = atr_value is not None and last.c <= sma20 - atr_mult * atr_value
    bb_trigger = last.l <= lower_band
    if not (atr_trigger or bb_trigger):
        return None

    reasons = []
    if atr_trigger:
        reasons.append("SMA20-1.5×ATR14 altında")
    if bb_trigger:
        reasons.append("Bollinger alt bandına değdi")
    return BuySignal(
        algorithm="volatility_support", price=round(last.c, 2),
        reason=" ve ".join(reasons), style="pullback",
    )


def breakout_volume_signal(bars: list[Bar], daily_closes: list[float] | None = None,
                            lookback_bars: int = 20, volume_mult: float = 1.5) -> BuySignal | None:
    """Kırılım ve Hacim İvmesi: son barın yükseği, önceki 20 barın en yükseğini
    (Donchian üst bandı) kırıyor ve barın hacmi önceki 20 barın ortalama
    hacminin en az %150'si."""
    if len(bars) < lookback_bars + 1:
        return None
    window = bars[-(lookback_bars + 1):-1]
    last = bars[-1]

    prior_high = max(b.h for b in window)
    prior_avg_vol = sum(b.v for b in window) / len(window)

    if last.h <= prior_high:
        return None
    if prior_avg_vol <= 0 or last.v < volume_mult * prior_avg_vol:
        return None

    return BuySignal(
        algorithm="breakout_volume", price=round(last.c, 2),
        reason=f"{lookback_bars} barlık direnç kırıldı, hacim ortalamanın %{last.v / prior_avg_vol * 100:.0f}'i",
        style="breakout",
    )


ALGORITHMS = {
    "demand_zone": ("Talep Bölgesi (Demand Zone)", demand_zone_signal),
    "trend_pullback": ("Trend İçi Dinamik Düzeltme", trend_pullback_signal),
    "volatility_support": ("Oynaklığa Duyarlı Dinamik Destek", volatility_support_signal),
    "breakout_volume": ("Kırılım + Hacim İvmesi", breakout_volume_signal),
}
DEFAULT_ALGORITHM = "demand_zone"


def compute_all_signals(bars: list[Bar], daily_closes: list[float] | None = None) -> dict[str, BuySignal | None]:
    """Tüm algoritmaların sonucunu {algoritma_id: BuySignal|None} olarak döner."""
    return {algo_id: fn(bars, daily_closes) for algo_id, (_, fn) in ALGORITHMS.items()}


def reject_if_marketable(signal: BuySignal | None, current_price: float) -> BuySignal | None:
    """"pullback" tarzı bir sinyal güncel fiyatın üzerinde/eşitse geçersiz sayılır
    (limit emri anında marketable olur, gerçek bir geri çekilme beklemez).
    "breakout" sinyalleri bu kontrole tabi değildir."""
    if signal is None:
        return None
    if signal.style == "pullback" and signal.price >= current_price:
        return None
    return signal
