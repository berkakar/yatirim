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
    olarak alır. Bu tarama, en güncel bardan geriye doğru `pencere` bar
    ile sınırlanabilir (varsayılan son 30 bar) - amaç, tarihteki en
    büyük düşüş yerine en güncel düşüşü önceliklendirmek; None
    verilirse tüm seri (sınırsız, eski davranış) taranır.
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
  - dip çizgisinin (en dip noktasından geçen, kılavuz ile aynı
    eğimdeki paralel doğru - bıçak çizgisiyle AYNI hat) fiyatla, en
    dip noktasından ÖNCEKİ (soldaki) taraftaki İLK kesiştiği YEŞİL
    (kapanışı açılışından yüksek) bar bulunur ("dip kesişim mumu" -
    bkz. _dip_cizgisi_ilk_yesil_kesisim).
  - dip kesişim mumundan geçmişe dönük - varsayılan olarak (sabit bir
    bar sayısı değil) serinin en başına kadar, yani zaten "kaç gün
    geriye gidilecek" ile sınırlanmış olan bar serisinin tamamında;
    `sifir_pencere` verilirse son o kadar bar içinde - lokal
    minimumlardan (dip pivotlarından) fiyatça en yükseği ("en yüksek
    alım noktası") bulunur; bu barın günlük en düşük fiyatı sıfır
    nokta olarak alınır (bkz. _find_sifir_nokta). Kılavuz ile aynı
    eğimde, bu noktadan geçen paralel doğru -> sıfır çizgisi.
  - kılavuz ile sıfır çizgisi arasındaki (eğim ortak olduğu için
    index'ten bağımsız, sabit) dikey mesafe, bıçak çizgisinin bu ikisi
    arasındaki konumuna göre iki parçaya bölünür (üst_oran: kılavuz-
    bıçak, alt_oran: bıçak-sıfır çizgisi; toplamları 1.0 olması gerekir
    ama zorlanmaz - gerçek veride bıçak bu aralığın dışına da çıkabilir).
  - üst_oran * fib_katsayısı (sabit bir Fibonacci oranı, varsayılan
    0.618) = türetilmiş_oran; kılavuzun, kanal genişliği (kılavuz-sıfır
    çizgisi mesafesi) kadarının türetilmiş_oran'ı kadar ÜSTÜNE (kanalın
    dışına, yukarı) ötelenmiş paralel doğru -> yeşil çizgi (alım
    çizgisi). alt_oran hâlâ hesaplanıp bilgi amaçlı döndürülür ama bu
    formülde kullanılmaz.
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
    sifir_nokta: Pivot                         # dip kesişim mumundan önceki (varsayılan: serinin tamamındaki) en yüksek lokal minimum
    sifir_cizgisi: tuple[float, float]         # (slope, intercept) - kılavuz ile aynı eğim, sifir_nokta'dan geçer
    ust_oran: float                            # kılavuz-bıçak arası pay (kılavuz-sıfır çizgisi mesafesine göre)
    alt_oran: float                            # bıçak-sıfır çizgisi arası pay (bilgi amaçlı - turetilmis_oran'da kullanılmıyor)
    turetilmis_oran: float                     # ust_oran * fib_katsayisi
    yesil_cizgi: tuple[float, float]           # (slope, intercept) - kılavuzun türetilmiş_oran kadar üstü (alım çizgisi)
    dip_kesisim_mumu: Pivot | None             # dip çizgisinin (en dip'ten geçen paralel doğru) en dip'ten ÖNCE İLK kesiştiği yeşil bar - sifir_nokta bu bardan geriye (varsayılan: serinin tamamında) aranır


def _select_decline_leg(pivots: list[Pivot], min_index: int = 0) -> tuple[Pivot, Pivot] | None:
    """Pivotlar arasında maksimum düşüş (maximum drawdown) mantığıyla en
    büyük genlikli tepe->dip düşüşünü (tepe, dip) olarak döner. `pivots`
    index'e göre sıralı olmalıdır (bkz. structure.find_pivots).
    `min_index`'ten küçük index'li pivotlar taramaya dahil edilmez -
    tarama penceresini (bkz. find_kilavuz'daki `pencere`) en güncel
    barlarla sınırlamak için kullanılır."""
    best: tuple[Pivot, Pivot] | None = None
    best_amplitude = 0.0
    current_peak: Pivot | None = None
    for p in pivots:
        if p.index < min_index:
            continue
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


def _dip_cizgisi_ilk_yesil_kesisim(bars: list[Bar], en_dip: Pivot, dip_cizgisi: tuple[float, float]) -> Pivot | None:
    """Dip çizgisinin (en dip noktasından geçen, kılavuz ile aynı
    eğimdeki paralel doğru) fiyatla, en dip noktasından ÖNCEKİ
    barlarda soldan sağa doğru İLK kesiştiği YEŞİL (kapanışı
    açılışından yüksek) barı bulur ("dip kesişim mumu"); hiç böyle bir
    kesişim yoksa None. Arama en dip noktasından ÖNCEKİ barlarla
    sınırlıdır - çizgi kendi tanımı gereği tam olarak en dip'in
    fiyatından geçtiği için, en dip'in kendisi (veya sonrası) sahte
    bir "kesişim" olarak sayılmasın."""
    slope, intercept = dip_cizgisi
    for i in range(0, en_dip.index):
        b = bars[i]
        level = slope * i + intercept
        if b.l <= level <= b.h and b.c > b.o:
            return Pivot(index=i, kind="low", price=level, t=b.t)
    return None


def _find_sifir_nokta(bars: list[Bar], pivots: list[Pivot], kesisim: Pivot,
                       pencere: int | None = None) -> Pivot | None:
    """Dip kesişim mumundan (`kesisim`) geçmişe dönük - `pencere` verilmişse
    son `pencere` bar içindeki, None ise (varsayılan) serinin en başından
    kesişim mumuna kadarki TÜM - lokal minimumlardan (dip pivotlarından)
    fiyatça en yükseğini ("en yüksek alım noktası") döner; hiç dip pivotu
    yoksa None. `pivots`, aynı `bars` üzerinde zaten hesaplanmış olmalı
    (bkz. structure.find_pivots).

    Varsayılan (None, sabit bir bar sayısı değil) davranışta ek bir
    pencere sınırı uygulanmaz - zaten veri çekilirken "kaç gün geriye
    gidilecek" ile sınırlanmış olan bar serisinin tamamı taranır, yani
    arama menzili kullanıcının o mum periyodu için seçtiği gün sayısına
    göre kendiliğinden ölçeklenir."""
    baslangic = 0 if pencere is None else max(0, kesisim.index - pencere)
    adaylar = [p for p in pivots if p.kind == "low" and baslangic <= p.index < kesisim.index]
    if not adaylar:
        return None
    return max(adaylar, key=lambda p: p.price)


def find_kilavuz(bars: list[Bar], order: int = 2, pencere: int | None = 30,
                  sifir_pencere: int | None = None, fib_katsayisi: float = 0.618) -> Kilavuz | None:
    """Bar serisinde en büyük genlikli düşüş trendini bulur; bu trendin
    tepe pivotlarından en yükseği ("en tepe") ve kronolojik olarak en
    son oluşanı ("son tepe") seçilip bu iki noktadan geçen direkt doğru
    kılavuz çizgisi olarak kurulur. Aynı trendin en düşük dip pivotundan
    ("en dip nokta"), kılavuz ile aynı eğimde geçen paralel doğru bıçak
    çizgisi olarak kurulur. Dip çizgisinin (bıçak ile aynı hat) en dip
    noktasından önce ilk kestiği yeşil bardan ("dip kesişim mumu"),
    geçmişe dönük en yüksek lokal minimumdan ("sıfır nokta" - bkz.
    _find_sifir_nokta), yine kılavuz ile aynı eğimde geçen paralel doğru
    sıfır çizgisi olarak kurulur; bu üç hat üzerinden üst_oran/alt_oran
    hesaplanır, türetilmis_oran = üst_oran * fib_katsayısı olarak
    kurulur ve kılavuzun türetilmis_oran kadar üstüne ötelenmiş paralel
    doğru yeşil çizgi (alım çizgisi) olarak kurulur. Yeterli/uygun yapı
    yoksa None döner.

    `pencere`: düşüş bacağı (leg) taraması, en güncel bara göre geriye
    doğru sadece son `pencere` bar içindeki pivotlarla sınırlanır - bu
    en güncel düşüşü, tarihteki en büyük genlikli düşüşe göre önceliklendirir.
    Varsayılan 30 bar. None verilirse tüm seri (sınırsız) taranır.

    `sifir_pencere`: sıfır nokta taraması, dip kesişim mumundan geriye
    doğru sadece son `sifir_pencere` bar içindeki dip pivotlarıyla
    sınırlanır. Varsayılan None - sabit bir bar sayısına değil, zaten
    "kaç gün geriye gidilecek" ile sınırlanmış olan bar serisinin
    tamamına bakılır (bkz. _find_sifir_nokta).

    `fib_katsayisi`: yeşil çizginin kılavuzun ne kadar üstüne
    ötelendiğini belirleyen sabit Fibonacci oranı (varsayılan 0.618).
    üst_oran (bıçağın kılavuz-sıfır aralığındaki konumu) bu sabit
    oranla çarpılarak türetilmis_oran'ı verir - üst_oran küçükse (bıçak
    kılavuza yakın, sığ düzeltme) çizgi kılavuza yakın kalır, üst_oran
    1'e yaklaşırsa (bıçak sıfıra yakın, derin düzeltme) çizgi
    fib_katsayısının tamamına yaklaşır. Not: alt_oran bu hesaba
    katılmıyor (eski üst_oran*alt_oran formülünün yerine geçti), yani
    türetilmis_oran artık ≤0.25 ile sınırlı değil - fib_katsayisi 1'den
    büyük seçilirse (örn. 1.618) çizgi kılavuzun daha da uzağına
    taşabilir."""
    pivots = find_pivots(bars, order)

    min_index = max(0, len(bars) - pencere) if pencere is not None else 0
    leg = _select_decline_leg(pivots, min_index)
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

    dip_kesisim_mumu = _dip_cizgisi_ilk_yesil_kesisim(bars, en_dip, bicak)
    if dip_kesisim_mumu is None:
        return None
    sifir_nokta = _find_sifir_nokta(bars, pivots, dip_kesisim_mumu, sifir_pencere)
    if sifir_nokta is None:
        return None
    sifir_intercept = sifir_nokta.price - kilavuz_slope * sifir_nokta.index
    sifir_cizgisi = (kilavuz_slope, sifir_intercept)

    kilavuz_intercept = kilavuz[1]
    toplam = kilavuz_intercept - sifir_intercept
    if toplam == 0:
        return None
    ust_oran = (kilavuz_intercept - bicak_intercept) / toplam
    alt_oran = (bicak_intercept - sifir_intercept) / toplam
    turetilmis_oran = ust_oran * fib_katsayisi
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
