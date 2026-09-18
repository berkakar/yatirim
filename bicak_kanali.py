"""
Bıçak Kanalı - üç paralel hat (kılavuz, bıçak, sıfır çizgisi) ve
bunlardan türetilen bir uzatma seviyesi (yeşil çizgi) tespiti:

  - Bar serisinde tepe/dip (pivot) noktaları bulunur (bkz. structure.
    find_pivots).
  - Pivotlar zaman sırasıyla taranıp klasik "maksimum düşüş" (maximum
    drawdown) mantığıyla en büyük genlikli tepe->dip düşüşü bulunur: o
    ana kadar görülen en yüksek tepe referans alınır, ondan sonraki her
    dip için genlik (referans tepe - dip) hesaplanır; en büyük genlik
    kazanır. Bu, aradaki küçük tepe/dip sıçramalarını (ara bacakları)
    "gürültü" sayıp tek bir bütün düşüş trendini (pratikte tipik olarak
    en uzun süreli olanla da örtüşür) "seçilen düşüş trendi" olarak alır.
  - Bu bacağın kapsadığı bar aralığındaki ham dip pivotlarından mutlak en
    düşüğü (tek nokta) ayrılır; kalan (daha az uç) dip pivotlarına (o
    günün en düşük fiyatı) en küçük kareler ile bir doğru fit edilir; bu
    doğrunun eğimi üç hattın ortak eğimini belirler -> bıçak çizgisi.
  - Ayrılan o tek en düşük dip noktasından, bıçak ile aynı eğimde geçen
    paralel doğru -> sıfır çizgisi.
  - Aynı aralıktaki tepe pivotlarına - ama y değeri olarak HER BİRİNİN o
    günkü en düşük fiyatı kullanılarak - yine bıçak eğimi sabit tutulup
    en iyi uyan (intercept'i optimize edilmiş) paralel doğru -> kılavuz.
  - kılavuz ile sıfır çizgisi arasındaki (eğim ortak olduğu için index'ten
    bağımsız, sabit) dikey mesafe, bıçak çizgisinin bu ikisi arasındaki
    konumuna göre iki parçaya bölünür (üst_oran: kılavuz-bıçak,
    alt_oran: bıçak-sıfır çizgisi; toplamları 1.0). Bu iki oranın
    çarpımı (üst_oran * alt_oran), kılavuzun üstüne kanal genişliği
    kadar ötelenmiş ek bir seviye tanımlar -> yeşil çizgi.

Not: ters_fibo.py'deki gibi, bu da elle çizilen görsel bir kurulumun
mekanik bir yaklaşımıdır; pikseline eş çizmez, aynı yöntemi (pivot ->
maksimum düşüş bacağı -> paralel kanal -> türetilmiş oran) takip eder.
Kasıtlı olarak ters_fibo.py'nin yardımcı fonksiyonlarını paylaşmaz - kendi
fit yardımcılarına sahiptir - böylece buy_algorithms.py ve backtest.py
tarafından kullanılan egimli_ters_fibo sinyali bu modülden etkilenmez.
"""

from dataclasses import dataclass

from structure import Bar, Pivot, find_pivots


@dataclass(frozen=True)
class BicakKanali:
    leg_tepe: Pivot
    leg_dip: Pivot
    bicak: tuple[float, float]          # (slope, intercept)
    sifir_cizgisi: tuple[float, float]  # (slope, intercept) - bicak ile aynı eğim
    kilavuz: tuple[float, float]        # (slope, intercept) - bicak ile aynı eğim
    ust_oran: float                     # kılavuz-bıçak arası pay (kanal genişliğine göre)
    alt_oran: float                     # bıçak-sıfır çizgisi arası pay
    turetilmis_oran: float              # ust_oran * alt_oran

    def genislik(self, index: int) -> float:
        """kılavuz ile sıfır çizgisi arasındaki dikey mesafe (eğim ortak
        olduğundan index'ten bağımsız sabit bir değerdir)."""
        k_slope, k_intercept = self.kilavuz
        s_slope, s_intercept = self.sifir_cizgisi
        return (k_slope * index + k_intercept) - (s_slope * index + s_intercept)

    def yesil_cizgi(self, index: int) -> float:
        """kılavuz çizgisinin, türetilmiş_oran * kanal genişliği kadar
        üstüne ötelenmiş uzatma seviyesi."""
        k_slope, k_intercept = self.kilavuz
        kilavuz_level = k_slope * index + k_intercept
        return kilavuz_level + self.turetilmis_oran * self.genislik(index)


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


def _fixed_slope_intercept(points: list[tuple[int, float]], slope: float) -> float:
    """Verilen sabit `slope` için, noktalara en iyi (en küçük kareler)
    uyan intercept: mean(y - slope*x)."""
    return sum(y - slope * x for x, y in points) / len(points)


def find_channel(bars: list[Bar], order: int = 2) -> BicakKanali | None:
    """Bar serisinde bıçak kanalını kurar; yeterli/uygun yapı yoksa None
    döner."""
    pivots = find_pivots(bars, order)

    leg = _select_decline_leg(pivots)
    if leg is None:
        return None
    leg_tepe, leg_dip = leg

    in_range = [p for p in pivots if leg_tepe.index <= p.index <= leg_dip.index]
    dip_pivots = [p for p in in_range if p.kind == "low"]
    tepe_pivots = [p for p in in_range if p.kind == "high"]
    if not tepe_pivots or not dip_pivots:
        return None

    min_dip = min(dip_pivots, key=lambda p: p.price)
    other_dips = [p for p in dip_pivots if p is not min_dip]

    bicak = _linear_fit([(p.index, p.price) for p in other_dips])
    if bicak is None:
        return None
    bicak_slope, bicak_intercept = bicak

    kilavuz_intercept = _fixed_slope_intercept(
        [(p.index, bars[p.index].l) for p in tepe_pivots], bicak_slope
    )
    kilavuz = (bicak_slope, kilavuz_intercept)

    sifir_intercept = min_dip.price - bicak_slope * min_dip.index
    sifir_cizgisi = (bicak_slope, sifir_intercept)

    toplam = kilavuz_intercept - sifir_intercept
    if toplam <= 0:
        return None
    ust_oran = (kilavuz_intercept - bicak_intercept) / toplam
    alt_oran = (bicak_intercept - sifir_intercept) / toplam
    if not (0 <= ust_oran <= 1 and 0 <= alt_oran <= 1):
        # bıçak, sıfır çizgisi ile kılavuz arasında kalmıyor (ör. düşüş
        # sona doğru yavaşlayıp bıçak'ın ekstrapolasyonu, gerçek en dip
        # fiyatın altına sarkıyor) - beklenen sıfır < bıçak < kılavuz
        # sıralaması bozulmuş, kurulum bu bacak için geçersiz.
        return None

    return BicakKanali(
        leg_tepe=leg_tepe, leg_dip=leg_dip,
        bicak=bicak, sifir_cizgisi=sifir_cizgisi, kilavuz=kilavuz,
        ust_oran=ust_oran, alt_oran=alt_oran,
        turetilmis_oran=ust_oran * alt_oran,
    )
