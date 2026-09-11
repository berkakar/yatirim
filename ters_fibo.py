"""
Ters Fibo ("eğik/ters Fibonacci kanalı") tespiti - ekran görüntüsündeki
"Gölge Teorisi" kurulumunun mekanik bir yaklaşımı:

  - Bar serisinde tepe/dip (pivot) noktaları bulunur (bkz. structure.
    find_pivots) ve ardışık aynı yönlü pivotlar birleştirilip alterne bir
    zigzag (tepe, dip, tepe, dip, ...) elde edilir.
  - Her dip için, ona giden düşüş bacağının (önceki tepe -> bu dip) kaç
    mumda oluştuğu, o tepeye giden yükseliş bacağının (önceki dip ->
    tepe) mum sayısıyla karşılaştırılır; sadece bu ikisi ±1 mum içinde
    örtüşüyorsa dip "geçerli" sayılır (rastgele/gürültülü dipleri eler).
  - Geçerli dipleri birleştiren bir destek hattı ve tüm tepeleri
    birleştiren bir direnç hattı en küçük kareler ile bulunur; bu iki
    hattın kesiştiği nokta "dönüm noktası" (turning point) olarak alınır.
  - Dönüm noktasından, pencerede en solda kalan ilk tepeye bir hat
    çizilir. Bu hat 0. (ilk tepe) ve dönüm noktasını birleştiren referans
    hattıdır; aynı eğimde, dikeyde bu iki nokta arasındaki mesafenin
    Fibonacci oranları kadar ötelenmiş paralel hatlar bir kanal
    oluşturur. Standart (yatay) Fibonacci bantlarının aksine bantlar bir
    trend hattının eğimini takip ettiği için "Ters Fibo" adı kullanılıyor.

Not: demand_zones.py'deki benzer nota bakınız - bu, elle çizilen, görsel
yargıya dayanan bir kurulumun mekanik bir yaklaşımıdır; fotoğraftaki
çizimi piksel piksel eşlemez, aynı yöntemi (tepe/dip simetrisi -> dönüm
noktası -> eğik Fibonacci kanalı) takip eder.
"""

from dataclasses import dataclass

from structure import Bar, Pivot, find_pivots


@dataclass(frozen=True)
class TersFiboChannel:
    turning_index: float
    turning_price: float
    first_peak_index: int
    first_peak_price: float
    slope: float      # kanalın eğimi (fiyat / bar)
    amplitude: float  # first_peak_price - turning_price (> 0)

    def level(self, ratio: float, index: int) -> float:
        """Kanalın 0. hattından (ilk tepe - dönüm noktası referans hattı)
        `ratio` kadar (amplitude cinsinden) aşağı ötelenmiş paralel
        hattın, verilen bar index'indeki fiyat seviyesi."""
        base = self.first_peak_price + self.slope * (index - self.first_peak_index)
        return base - ratio * self.amplitude


def _zigzag(pivots: list[Pivot]) -> list[Pivot]:
    """Ardışık aynı yönlü pivotları (üst üste iki tepe/iki dip) daha
    belirgin (daha yüksek tepe / daha düşük dip) olanla birleştirip
    tepe/dip dizisini alterne hale getirir."""
    zz: list[Pivot] = []
    for p in pivots:
        if zz and zz[-1].kind == p.kind:
            better = (p.price > zz[-1].price) if p.kind == "high" else (p.price < zz[-1].price)
            if better:
                zz[-1] = p
        else:
            zz.append(p)
    return zz


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


def find_channel(bars: list[Bar], order: int = 2, symmetry_tolerance: int = 1) -> TersFiboChannel | None:
    """Verilen bar serisinde Ters Fibo kanalını kurar, yeterli/uygun yapı
    yoksa None döner."""
    pivots = find_pivots(bars, order)
    zz = _zigzag(pivots)
    highs = [p for p in zz if p.kind == "high"]
    if len(highs) < 2:
        return None

    validated_lows: list[Pivot] = []
    for i in range(2, len(zz)):
        if zz[i].kind != "low" or zz[i - 1].kind != "high" or zz[i - 2].kind != "low":
            continue
        up_leg = zz[i - 1].index - zz[i - 2].index
        down_leg = zz[i].index - zz[i - 1].index
        if abs(down_leg - up_leg) <= symmetry_tolerance:
            validated_lows.append(zz[i])
    if zz[0].kind == "low" and zz[0] not in validated_lows:
        # İlk dip'in karşılaştırılacak önceki bir bacağı yok - referans
        # noktası olarak doğrudan kabul edilir.
        validated_lows.insert(0, zz[0])
    if len(validated_lows) < 2:
        return None

    dip_fit = _linear_fit([(p.index, p.price) for p in validated_lows])
    peak_fit = _linear_fit([(p.index, p.price) for p in highs])
    if dip_fit is None or peak_fit is None:
        return None
    dip_slope, dip_intercept = dip_fit
    peak_slope, peak_intercept = peak_fit
    if abs(dip_slope - peak_slope) < 1e-9:
        return None  # dip ve tepe hatları paralel - kesişim (dönüm noktası) yok

    turning_index = (peak_intercept - dip_intercept) / (dip_slope - peak_slope)
    turning_price = dip_slope * turning_index + dip_intercept

    first_peak = highs[0]
    if first_peak.index == turning_index:
        return None
    amplitude = first_peak.price - turning_price
    if amplitude <= 0:
        return None  # beklenen yapı (ilk tepe, dönüm noktasından yüksek) bozulmuş

    channel_slope = (first_peak.price - turning_price) / (first_peak.index - turning_index)
    return TersFiboChannel(
        turning_index=turning_index, turning_price=turning_price,
        first_peak_index=first_peak.index, first_peak_price=first_peak.price,
        slope=channel_slope, amplitude=amplitude,
    )
