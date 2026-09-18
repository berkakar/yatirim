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
  - Bu bacağın kapsadığı bar aralığındaki tepe pivotlarından (en az 2
    nokta gerekir, o günün en yüksek fiyatı kullanılarak) ikisi seçilir:
    en yüksek olan ("en tepe") ve trend boyunca en son oluşan (dibe en
    yakın, kronolojik olarak en son) tepe pivotu ("son tepe"). Bu iki
    noktadan geçen direkt doğru -> kılavuz çizgisi (diğer tepe noktaları
    arasında bir ortalama fit değil) - klasik direnç trend çizgisi
    çekme yöntemine benzer şekilde, tüm düşüş trendini uçtan uca kapsar.

Sıradaki adımlar (bıçak çizgisi, sıfır çizgisi, türetilmiş oran/yeşil
çizgi) kılavuz gerçek veride doğrulandıktan sonra eklenecek.
"""

from dataclasses import dataclass

from structure import Bar, Pivot, find_pivots


@dataclass(frozen=True)
class Kilavuz:
    leg_tepe: Pivot
    leg_dip: Pivot
    tepe_pivots: list[Pivot]                 # trend boyunca bulunan tüm tepe adayları
    kilavuz_noktalari: tuple[Pivot, Pivot]    # kılavuz çizgisini belirleyen 2 nokta (en tepe, son tepe)
    kilavuz: tuple[float, float]              # (slope, intercept) - bu 2 noktadan geçen doğru


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
    """Bar serisinde en büyük genlikli düşüş trendini bulur; bu trendin
    tepe pivotlarından en yükseği ("en tepe") ve kronolojik olarak en
    son oluşanı ("son tepe") seçilip bu iki noktadan geçen direkt doğru
    kılavuz çizgisi olarak kurulur. Yeterli/uygun yapı yoksa None döner."""
    pivots = find_pivots(bars, order)

    leg = _select_decline_leg(pivots)
    if leg is None:
        return None
    leg_tepe, leg_dip = leg

    tepe_pivots = [
        p for p in pivots
        if leg_tepe.index <= p.index <= leg_dip.index and p.kind == "high"
    ]
    if len(tepe_pivots) < 2:
        return None

    # p.price bir "high" pivotu için zaten o günün en yüksek fiyatı (bkz.
    # structure.find_pivots), bars üzerinden ayrıca bakmaya gerek yok.
    en_tepe = max(tepe_pivots, key=lambda p: p.price)
    son_tepe = max((p for p in tepe_pivots if p is not en_tepe), key=lambda p: p.index)

    kilavuz = _linear_fit([(en_tepe.index, en_tepe.price), (son_tepe.index, son_tepe.price)])
    if kilavuz is None:
        return None

    return Kilavuz(
        leg_tepe=leg_tepe, leg_dip=leg_dip, tepe_pivots=tepe_pivots,
        kilavuz_noktalari=(en_tepe, son_tepe), kilavuz=kilavuz,
    )
