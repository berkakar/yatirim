"""
Bıçak Kanalı - adım adım inşa ediliyor. Şu ana kadar tamamlanan adımlar:

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
  - Aynı bacaktaki dip pivotlarından en düşüğü ("en dip nokta") bulunur;
    kılavuz ile aynı eğimde, bu noktadan geçen paralel doğru -> bıçak
    çizgisi.
  - GEÇİCİ OLARAK EN BASİT HALİNE DÖNDÜRÜLDÜ (sıfır noktasının tam
    tanımı üzerinde art arda çok fazla değişiklik yapılıp işler
    karıştığı için): sıfır nokta, bıçak çizgisini belirleyen aynı "en
    dip nokta" (trend boyunca en düşük dip pivotu) olarak alınıyor.
    Kılavuz ile aynı eğimde, bu noktadan geçen paralel doğru -> sıfır
    çizgisi - bu da pratikte bıçak çizgisiyle AYNI çizgi demektir
    (üst_oran=1, alt_oran=0, türetilmiş_oran=0, yeşil çizgi=kılavuz
    çizgisiyle çakışır). Bu, yeniden adım adım netleştirilecek bir
    başlangıç noktasıdır.
  - ADIM ADIM YENİDEN İNŞA - 1. adım: dip çizgisinin (leg_tepe'den
    leg_dip'e çizilen direkt doğru - grafikteki "Trend Çizgisi (Tepe-
    Dip)"; kılavuz ile aynı eğimde DEĞİL) fiyatla soldan sağa doğru
    İLK kesiştiği YEŞİL (kapanışı açılışından yüksek) bar bulunup
    "dip kesişim mumu" olarak işaretlenir (bkz.
    _dip_cizgisi_ilk_yesil_kesisim). Bu adımda henüz sıfır noktası
    hesabına dahil edilmiyor - sadece grafikte gösteriliyor.
  - kılavuz ile sıfır çizgisi arasındaki (eğim ortak olduğu için
    index'ten bağımsız, sabit) dikey mesafe, bıçak çizgisinin bu ikisi
    arasındaki konumuna göre iki parçaya bölünür (üst_oran: kılavuz-
    bıçak, alt_oran: bıçak-sıfır çizgisi; toplamları 1.0 olması gerekir
    ama zorlanmaz - gerçek veride bıçak bu aralığın dışına da çıkabilir).
  - üst_oran * alt_oran = türetilmiş_oran; kılavuzun, kanal genişliği
    (kılavuz-sıfır çizgisi mesafesi) kadarının türetilmiş_oran'ı kadar
    ÜSTÜNE (kanalın dışına, yukarı) ötelenmiş paralel doğru -> yeşil
    çizgi (alım çizgisi).
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
    en_dip: Pivot                             # trend boyunca en düşük dip pivotu
    bicak: tuple[float, float]                # (slope, intercept) - kılavuz ile aynı eğim, en_dip'ten geçer
    sifir_nokta: Pivot                         # GEÇİCİ: en_dip ile aynı (en basit hale döndürüldü)
    sifir_cizgisi: tuple[float, float]         # (slope, intercept) - kılavuz ile aynı eğim, sifir_nokta'dan geçer
    ust_oran: float                            # kılavuz-bıçak arası pay (kılavuz-sıfır çizgisi mesafesine göre)
    alt_oran: float                            # bıçak-sıfır çizgisi arası pay
    turetilmis_oran: float                     # ust_oran * alt_oran
    yesil_cizgi: tuple[float, float]           # (slope, intercept) - kılavuzun türetilmiş_oran kadar üstü (alım çizgisi)
    dip_kesisim_mumu: Pivot | None             # dip çizgisinin soldan sağa İLK kesiştiği yeşil bar (henüz sıfır nokta hesabında kullanılmıyor)


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


def _dip_cizgisi_ilk_yesil_kesisim(bars: list[Bar], leg_tepe: Pivot, leg_dip: Pivot) -> Pivot | None:
    """Dip çizgisinin (leg_tepe'den leg_dip'e çizilen direkt doğru -
    grafikteki "Trend Çizgisi (Tepe-Dip)"; kılavuz ile aynı eğimde
    DEĞİL) fiyatla soldan sağa doğru İLK kesiştiği YEŞİL (kapanışı
    açılışından yüksek) barı bulur ("dip kesişim mumu"); hiç böyle bir
    kesişim yoksa None. Arama leg_tepe'den SONRAKİ barlarla
    sınırlıdır - çizgi kendi tanımı gereği tam olarak leg_tepe'nin
    fiyatından geçtiği için, tepeye giden son (genelde yeşil) yükseliş
    barı sahte bir "kesişim" olarak sayılmasın."""
    dip_cizgisi = _linear_fit([(leg_tepe.index, leg_tepe.price), (leg_dip.index, leg_dip.price)])
    if dip_cizgisi is None:
        return None
    slope, intercept = dip_cizgisi
    for i in range(leg_tepe.index + 1, len(bars)):
        b = bars[i]
        level = slope * i + intercept
        if b.l <= level <= b.h and b.c > b.o:
            return Pivot(index=i, kind="low", price=level, t=b.t)
    return None


def find_kilavuz(bars: list[Bar], order: int = 2) -> Kilavuz | None:
    """Bar serisinde en büyük genlikli düşüş trendini bulur; bu trendin
    tepe pivotlarından en yükseği ("en tepe") ve kronolojik olarak en
    son oluşanı ("son tepe") seçilip bu iki noktadan geçen direkt doğru
    kılavuz çizgisi olarak kurulur. Aynı trendin en düşük dip pivotundan
    ("en dip nokta"), kılavuz ile aynı eğimde geçen paralel doğru bıçak
    çizgisi olarak kurulur. Sıfır nokta GEÇİCİ OLARAK en_dip ile aynı
    alınıyor (bkz. modül docstring'i) - yine kılavuz ile aynı eğimde
    geçen paralel doğru sıfır çizgisi olarak kurulur; bu üç hat
    üzerinden üst_oran/alt_oran/turetilmis_oran hesaplanır ve kılavuzun
    türetilmis_oran kadar üstüne ötelenmiş paralel doğru yeşil çizgi
    (alım çizgisi) olarak kurulur. Yeterli/uygun yapı yoksa None döner."""
    pivots = find_pivots(bars, order)

    leg = _select_decline_leg(pivots)
    if leg is None:
        return None
    leg_tepe, leg_dip = leg

    in_range = [p for p in pivots if leg_tepe.index <= p.index <= leg_dip.index]
    tepe_pivots = [p for p in in_range if p.kind == "high"]
    dip_pivots = [p for p in in_range if p.kind == "low"]
    if len(tepe_pivots) < 2 or not dip_pivots:
        return None

    # p.price bir "high"/"low" pivotu için zaten o günün en yüksek/en
    # düşük fiyatı (bkz. structure.find_pivots), bars üzerinden ayrıca
    # bakmaya gerek yok.
    en_tepe = max(tepe_pivots, key=lambda p: p.price)
    son_tepe = max((p for p in tepe_pivots if p is not en_tepe), key=lambda p: p.index)

    kilavuz = _linear_fit([(en_tepe.index, en_tepe.price), (son_tepe.index, son_tepe.price)])
    if kilavuz is None:
        return None
    kilavuz_slope, _ = kilavuz

    en_dip = min(dip_pivots, key=lambda p: p.price)
    bicak_intercept = en_dip.price - kilavuz_slope * en_dip.index
    bicak = (kilavuz_slope, bicak_intercept)

    sifir_nokta = en_dip
    sifir_intercept = bicak_intercept
    sifir_cizgisi = (kilavuz_slope, sifir_intercept)

    dip_kesisim_mumu = _dip_cizgisi_ilk_yesil_kesisim(bars, leg_tepe, leg_dip)

    kilavuz_intercept = kilavuz[1]
    toplam = kilavuz_intercept - sifir_intercept
    if toplam == 0:
        return None
    ust_oran = (kilavuz_intercept - bicak_intercept) / toplam
    alt_oran = (bicak_intercept - sifir_intercept) / toplam
    turetilmis_oran = ust_oran * alt_oran
    yesil_cizgi = (kilavuz_slope, kilavuz_intercept + turetilmis_oran * toplam)

    return Kilavuz(
        leg_tepe=leg_tepe, leg_dip=leg_dip, tepe_pivots=tepe_pivots,
        kilavuz_noktalari=(en_tepe, son_tepe), kilavuz=kilavuz,
        en_dip=en_dip, bicak=bicak,
        sifir_nokta=sifir_nokta, sifir_cizgisi=sifir_cizgisi,
        ust_oran=ust_oran, alt_oran=alt_oran, turetilmis_oran=turetilmis_oran,
        yesil_cizgi=yesil_cizgi, dip_kesisim_mumu=dip_kesisim_mumu,
    )


def yesil_cizgi_kesisimi(bars: list[Bar], result: Kilavuz) -> Pivot | None:
    """Yeşil çizginin (alım çizgisi) bar verisiyle en son (en güncel)
    kesiştiği barı bulur: o bardaki günlük [en düşük, en yüksek]
    aralığı, çizginin o bardaki değerini içeriyorsa "kesişim" sayılır.
    Kesişim barındaki çizgi değeri fiyat, bar index'i ve tarihiyle
    birlikte bir Pivot olarak döner; hiç kesişim yoksa None."""
    slope, intercept = result.yesil_cizgi
    kesisim: Pivot | None = None
    for i, b in enumerate(bars):
        level = slope * i + intercept
        if b.l <= level <= b.h:
            kesisim = Pivot(index=i, kind="low", price=level, t=b.t)
    return kesisim
