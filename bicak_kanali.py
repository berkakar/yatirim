"""
Bıçak Kanalı - adım adım inşa ediliyor. Şu an sadece 1. adım var:

  - Bar serisinde tepe/dip (pivot) noktaları bulunur (bkz. structure.
    find_pivots).
  - Pivotlar zaman sırasıyla taranıp klasik "maksimum düşüş" (maximum
    drawdown) mantığıyla en büyük genlikli tepe->dip düşüşü bulunur: o
    ana kadar görülen en yüksek tepe referans alınır, ondan sonraki her
    dip için genlik (referans tepe - dip) hesaplanır; en büyük genlik
    kazanır. Bu, aradaki küçük tepe/dip sıçramalarını (ara bacakları)
    "gürültü" sayıp tek bir bütün düşüş trendini "seçilen düşüş trendi"
    olarak alır.
  - Bu bacağın kapsadığı bar aralığındaki tepe pivotlarına (en az 2
    nokta gerekir, o günün en düşük fiyatı kullanılarak) en küçük
    kareler ile bir doğru fit edilir -> kılavuz çizgisi.

Sıradaki adımlar (bıçak çizgisi, sıfır çizgisi, türetilmiş oran/yeşil
çizgi) kılavuz gerçek veride doğrulandıktan sonra eklenecek.
"""

from dataclasses import dataclass

from structure import Bar, Pivot, find_pivots


@dataclass(frozen=True)
class Kilavuz:
    leg_tepe: Pivot
    leg_dip: Pivot
    tepe_pivots: list[Pivot]      # kılavuz'u oluşturan tepe noktaları (en az 2)
    kilavuz: tuple[float, float]  # (slope, intercept)


def _select_decline_leg(pivots: list[Pivot]) -> tuple[Pivot, Pivot] | None:
    """Pivotlar arasında maksimum düşüş (maximum drawdown) mantığıyla en
    büyük genlikli tepe->dip düşüşünü (tepe, dip) olarak döner. `pivots`
    index'e göre sıralı olmalıdır (bkz. structure.find_pivots)."""
    best: tuple[Pivot, Pivot] | None = None
    best_amplitude = 0.0
    current_peak: Pivot | None = None
    for p in pivots:
        if p.kind == "high":
            if current_peak is None or p.price > current_peak.price:
                current_peak = p
        elif current_peak is not None:
            amplitude = current_peak.price - p.price
            if amplitude > best_amplitude:
                best_amplitude = amplitude
                best = (current_peak, p)
    return best


def _linear_fit(points: list[tuple[int, float]]) -> tuple[float, float] | None:
    """En küçük kareler ile y = slope*x + intercept. 2'den az farklı x
    değeri varsa None."""
    n = len(points)
    if n < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / denom
    intercept = mean_y - slope * mean_x
    return slope, intercept


def find_kilavuz(bars: list[Bar], order: int = 2) -> Kilavuz | None:
    """Bar serisinde en büyük genlikli düşüş trendini bulur ve bu
    trendin tepe pivotlarından (en az 2 nokta) kılavuz çizgisini kurar;
    yeterli/uygun yapı yoksa None döner."""
    pivots = find_pivots(bars, order)

    leg = _select_decline_leg(pivots)
    if leg is None:
        return None
    leg_tepe, leg_dip = leg

    tepe_pivots = [
        p for p in pivots
        if leg_tepe.index <= p.index <= leg_dip.index and p.kind == "high"
    ]
    kilavuz = _linear_fit([(p.index, bars[p.index].l) for p in tepe_pivots])
    if kilavuz is None:
        return None

    return Kilavuz(leg_tepe=leg_tepe, leg_dip=leg_dip, tepe_pivots=tepe_pivots, kilavuz=kilavuz)
