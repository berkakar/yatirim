"""Stop-loss hesaplaması için birden fazla, birbirinden bağımsız algoritma.
buy_algorithms.py'deki desenle aynı: her algoritma ortak bir arayüzde
tanımlanır ve STOP_ALGORITHMS sözlüğünde toplanır, böylece canlı sistem
(alpaca_trailing_stop.py) ve backtest (backtest_engine.py) aynı seçili
algoritmayı çalıştırabilir.

Bir stop-loss algoritması iki farklı anda karar verir, bu yüzden her biri
iki fonksiyondan oluşuyor (buy_algorithms'daki tek `fn(bars) -> BuySignal`
yerine):
  - initial_stop: pozisyon yeni açıldığında (henüz resting bir stop yokken)
    ilk stop seviyesini belirler.
  - trail: resting bir stop zaten varken, onu SIKILAŞTIRMAK için bir aday
    üretir. "Asla gevşetme" kuralı algoritmanın kendisinde değil, çağıran
    tarafta (mevcut current_stop_price ile karşılaştırılarak) uygulanır -
    her algoritma bunu tekrar yazmak zorunda kalmasın diye.

Her parametrenin (initial_stop_pct, breakeven_trigger_pct, atr_period, ...)
KOD içindeki varsayılanı burada, ilgili fonksiyonun kendi imza değeri olarak
yaşar. Kullanıcı Stop Loss Ayarları sayfasında (stop_loss_settings.py) bir
değeri değiştirip kaydederse, bu override'lar stop_loss_settings_<kullanıcı>.json
dosyasında tutulur ve resolve_kwargs() ile - fonksiyonun imzasını inceleyerek,
elle her parametreyi tek tek eşlemeden - çağıranlara (alpaca_trailing_stop.
manage_position, backtest_engine.run_backtest, alpaca_buy_points.py) geçirilecek
kwargs'a dönüştürülür. Kaydedilmiş bir değer yoksa (dosya hiç yok, ya da o
alan hiç değiştirilmemiş) ilgili fonksiyonun kod-varsayılanı geçerli olur -
yeni bir algoritma/parametre eklendiğinde resolve_kwargs'ta HİÇBİR değişiklik
gerekmez.

Altı algoritma var (beşincisi, "atr_volatility", 2026-09-28 emir analizinin
1-2. önerileriyle; altıncısı, "adaptive_dynamic", 2026-10-05'te eklendi -
dosyanın sonundaki bölüm notlarına bakın):
  - "breakeven_atr_structure" (DEFAULT_STOP_ALGORITHM): sabit-% ilk stop +
    breakeven floor + günlük EMA trend filtresiyle gate'lenen ATR-buffered
    break-of-structure trail.
  - "wait_then_trail" ("Beklemeli ve İz Süren Stop"): sabit-% ilk stop +
    breakeven floor, SONRA fiyat maliyetin belirli bir kâr eşiğine
    ulaşana kadar (yapısal trail olmadan) beklenir; eşik bir kez aşıldığında
    (kalıcı olarak) stop bir kâr kilidine çekilir ve yukarıdakiyle AYNI
    yapısal trail (_structure_trail_candidate) devreye girer.
  - "opening_range" ("Açılış Aralığı (ORB) Stop"): sabit-% yerine, pozisyonun
    açıldığı seansın İLK barının (buy_algorithms.orb_signal'ın "açılış
    aralığı" saydığı bar) ters ucuna (long için low) küçük bir tamponla
    kurulan yapısal ilk stop - buy_algorithms.orb_signal ile birlikte
    kullanılmak üzere tasarlandı. Trail, breakeven_atr_structure_trail ile
    BİREBİR AYNI (ayrı bir trail fonksiyonu yok - ilk stop yerleştirildikten
    sonra "yapısal trail" kavramı zaten algoritmadan bağımsız). `bars`
    verilmezse (ör. bazı fallback/top-up çağrı yolları henüz bunu
    geçirmiyor - bkz. alpaca_trailing_stop.manage_position'ın "stopsuz
    pozisyon" fallback'i) ya da hesaplanan seviye entry_price'ın yanlış
    tarafında kalırsa (ör. bu stop, ORB dışı bir algoritmanın sinyaliyle
    seçildiğinde), breakeven_atr_structure_initial_stop ile aynı sabit-%
    düşüşe güvenli şekilde geri düşer.
  - "heikin_ashi_exit" ("Heikin Ashi Çıkışı"): buy_algorithms.
    heikin_ashi_stoch_signal ile eşleşir - sinyal barının low'una yapısal
    ilk stop; ilk kırmızı HA mumu / Stokastik aşırı alım kesişiminde stop
    son kapanışın hemen altına çekilir (bkz. aşağıdaki bölüm notu).
  - "atr_volatility" ("Oynaklık (ATR) Stop"): ATR bazlı ilk stop, R bazlı
    breakeven, sabit çarpanlı chandelier trail.
  - "adaptive_dynamic" ("Akıllı Dinamik Stop"): swing low'a dayalı, ATR
    bandına sıkıştırılmış ilk stop + kâr büyüdükçe daralan chandelier.
"""

import inspect
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from heikin_ashi import long_exit_reason as heikin_ashi_long_exit_reason
from indicators import atr, ema
from structure import Bar, find_pivots, validated_trailing_level


@dataclass(frozen=True)
class StopContext:
    """trail() algoritmalarının ihtiyaç duyduğu ortak girdiler - hem canlı
    sistem hem backtest tarafından aynı şekilde doldurulur."""
    side: str  # "long" or "short"
    entry_price: float
    current_stop_price: float
    bars: list[Bar]  # pozisyon yönetim başlangıcından bu yana barlar, sonuncusu güncel bar
    daily_closes: list[float] | None = None  # trend filtresi için, artan sırada kapanışlar
    topped_up: bool = False
    top_up_stop_mode: str = "keep"
    # [2026-09-28 · Öneri 1-2] Pozisyonun İLK stop seviyesi - 1R'nin (giriş -
    # ilk stop) hesaplanabilmesi için. Canlıda alpaca_trailing_stop, sembolün
    # en eski stop emrinden okur; backtest, pozisyonu açarken kurduğu stoptan.
    # None ise R kullanan algoritmalar (atr_volatility) kendi tahminine düşer.
    initial_stop_price: float | None = None
    # [2026-09-28 · Öneri 1] Giriş ÖNCESİNİ de içeren daha uzun bar penceresi -
    # SADECE ATR hesabı için. `bars` pozisyon yönetim başlangıcından (≈ giriş)
    # başladığı için günlük periyotta ilk 2-3 hafta ATR(14) hiç
    # hesaplanamıyordu; yapısal analiz yine `bars` üzerinden yapılır.
    history_bars: list[Bar] | None = None


@dataclass(frozen=True)
class StopDecision:
    price: float
    reason: str


@dataclass(frozen=True)
class StopAlgorithm:
    label: str
    initial_stop: Callable[..., float]
    trail: Callable[..., "StopDecision | None"]


_CTX_PARAM_NAMES = frozenset({"ctx", "entry_price", "side", "bars"})
_INT_PARAM_NAMES = frozenset({"atr_period", "trend_ema_period", "swing_order", "stoch_k_period", "stoch_d_period", "exit_red_candles",
                              "structure_lookback", "er_period"})


def resolve_kwargs(fn: Callable, settings_for_algo: dict, shared_settings: dict) -> dict:
    """Stop Loss Ayarları sayfasında (stop_loss_settings.py) kaydedilmiş
    değerlerden, `fn` (bir algoritmanın initial_stop veya trail'i) için
    geçirilecek kwargs'ı kurar - `fn`'in İMZASINI inceler, elle bir eşleme
    listesi tutmaz, böylece yeni bir algoritma/parametre eklendiğinde bu
    fonksiyon hiç değişmeden çalışmaya devam eder.

    Her parametre için önce settings_for_algo'da (o algoritmaya özgü,
    ör. "wait_then_trail"), yoksa shared_settings'te (atr_period gibi
    algoritmalar arası paylaşılan) bir değer arar; ikisinde de yoksa o
    parametre hiç kwargs'a eklenmez - fn'in kendi kod-varsayılanı geçerli
    olur. "_pct" ile biten adlar kullanıcı tarafından yüzde olarak girildiği
    için (ör. "1.5" -> 0.015) 100'e bölünür; atr_period/trend_ema_period/
    swing_order int'e, diğerleri (atr_multiplier, stale_reference_days gibi)
    float'a çevrilir. ctx/entry_price/side/bars (StopContext'ten ya da
    doğrudan çağrıdan gelen asıl girdiler) hiç dokunulmaz."""
    kwargs: dict = {}
    for name in inspect.signature(fn).parameters:
        if name in _CTX_PARAM_NAMES:
            continue
        if name in settings_for_algo:
            raw = settings_for_algo[name]
        elif name in shared_settings:
            raw = shared_settings[name]
        else:
            continue
        if raw is None:
            continue
        if name.endswith("_pct"):
            kwargs[name] = float(raw) / 100
        elif name in _INT_PARAM_NAMES:
            kwargs[name] = int(raw)
        else:
            kwargs[name] = float(raw)
    return kwargs


# ---- Varsayılan algoritma: sabit-% ilk stop + breakeven floor + günlük EMA
# trend filtresiyle gate'lenen ATR-buffered break-of-structure trail. Bu,
# alpaca_trailing_stop.manage_position() ve backtest_engine.run_backtest()
# içine daha önce hardcoded olan TEK mantıkla birebir aynı davranışı taşır -
# bkz. alpaca_trailing_stop.py modül docstring'i (adım 2-5).

INITIAL_STOP_PCT = 0.015
ATR_PERIOD = 14
ATR_MULTIPLIER = 0.25
# [2026-09-28 · Öneri 2] %1 -> %1.5 (= 1R, çünkü ilk stop %1.5): eskiden stop,
# fiyat daha 0.67R kâr görmeden girişe çekiliyordu; MSFT/NOW/SKHY/NVDA'da
# +%2-4.6 kâr görmüş işlemler normal gün içi dalgalanmayla başa baş ya da
# zararla kapandı (bkz. Algo Analiz > Değişiklik Günlüğü sayfası).
BREAKEVEN_TRIGGER_PCT = 0.015
# [2026-09-28 · Öneri 2] Breakeven stopu tam girişe değil, girişin bu kadar
# üstüne kurulur: açılış boşluğunda stop piyasa fiyatından dolduğunda
# (MSFT: giriş 494.44, çıkış 492.52) "başa baş" zarara dönmesin.
BREAKEVEN_BUFFER_PCT = 0.002
STALE_REFERENCE_DAYS = 10.0
TREND_EMA_PERIOD = 50
SWING_ORDER = 2
FALLBACK_BUFFER_PCT = 0.001  # only used if ATR can't be computed yet (too few bars)


def _trend_ok(daily_closes: list[float] | None, side: str, trend_ema_period: int) -> bool:
    """Günlük EMA'ya göre trend filtresi - yeterli günlük veri yoksa ya da
    filtre kapalıysa (period<=0) trail'i engellemez (True döner)."""
    if trend_ema_period <= 0 or not daily_closes:
        return True
    trend_ema = ema(daily_closes, trend_ema_period)
    if trend_ema is None:
        return True
    last_close = daily_closes[-1]
    return last_close > trend_ema if side == "long" else last_close < trend_ema


def _breakeven_candidate(
    ctx: StopContext, breakeven_trigger_pct: float, breakeven_buffer_pct: float,
) -> tuple[float, str] | None:
    """Fiyat girişin breakeven_trigger_pct kadar lehine geçtiyse, girişin
    breakeven_buffer_pct kadar ötesine (long için üstüne) bir breakeven adayı.
    [2026-09-28 · Öneri 2] Önceden aday tam giriş fiyatıydı (tampon yoktu) ve
    aynı kod breakeven_atr_structure/wait_then_trail'de iki kez yazılıydı."""
    last_price = ctx.bars[-1].c
    if ctx.side == "long":
        gain_pct = (last_price - ctx.entry_price) / ctx.entry_price
        level = ctx.entry_price * (1 + breakeven_buffer_pct)
        if gain_pct >= breakeven_trigger_pct and ctx.current_stop_price < level < last_price:
            return level, "breakeven"
        return None
    gain_pct = (ctx.entry_price - last_price) / ctx.entry_price
    level = ctx.entry_price * (1 - breakeven_buffer_pct)
    if gain_pct >= breakeven_trigger_pct and ctx.current_stop_price > level > last_price:
        return level, "breakeven"
    return None


def _structure_trail_candidate(
    ctx: StopContext, atr_period: int, atr_multiplier: float, stale_reference_days: float,
    trend_ema_period: int, swing_order: int, fallback_buffer_pct: float,
) -> tuple[float, str] | None:
    """ATR-buffered break-of-structure trail adayı (bkz. structure.
    validated_trailing_level), günlük EMA trend filtresiyle gate'lenir.
    breakeven_atr_structure_trail ile wait_then_trail_trail arasında
    PAYLAŞILAN tek mantık - ikincisi, kullanıcının "o zaten ilk stop
    algoritmasında tanımlı" dediği aynı yapısal trail'i, sadece kâr kilidi
    tetiklendikten sonra devreye sokmak için bunu çağırır."""
    if not _trend_ok(ctx.daily_closes, ctx.side, trend_ema_period):
        return None
    pivot = validated_trailing_level(ctx.bars, ctx.side, swing_order, stale_reference_days)
    if pivot is None:
        return None
    atr_value = atr(ctx.bars, atr_period)
    buffer_amount = atr_value * atr_multiplier if atr_value is not None else pivot.price * fallback_buffer_pct
    last_price = ctx.bars[-1].c
    if ctx.side == "long":
        candidate = pivot.price - buffer_amount
        return (candidate, f"structure@{pivot.price:.2f}") if candidate < last_price else None
    candidate = pivot.price + buffer_amount
    return (candidate, f"structure@{pivot.price:.2f}") if candidate > last_price else None


def breakeven_atr_structure_initial_stop(
    entry_price: float, side: str, bars: list[Bar] | None = None,
    initial_stop_pct: float = INITIAL_STOP_PCT,
) -> float:
    return entry_price * (1 - initial_stop_pct) if side == "long" else entry_price * (1 + initial_stop_pct)


def breakeven_atr_structure_trail(
    ctx: StopContext,
    initial_stop_pct: float = INITIAL_STOP_PCT,
    atr_period: int = ATR_PERIOD,
    atr_multiplier: float = ATR_MULTIPLIER,
    breakeven_trigger_pct: float = BREAKEVEN_TRIGGER_PCT,
    stale_reference_days: float = STALE_REFERENCE_DAYS,
    trend_ema_period: int = TREND_EMA_PERIOD,
    swing_order: int = SWING_ORDER,
    fallback_buffer_pct: float = FALLBACK_BUFFER_PCT,
    breakeven_buffer_pct: float = BREAKEVEN_BUFFER_PCT,
) -> StopDecision | None:
    """Breakeven floor + (top-up sonrası "tighten_to_new_entry" seçiliyse)
    yeni ortalamaya göre nefes payı adayı + günlük EMA trend filtresiyle
    gate'lenen ATR-buffered break-of-structure trail. Adaylardan sadece en
    çok sıkılaştıran, mevcut fiyatın doğru tarafında kalan seçilir."""
    if not ctx.bars:
        return None
    side = ctx.side
    last_price = ctx.bars[-1].c
    candidates: list[tuple[float, str]] = []

    breakeven = _breakeven_candidate(ctx, breakeven_trigger_pct, breakeven_buffer_pct)
    if breakeven is not None:
        candidates.append(breakeven)

    if ctx.topped_up and ctx.top_up_stop_mode == "tighten_to_new_entry":
        if side == "long":
            top_up_candidate = ctx.entry_price * (1 - initial_stop_pct)
            if top_up_candidate < last_price:
                candidates.append((top_up_candidate, "top-up nefes payı"))
        else:
            top_up_candidate = ctx.entry_price * (1 + initial_stop_pct)
            if top_up_candidate > last_price:
                candidates.append((top_up_candidate, "top-up nefes payı"))

    structure_candidate = _structure_trail_candidate(
        ctx, atr_period, atr_multiplier, stale_reference_days, trend_ema_period, swing_order, fallback_buffer_pct,
    )
    if structure_candidate is not None:
        candidates.append(structure_candidate)

    if not candidates:
        return None

    if side == "long":
        best_price, reason = max(candidates, key=lambda c: c[0])
        improves = best_price > ctx.current_stop_price
    else:
        best_price, reason = min(candidates, key=lambda c: c[0])
        improves = best_price < ctx.current_stop_price

    if not improves:
        return None
    return StopDecision(price=best_price, reason=reason)


# ---- İkinci algoritma: "Beklemeli ve İz Süren Stop". Sabit-% ilk stop
# (varsayılan %2), fiyat maliyetin +%1.5'ine ulaşınca breakeven'e çekilir.
# Oradan sonra - yapısal trail HENÜZ DEVREDE DEĞİL, sadece bekleniyor -
# fiyat maliyetin +%5'ine ulaşana kadar mum mum izlenir. Fiyat bir kez
# +%5'e ulaştıysa (sonradan geri çekilse bile - bkz. kullanıcı onayı: bu
# kalıcı bir geçiş), stop maliyetin +%4'üne kilitlenir VE bu noktadan
# itibaren yukarıdaki breakeven_atr_structure_trail ile birebir aynı
# ATR-buffered break-of-structure trail (_structure_trail_candidate)
# devreye girer - "o zaten ilk stop algoritmasında tanımlı".

WAIT_THEN_TRAIL_INITIAL_STOP_PCT = 0.02
WAIT_THEN_TRAIL_BREAKEVEN_TRIGGER_PCT = 0.015
WAIT_THEN_TRAIL_PROFIT_LOCK_TRIGGER_PCT = 0.05
WAIT_THEN_TRAIL_PROFIT_LOCK_PCT = 0.04


def wait_then_trail_initial_stop(
    entry_price: float, side: str, bars: list[Bar] | None = None,
    initial_stop_pct: float = WAIT_THEN_TRAIL_INITIAL_STOP_PCT,
) -> float:
    return entry_price * (1 - initial_stop_pct) if side == "long" else entry_price * (1 + initial_stop_pct)


def _reached_profit_lock(ctx: StopContext, profit_lock_trigger_pct: float) -> bool:
    """Fiyat, pozisyon yönetim başlangıcından bu yana (ctx.bars) HERHANGİ bir
    anda giriş fiyatının +profit_lock_trigger_pct kadar lehine hareket etmiş
    mi - bir kez True olduysa, sonraki barlarda fiyat geri çekilse bile aynı
    kalır (kalıcı geçiş): en yüksek/düşük fiyat ctx.bars'ın TAMAMINDAN
    hesaplanır, sadece son bardan değil."""
    if ctx.side == "long":
        favorable_excursion = max(b.h for b in ctx.bars)
        return favorable_excursion >= ctx.entry_price * (1 + profit_lock_trigger_pct)
    favorable_excursion = min(b.l for b in ctx.bars)
    return favorable_excursion <= ctx.entry_price * (1 - profit_lock_trigger_pct)


def wait_then_trail_trail(
    ctx: StopContext,
    initial_stop_pct: float = WAIT_THEN_TRAIL_INITIAL_STOP_PCT,
    breakeven_trigger_pct: float = WAIT_THEN_TRAIL_BREAKEVEN_TRIGGER_PCT,
    profit_lock_trigger_pct: float = WAIT_THEN_TRAIL_PROFIT_LOCK_TRIGGER_PCT,
    profit_lock_pct: float = WAIT_THEN_TRAIL_PROFIT_LOCK_PCT,
    atr_period: int = ATR_PERIOD,
    atr_multiplier: float = ATR_MULTIPLIER,
    stale_reference_days: float = STALE_REFERENCE_DAYS,
    trend_ema_period: int = TREND_EMA_PERIOD,
    swing_order: int = SWING_ORDER,
    fallback_buffer_pct: float = FALLBACK_BUFFER_PCT,
    breakeven_buffer_pct: float = BREAKEVEN_BUFFER_PCT,
) -> StopDecision | None:
    """Beklemeli ve İz Süren Stop: breakeven floor (+%1.5 tetikte) + (top-up
    sonrası "tighten_to_new_entry" seçiliyse) nefes payı adayı - buraya kadar
    breakeven_atr_structure_trail ile aynı mantık, sadece farklı yüzdelerle.
    Ayrıca: fiyat bir kez +%5 kâra ulaştıysa, +%4 kâr kilidi adayı VE
    breakeven_atr_structure_trail'deki aynı yapısal trail adayı devreye
    girer - o eşiğe ulaşılana kadar (sadece breakeven hariç) hiçbir
    sıkılaştırma yapılmaz, "bekleme" budur."""
    if not ctx.bars:
        return None
    side = ctx.side
    last_price = ctx.bars[-1].c
    candidates: list[tuple[float, str]] = []

    breakeven = _breakeven_candidate(ctx, breakeven_trigger_pct, breakeven_buffer_pct)
    if breakeven is not None:
        candidates.append(breakeven)

    if ctx.topped_up and ctx.top_up_stop_mode == "tighten_to_new_entry":
        if side == "long":
            top_up_candidate = ctx.entry_price * (1 - initial_stop_pct)
            if top_up_candidate < last_price:
                candidates.append((top_up_candidate, "top-up nefes payı"))
        else:
            top_up_candidate = ctx.entry_price * (1 + initial_stop_pct)
            if top_up_candidate > last_price:
                candidates.append((top_up_candidate, "top-up nefes payı"))

    if _reached_profit_lock(ctx, profit_lock_trigger_pct):
        if side == "long":
            lock_price = ctx.entry_price * (1 + profit_lock_pct)
            if lock_price < last_price:
                candidates.append((lock_price, f"kâr kilidi (+%{profit_lock_pct * 100:g})"))
        else:
            lock_price = ctx.entry_price * (1 - profit_lock_pct)
            if lock_price > last_price:
                candidates.append((lock_price, f"kâr kilidi (-%{profit_lock_pct * 100:g})"))

        structure_candidate = _structure_trail_candidate(
            ctx, atr_period, atr_multiplier, stale_reference_days, trend_ema_period, swing_order, fallback_buffer_pct,
        )
        if structure_candidate is not None:
            candidates.append(structure_candidate)

    if not candidates:
        return None

    if side == "long":
        best_price, reason = max(candidates, key=lambda c: c[0])
        improves = best_price > ctx.current_stop_price
    else:
        best_price, reason = min(candidates, key=lambda c: c[0])
        improves = best_price < ctx.current_stop_price

    if not improves:
        return None
    return StopDecision(price=best_price, reason=reason)


# ---- Üçüncü algoritma: "Açılış Aralığı (ORB) Stop". buy_algorithms.orb_signal
# ile eşleşmek üzere tasarlandı - ilk stop, sabit bir yüzde yerine pozisyonun
# açıldığı seansın açılış barının ters ucundan (long için low) küçük bir
# tamponla kurulur: fiyat kırılımdan sonra tekrar açılış aralığının İÇİNE
# dönerse kırılım tezi zaten geçersiz kalmıştır. Trail aşamasında ayrı bir
# mantık yok - breakeven_atr_structure_trail'i olduğu gibi kullanır.

ORB_STOP_BUFFER_PCT = 0.002
# breakeven_atr_structure_trail'in paylaşılan TREND_EMA_PERIOD'undan (50)
# BİLEREK farklı: günlük EMA50 trend filtresi, pozisyon/swing-trade mantığından
# geliyor - ORB gibi gün-içi bir kırılım stratejisi zaten sadece o günün
# momentumuna göre alınıyor, günlük trendin "doğru tarafında" olması şart
# değil. Filtre açıkken (trend uygun değilse) yapısal trail tamamen devre
# dışı kalıp pozisyon sadece breakeven'de korunuyordu (gözlemlenen gerçek
# örnek: OMCL, 2026-09-26, fiyat girişin %3.69 üzerindeyken stop hâlâ tam
# breakeven'deydi). 0 = filtre kapalı, sadece 15dk'lık swing-low yapısı
# temel alınır - resolve_kwargs bunu stop_settings["opening_range"]["trend_ema_period"]
# üzerinden PAYLAŞILAN (breakeven_atr_structure/wait_then_trail'in kullandığı)
# değerden bağımsız uygular (bkz. stop_loss_settings.py).
ORB_TREND_EMA_PERIOD = 0


def opening_range_initial_stop(
    entry_price: float, side: str, bars: list[Bar] | None = None,
    buffer_pct: float = ORB_STOP_BUFFER_PCT, fallback_pct: float = INITIAL_STOP_PCT,
) -> float:
    """`bars`'ın SON barının ait olduğu seansı bulur, o seansın İLK barının
    (açılış aralığı) ters ucuna (long için low, short için high) buffer_pct
    kadar tampon payı ekler. `bars` verilmemişse, o seansın barı hiç
    bulunamazsa, ya da hesaplanan seviye entry_price'ın yanlış tarafında
    kalırsa (stop, girişin ötesine/gerisine düşer - bu, ORB dışı bir
    algoritmanın sinyaliyle bu stop seçildiğinde, ör. fiyat zaten aralığın
    içindeyken bir pullback girişinde olabilir), fallback_pct ile
    breakeven_atr_structure_initial_stop'la AYNI sabit-% düşüşe güvenli
    şekilde geri düşülür - pozisyon hiçbir durumda stopsuz kalmaz."""
    naive_stop = entry_price * (1 - fallback_pct) if side == "long" else entry_price * (1 + fallback_pct)
    if not bars:
        return naive_stop

    session_date = bars[-1].t[:10]
    session_bars = [b for b in bars if b.t[:10] == session_date]
    if not session_bars:
        return naive_stop
    opening_bar = session_bars[0]

    if side == "long":
        structural_stop = opening_bar.l * (1 - buffer_pct)
        return structural_stop if structural_stop < entry_price else naive_stop
    structural_stop = opening_bar.h * (1 + buffer_pct)
    return structural_stop if structural_stop > entry_price else naive_stop


# ---- Dördüncü algoritma: "Heikin Ashi Çıkışı" - buy_algorithms.
# heikin_ashi_stoch_signal ile eşleşmek üzere tasarlandı. İlk stop, sinyal
# barının low'unun küçük bir tamponla altına kurulur (sinyal mumu alt
# fitilsiz olduğundan bu low, dönüşün başladığı seviyedir). Trail tarafında
# stratejinin çıkış kuralı (ilk kırmızı HA mumu ya da Stokastik %K > 80 iken
# %D'nin altına kesişim) tetiklendiğinde stop, o barın kapanışının hemen
# altına çekilir - bir sonraki barda fiyat geri çekildiği anda pozisyon
# kapanır. Doğrudan market satış yerine böyle modellendi çünkü hem canlı
# sistem (alpaca_trailing_stop.py) hem backtest (backtest_engine.py) çıkışı
# yalnızca resting stop üzerinden yönetiyor. Çıkış sinyali yokken
# breakeven_atr_structure_trail'in adayları da geçerli (hangisi daha sıkıysa).
#
# NOT: trail()'e verilen `bars` pozisyonun yönetim başlangıcından (≈ giriş)
# itibaren başlıyor. HA_Open özyinelemeli olduğu için ilk birkaç barda HA
# renkleri yaklaşık, Stokastik ise stoch_k_period + stoch_d_period - 1 bar
# dolana kadar hesaplanamaz (bu sürede sadece kırmızı HA çıkışı çalışır).

HEIKIN_ASHI_STOP_BUFFER_PCT = 0.002
HEIKIN_ASHI_EXIT_BUFFER_PCT = 0.001
# [2026-09-28] İlk stop için oynaklık tabanı: stop girişten en az bu kadar
# ATR uzakta olur. Sinyal mumu alt fitilsiz yeşil bir mum ve giriş onun
# kapanışında yapıldığından, "sinyal barının low'u" çoğu zaman girişin
# sentler altında kalıyordu (gözlemlenen gerçek örnek: LAUR, 2026-09-25 -
# giriş 37.58, stop 37.47, mesafe %0.29) - sıradan bir 30dk dalgalanması
# pozisyonu kapatıyordu. 0 = taban kapalı (eski davranış).
HEIKIN_ASHI_MIN_ATR_MULT = 1.0
# Kırmızı HA çıkışı için art arda gereken kırmızı mum sayısı - 1 = ilk
# kırmızı mumda çık (orijinal kural). Bkz. scripts/compare_heikin_ashi_stop.py.
HEIKIN_ASHI_EXIT_RED_CANDLES = 1


def heikin_ashi_initial_stop(
    entry_price: float, side: str, bars: list[Bar] | None = None,
    buffer_pct: float = HEIKIN_ASHI_STOP_BUFFER_PCT, fallback_pct: float = INITIAL_STOP_PCT,
    min_atr_mult: float = HEIKIN_ASHI_MIN_ATR_MULT, atr_period: int = ATR_PERIOD,
) -> float:
    """Sinyal barının (bars[-1]) low'unun buffer_pct altı (short için high'ın
    üstü). `bars` yoksa ya da seviye girişin yanlış tarafında kalırsa
    fallback_pct'lik sabit-% stopa düşülür.

    Oynaklık tabanı: bars'tan ATR(atr_period) hesaplanabiliyorsa stop,
    girişten en az min_atr_mult x ATR uzakta olacak şekilde genişletilir -
    yapısal seviye bundan daha uzaksa olduğu gibi kalır (taban sadece
    GENİŞLETİR, asla daraltmaz). ATR, sinyalin kendi mum periyodundaki
    barlardan hesaplanır (HA Gün İçi için 30dk)."""
    naive_stop = entry_price * (1 - fallback_pct) if side == "long" else entry_price * (1 + fallback_pct)
    if not bars:
        return naive_stop
    if side == "long":
        structural_stop = bars[-1].l * (1 - buffer_pct)
        stop = structural_stop if structural_stop < entry_price else naive_stop
    else:
        structural_stop = bars[-1].h * (1 + buffer_pct)
        stop = structural_stop if structural_stop > entry_price else naive_stop

    atr_value = atr(bars, atr_period) if min_atr_mult > 0 else None
    if atr_value is None:
        return stop
    if side == "long":
        return min(stop, entry_price - min_atr_mult * atr_value)
    return max(stop, entry_price + min_atr_mult * atr_value)


def _closed_bars(bars: list[Bar]) -> list[Bar]:
    """Canlı trailing stop botu (alpaca_trailing_stop.manage_position) henüz
    oluşmakta olan son barı da geçiriyor - HA çıkış kuralı bar KAPANIŞINDA
    tanımlı olduğundan o bar atılır. Periyot, ardışık barlar arasındaki en
    kısa aralıktan çıkarılır; backtest'teki geçmiş barlar hiçbir zaman atılmaz."""
    if len(bars) < 3:
        return bars
    stamps = [datetime.fromisoformat(b.t.replace("Z", "+00:00")) for b in bars[-6:]]
    duration = min(b - a for a, b in zip(stamps, stamps[1:]))
    if stamps[-1] + duration > datetime.now(timezone.utc):
        return bars[:-1]
    return bars


def heikin_ashi_trail(
    ctx: StopContext,
    exit_buffer_pct: float = HEIKIN_ASHI_EXIT_BUFFER_PCT,
    stoch_k_period: int = 14,
    stoch_d_period: int = 3,
    stoch_overbought: float = 80.0,
    exit_red_candles: int = HEIKIN_ASHI_EXIT_RED_CANDLES,
    initial_stop_pct: float = INITIAL_STOP_PCT,
    atr_period: int = ATR_PERIOD,
    atr_multiplier: float = ATR_MULTIPLIER,
    breakeven_trigger_pct: float = BREAKEVEN_TRIGGER_PCT,
    stale_reference_days: float = STALE_REFERENCE_DAYS,
    trend_ema_period: int = TREND_EMA_PERIOD,
    swing_order: int = SWING_ORDER,
    fallback_buffer_pct: float = FALLBACK_BUFFER_PCT,
    breakeven_buffer_pct: float = BREAKEVEN_BUFFER_PCT,
) -> StopDecision | None:
    """HA/Stokastik çıkış sinyali varsa stopu son kapanışın exit_buffer_pct
    altına çeker; yoksa (ya da o aday daha gevşekse) breakeven_atr_structure_
    trail'in kararını kullanır. Strateji sadece long tanımlı olduğundan
    short pozisyonlarda yalnızca yapısal trail çalışır."""
    base = breakeven_atr_structure_trail(
        ctx, initial_stop_pct, atr_period, atr_multiplier, breakeven_trigger_pct,
        stale_reference_days, trend_ema_period, swing_order, fallback_buffer_pct, breakeven_buffer_pct,
    )
    if ctx.side != "long" or not ctx.bars:
        return base
    closed = _closed_bars(ctx.bars)
    reason = heikin_ashi_long_exit_reason(closed, stoch_k_period, stoch_d_period, stoch_overbought, exit_red_candles)
    if reason is None:
        return base
    # Güncel fiyat (oluşmakta olan bar) kapanışın da altına indiyse stop onun
    # altına kurulur - piyasa fiyatının üstünde bir satış stop'u reddedilir.
    exit_price = round(min(closed[-1].c, ctx.bars[-1].c) * (1 - exit_buffer_pct), 2)
    best_so_far = base.price if base is not None else ctx.current_stop_price
    if exit_price <= best_so_far:
        return base
    return StopDecision(price=exit_price, reason=f"HA çıkış sinyali - {reason}")


# ---- Beşinci algoritma: "Oynaklık (ATR) Stop" - [2026-09-28 · Öneri 1-2].
#
# Neden: sabit %1.5 ilk stop, portföydeki hisselerin GÜNLÜK ATR'sinin sadece
# 0.3-0.7'si kadardı (MSFT %2.0, NOW %4.9, SKHY %5.0, UROY %5.2 günlük ATR) -
# yani stop hissenin sıradan günlük gürültüsünün içindeydi. Aynı işlemlerde
# 2xATR stop MSFT/NOW/SKHY/UROY'da hiç tetiklenmeyecekti (MSFT +%4.3'te).
#
# Nasıl:
#   - İlk stop = giriş - initial_atr_mult x ATR(atr_period). ATR, GİRİŞ
#     SİNYALİNİN ZAMAN DİLİMİNDEKİ barlardan hesaplanır (alpaca_buy_points
#     initial_stop'a sembolün kendi periyodundaki barları geçirir; canlı trail
#     de alpaca_trailing_stop.resolve_stop_timeframe ile aynı periyodu kullanır).
#     Stop max_stop_pct'den daha uzağa düşmez (çok oynak hissede tavan).
#   - 1R = giriş - ilk stop. Breakeven, bir bar KAPANIŞI giriş + breakeven_r x R'yi
#     geçince giriş + breakeven_buffer_atr x ATR'ye çekilir (tam girişe değil).
#   - En yüksek fiyat giriş + trail_start_r x R'ye ulaştıktan sonra chandelier
#     trail: (girişten beri en yüksek fiyat) - trail_atr_mult x ATR.
#   - Stop hiçbir zaman gevşemez ve güncel fiyatın üstüne çıkmaz.
# Pozisyon büyüklüğü bu stopa göre risk_sizing.py'de hesaplanır (Öneri 5) -
# geniş stop ancak küçülen pozisyonla güvenli.
#
# [2026-09-28 · TEM/INTC stop incelemesi] İlk stop ATR çarpanı varsayılanı
# 2.0 -> 1.5. Premium Buy Point ve Relative Strength Rotasyonu bu algoritmaya
# geçirildi: sabit %1.5 ilk stop, INTC'nin saatlik ATR'sinden (≈%1.9) bile
# kısaydı. Çarpan Stop Loss Ayarları'ndan değiştirilebilir.
#
# Önceki DURUM: Seçenek olarak mevcut, hiçbir hissenin VARSAYILANI DEĞİL. 2026-09-28
# doğrulama backtestinde (scripts/compare_stop_algorithms.py, 11 hisse, günlük)
# breakeven_atr_structure'ın günlük barlarda izlenen hali +237R, bu algoritma
# -0.7R (8xATR trail ile +8.7R) verdi - chandelier büyük trendlerden erken
# çıkıyor. Ayrıntı: Algo Analiz > Değişiklik Günlüğü > Test sonuçları.

ATR_VOL_INITIAL_ATR_MULT = 1.5
ATR_VOL_MAX_STOP_PCT = 0.12
ATR_VOL_FALLBACK_PCT = 0.03
ATR_VOL_BREAKEVEN_R = 1.0
ATR_VOL_BREAKEVEN_BUFFER_ATR = 0.1
ATR_VOL_TRAIL_START_R = 2.0
ATR_VOL_TRAIL_ATR_MULT = 3.0
# [2026-10-06 · MDB incelemesi]
#   atr_outlier_mult: GÜNLÜK (ve daha uzun) barlarda, ATR penceresinde gerçek
#     aralığı pencerenin medyan gerçek aralığının bu katını aşan gün AYKIRI
#     sayılır ve ortalamaya hiç katılmaz. Kazanç açıklaması gibi tek seferlik
#     bir boşluk (MDB 28.09: 110$ aralık, medyan ~24$) ATR'yi - ve ondan
#     türeyen 1R'yi ve chandelier mesafesini - iki hafta şişirmesin. 30dk gibi
#     gün içi barlarda UYGULANMAZ: orada her sabahki gece boşluğu normal ve
#     backtestte ayıklamak sonucu belirgin kötüleştirdi. 0 = kapalı.
#   trail_tighten_per_r: chandelier çarpanı, trail_start_r'nin ötesindeki her
#     R kâr için bu kadar daralır; trail_min_atr_mult'ın altına inmez.
#     Varsayılan KAPALI (backtestte dönemler arası tutarsız).
# Karşılaştırma: scripts/compare_atr_trail_variants.py
ATR_VOL_OUTLIER_MULT = 3.0
ATR_VOL_TRAIL_TIGHTEN_PER_R = 0.0
ATR_VOL_TRAIL_MIN_ATR_MULT = 2.0
_DAILY_BAR_MIN_SPACING_S = 20 * 3600


def _is_daily_or_longer(bars: list[Bar]) -> bool:
    """Ardışık bar zaman damgaları arasındaki medyan aralık >= 20 saat mi."""
    stamps = []
    for b in bars[-6:]:
        try:
            stamps.append(datetime.fromisoformat(b.t.replace("Z", "+00:00")).timestamp())
        except (TypeError, ValueError):
            return False
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    return bool(gaps) and statistics.median(gaps) >= _DAILY_BAR_MIN_SPACING_S


def robust_atr(bars: list[Bar] | None, period: int, outlier_mult: float = 0.0) -> float | None:
    """atr() ile aynı pencere; outlier_mult > 0 ve barlar günlükse, gerçek
    aralığı medyanın outlier_mult katını aşan günler ortalamadan çıkarılır
    (bkz. ATR_VOL_OUTLIER_MULT)."""
    if not bars:
        return None
    if outlier_mult <= 0 or not _is_daily_or_longer(bars):
        return atr(bars, period)
    if len(bars) < period + 1:
        return None
    window = bars[-(period + 1):]
    true_ranges = [max(cur.h - cur.l, abs(cur.h - prev.c), abs(cur.l - prev.c)) for prev, cur in zip(window, window[1:])]
    limit = outlier_mult * statistics.median(true_ranges)
    kept = [tr for tr in true_ranges if tr <= limit]
    return sum(kept) / len(kept)


def _atr_from_context(bars: list[Bar] | None, history_bars: list[Bar] | None, atr_period: int,
                      outlier_mult: float = 0.0) -> float | None:
    """history_bars (giriş öncesini de içeren) varsa ATR ondan, yoksa bars'tan."""
    for source in (history_bars, bars):
        if source:
            value = robust_atr(source, atr_period, outlier_mult)
            if value is not None:
                return value
    return None


def atr_volatility_initial_stop(
    entry_price: float, side: str, bars: list[Bar] | None = None,
    initial_atr_mult: float = ATR_VOL_INITIAL_ATR_MULT, atr_period: int = ATR_PERIOD,
    max_stop_pct: float = ATR_VOL_MAX_STOP_PCT, fallback_pct: float = ATR_VOL_FALLBACK_PCT,
    atr_outlier_mult: float = ATR_VOL_OUTLIER_MULT,
) -> float:
    """giriş - initial_atr_mult x ATR (short için +). ATR hesaplanamazsa
    (bars yok / yetersiz) fallback_pct; mesafe max_stop_pct ile sınırlı."""
    atr_value = robust_atr(bars, atr_period, atr_outlier_mult)
    distance = initial_atr_mult * atr_value if atr_value else entry_price * fallback_pct
    distance = min(distance, entry_price * max_stop_pct)
    return entry_price - distance if side == "long" else entry_price + distance


def atr_volatility_trail(
    ctx: StopContext,
    initial_atr_mult: float = ATR_VOL_INITIAL_ATR_MULT,
    atr_period: int = ATR_PERIOD,
    max_stop_pct: float = ATR_VOL_MAX_STOP_PCT,
    fallback_pct: float = ATR_VOL_FALLBACK_PCT,
    breakeven_r: float = ATR_VOL_BREAKEVEN_R,
    breakeven_buffer_atr: float = ATR_VOL_BREAKEVEN_BUFFER_ATR,
    trail_start_r: float = ATR_VOL_TRAIL_START_R,
    trail_atr_mult: float = ATR_VOL_TRAIL_ATR_MULT,
    atr_outlier_mult: float = ATR_VOL_OUTLIER_MULT,
    trail_tighten_per_r: float = ATR_VOL_TRAIL_TIGHTEN_PER_R,
    trail_min_atr_mult: float = ATR_VOL_TRAIL_MIN_ATR_MULT,
) -> StopDecision | None:
    """R bazlı breakeven + chandelier trail - bkz. bölüm notu. Breakeven
    kararı KAPANMIŞ bara göre verilir (oluşmakta olan barın anlık iğnesi
    tetiklemez); stopun güncel fiyatın doğru tarafında kalması ise son
    (oluşan) barın fiyatına göre kontrol edilir."""
    if not ctx.bars:
        return None
    side = ctx.side
    last_price = ctx.bars[-1].c
    atr_value = _atr_from_context(ctx.bars, ctx.history_bars, atr_period, atr_outlier_mult)

    if ctx.initial_stop_price is not None and ctx.initial_stop_price != ctx.entry_price:
        one_r = abs(ctx.entry_price - ctx.initial_stop_price)
    elif atr_value is not None:
        one_r = min(initial_atr_mult * atr_value, ctx.entry_price * max_stop_pct)
    else:
        one_r = ctx.entry_price * fallback_pct
    if one_r <= 0:
        return None

    closed = _closed_bars(ctx.bars)
    last_close = closed[-1].c if closed else last_price
    sign = 1 if side == "long" else -1
    candidates: list[tuple[float, str]] = []

    if sign * (last_close - ctx.entry_price) >= breakeven_r * one_r:
        buffer_amount = breakeven_buffer_atr * atr_value if atr_value is not None else 0.0
        candidates.append((ctx.entry_price + sign * buffer_amount, f"breakeven (+{breakeven_r:g}R)"))

    if side == "long":
        extreme = max(b.h for b in ctx.bars)
        reached = extreme - ctx.entry_price >= trail_start_r * one_r
    else:
        extreme = min(b.l for b in ctx.bars)
        reached = ctx.entry_price - extreme >= trail_start_r * one_r
    if reached and atr_value is not None:
        k = trail_atr_mult
        if trail_tighten_per_r > 0:
            mfe_r = sign * (extreme - ctx.entry_price) / one_r
            k = max(trail_min_atr_mult, trail_atr_mult - trail_tighten_per_r * (mfe_r - trail_start_r))
        candidates.append((extreme - sign * k * atr_value, f"chandelier ({round(k, 2):g}xATR)"))

    valid = [c for c in candidates if (c[0] < last_price if side == "long" else c[0] > last_price)]
    if not valid:
        return None
    if side == "long":
        best_price, reason = max(valid, key=lambda c: c[0])
        improves = best_price > ctx.current_stop_price
    else:
        best_price, reason = min(valid, key=lambda c: c[0])
        improves = best_price < ctx.current_stop_price
    if not improves:
        return None
    return StopDecision(price=best_price, reason=reason)


# ---- Altıncı algoritma: "Akıllı Dinamik Stop" (adaptive_dynamic).
#
# Neden: Sabit-% stop (breakeven_atr_structure) hissenin oynaklığını
# görmüyor; atr_volatility oynaklığı görüyor ama yapıyı (destek) görmüyor ve
# chandelier çarpanı işlem boyunca sabit - büyük trendlerden erken çıkıyor.
# Bu algoritma üç bilgiyi birleştirir:
#   1. İlk stop YAPIYA göre: girişin altındaki en yakın onaylı swing low'un
#      structure_buffer_atr x ATR altı - ama mesafe [min_atr_mult, max_atr_mult]
#      x ATR bandına sıkıştırılır (destek çok yakınsa gürültüde patlamasın,
#      çok uzaksa risk şişmesin). Swing low yoksa initial_atr_mult x ATR.
#   2. Trail çarpanı KÂRA göre daralır: en yüksek fiyat trail_start_r'ye
#      ulaşınca chandelier (en yüksek - k x ATR) başlar; k, trail_atr_mult'tan
#      başlayıp trail_start_r'nin ötesindeki her R için tighten_per_r kadar
#      azalır, min_trail_atr_mult'ın altına inmez - kâr büyüdükçe daha çok
#      kilitlenir, başlangıçta trend nefes alır.
#   3. Trail çarpanı TRENDİN KALİTESİNE göre ayarlanır: Kaufman verimlilik
#      oranı (ER, 0 = yatay/gürültülü, 1 = dümdüz trend) er_period bar
#      üzerinden; k x (1 + er_weight x (ER - 0.5)). Temiz trendde stop geniş
#      kalır, testereye dönmüş fiyatta kâr daha erken korunur.
# Ek olarak (varsayılan KAPALI): R bazlı breakeven (atr_volatility ile aynı,
# kapanmış bara göre) ve use_structure > 0 ise breakeven_atr_structure'ın
# yapısal trail adayı.
#
# Varsayılanlar (5xATR trail, 1.5R'den başlar, R başına 0.25 daralır, ilk stop
# en fazla 2xATR, breakeven ve ER kapalı) scripts/backtest_adaptive_stop.py'nin
# walk-forward taramasında EĞİTİM yarısında seçilip TEST yarısında doğrulandı.
# Adaylardan en sıkı olanı seçilir; stop asla gevşemez (çağıran taraf).
# Doğrulama: scripts/backtest_adaptive_stop.py (walk-forward + eşleştirilmiş
# bootstrap) - sonuçlar Değişiklik Günlüğü'nde.

ADAPTIVE_INITIAL_ATR_MULT = 2.0
ADAPTIVE_MIN_ATR_MULT = 1.0
ADAPTIVE_MAX_ATR_MULT = 2.0
ADAPTIVE_STRUCTURE_BUFFER_ATR = 0.25
ADAPTIVE_STRUCTURE_LOOKBACK = 20
# 0 = kapalı. Walk-forward taramasında 1R/2R breakeven, test döneminde
# özsermaye katkısını düşürdü (2026-09-28 analizindeki erken breakeven bulgusuyla aynı).
ADAPTIVE_BREAKEVEN_R = 0.0
ADAPTIVE_BREAKEVEN_BUFFER_ATR = 0.1
ADAPTIVE_TRAIL_START_R = 1.5
ADAPTIVE_TRAIL_ATR_MULT = 5.0
ADAPTIVE_MIN_TRAIL_ATR_MULT = 2.0
ADAPTIVE_TIGHTEN_PER_R = 0.25
ADAPTIVE_ER_PERIOD = 20
# Taramada katkısı ölçülemedi (0 ile 0.5 arası fark gürültü düzeyinde) - kapalı.
ADAPTIVE_ER_WEIGHT = 0.0
ADAPTIVE_USE_STRUCTURE = 0.0


def efficiency_ratio(closes: list[float], period: int) -> float | None:
    """Kaufman verimlilik oranı: |net değişim| / toplam mutlak değişim, son
    `period` bar üzerinden. 1 = tek yönlü hareket, 0'a yakın = gürültü."""
    if period <= 0 or len(closes) < period + 1:
        return None
    window = closes[-(period + 1):]
    path = sum(abs(b - a) for a, b in zip(window, window[1:]))
    if path <= 0:
        return 0.0
    return abs(window[-1] - window[0]) / path


def _nearest_swing(bars: list[Bar], side: str, entry_price: float, swing_order: int, lookback: int):
    """Son `lookback` barda, girişin doğru tarafındaki (long: altındaki) en
    güncel onaylı swing low (short: high)."""
    pivots = find_pivots(bars[-lookback:], swing_order) if lookback > 0 else []
    kind = "low" if side == "long" else "high"
    for pivot in reversed(pivots):
        if pivot.kind != kind:
            continue
        if (side == "long" and pivot.price < entry_price) or (side == "short" and pivot.price > entry_price):
            return pivot
    return None


def adaptive_dynamic_initial_stop(
    entry_price: float, side: str, bars: list[Bar] | None = None,
    initial_atr_mult: float = ADAPTIVE_INITIAL_ATR_MULT, min_atr_mult: float = ADAPTIVE_MIN_ATR_MULT,
    max_atr_mult: float = ADAPTIVE_MAX_ATR_MULT, structure_buffer_atr: float = ADAPTIVE_STRUCTURE_BUFFER_ATR,
    structure_lookback: int = ADAPTIVE_STRUCTURE_LOOKBACK, swing_order: int = SWING_ORDER,
    atr_period: int = ATR_PERIOD, max_stop_pct: float = ATR_VOL_MAX_STOP_PCT,
    fallback_pct: float = ATR_VOL_FALLBACK_PCT,
) -> float:
    """Yapısal (swing low) ilk stop, ATR bandına sıkıştırılmış - bkz. bölüm
    notu. ATR hesaplanamazsa fallback_pct; mesafe max_stop_pct ile sınırlı."""
    atr_value = atr(bars, atr_period) if bars else None
    if not atr_value:
        distance = entry_price * fallback_pct
    else:
        pivot = _nearest_swing(bars, side, entry_price, swing_order, structure_lookback)
        if pivot is None:
            distance = initial_atr_mult * atr_value
        else:
            distance = abs(entry_price - pivot.price) + structure_buffer_atr * atr_value
            distance = min(max(distance, min_atr_mult * atr_value), max_atr_mult * atr_value)
    distance = min(distance, entry_price * max_stop_pct)
    return entry_price - distance if side == "long" else entry_price + distance


def adaptive_dynamic_trail(
    ctx: StopContext,
    initial_atr_mult: float = ADAPTIVE_INITIAL_ATR_MULT,
    atr_period: int = ATR_PERIOD,
    max_stop_pct: float = ATR_VOL_MAX_STOP_PCT,
    fallback_pct: float = ATR_VOL_FALLBACK_PCT,
    breakeven_r: float = ADAPTIVE_BREAKEVEN_R,
    breakeven_buffer_atr: float = ADAPTIVE_BREAKEVEN_BUFFER_ATR,
    trail_start_r: float = ADAPTIVE_TRAIL_START_R,
    trail_atr_mult: float = ADAPTIVE_TRAIL_ATR_MULT,
    min_trail_atr_mult: float = ADAPTIVE_MIN_TRAIL_ATR_MULT,
    tighten_per_r: float = ADAPTIVE_TIGHTEN_PER_R,
    er_period: int = ADAPTIVE_ER_PERIOD,
    er_weight: float = ADAPTIVE_ER_WEIGHT,
    use_structure: float = ADAPTIVE_USE_STRUCTURE,
    atr_multiplier: float = ATR_MULTIPLIER,
    stale_reference_days: float = STALE_REFERENCE_DAYS,
    swing_order: int = SWING_ORDER,
    fallback_buffer_pct: float = FALLBACK_BUFFER_PCT,
) -> StopDecision | None:
    """R bazlı breakeven + kâra ve trend kalitesine göre daralan chandelier
    (+ opsiyonel yapısal trail) - bkz. bölüm notu."""
    if not ctx.bars:
        return None
    side = ctx.side
    sign = 1 if side == "long" else -1
    last_price = ctx.bars[-1].c
    atr_value = _atr_from_context(ctx.bars, ctx.history_bars, atr_period)

    if ctx.initial_stop_price is not None and ctx.initial_stop_price != ctx.entry_price:
        one_r = abs(ctx.entry_price - ctx.initial_stop_price)
    elif atr_value is not None:
        one_r = min(initial_atr_mult * atr_value, ctx.entry_price * max_stop_pct)
    else:
        one_r = ctx.entry_price * fallback_pct
    if one_r <= 0:
        return None

    closed = _closed_bars(ctx.bars)
    last_close = closed[-1].c if closed else last_price
    candidates: list[tuple[float, str]] = []

    if breakeven_r > 0 and sign * (last_close - ctx.entry_price) >= breakeven_r * one_r:
        buffer_amount = breakeven_buffer_atr * atr_value if atr_value is not None else 0.0
        candidates.append((ctx.entry_price + sign * buffer_amount, f"breakeven (+{breakeven_r:g}R)"))

    extreme = max(b.h for b in ctx.bars) if side == "long" else min(b.l for b in ctx.bars)
    mfe_r = sign * (extreme - ctx.entry_price) / one_r
    if mfe_r >= trail_start_r and atr_value is not None:
        k = trail_atr_mult - tighten_per_r * (mfe_r - trail_start_r)
        source = ctx.history_bars or ctx.bars
        er = efficiency_ratio([b.c for b in source], er_period)
        if er is not None:
            k *= 1 + er_weight * (er - 0.5)
        k = max(k, min_trail_atr_mult)
        candidates.append((extreme - sign * k * atr_value, f"adaptif chandelier ({k:.2f}xATR, {mfe_r:.1f}R)"))

    if use_structure > 0:
        structure_candidate = _structure_trail_candidate(
            ctx, atr_period, atr_multiplier, stale_reference_days, 0, swing_order, fallback_buffer_pct,
        )
        if structure_candidate is not None:
            candidates.append(structure_candidate)

    valid = [c for c in candidates if (c[0] < last_price if side == "long" else c[0] > last_price)]
    if not valid:
        return None
    if side == "long":
        best_price, reason = max(valid, key=lambda c: c[0])
        improves = best_price > ctx.current_stop_price
    else:
        best_price, reason = min(valid, key=lambda c: c[0])
        improves = best_price < ctx.current_stop_price
    if not improves:
        return None
    return StopDecision(price=best_price, reason=reason)


STOP_ALGORITHMS: dict[str, StopAlgorithm] = {
    "breakeven_atr_structure": StopAlgorithm(
        label="Breakeven + Yapısal Trail (ATR tamponlu)",
        initial_stop=breakeven_atr_structure_initial_stop,
        trail=breakeven_atr_structure_trail,
    ),
    "wait_then_trail": StopAlgorithm(
        label="Beklemeli ve İz Süren Stop",
        initial_stop=wait_then_trail_initial_stop,
        trail=wait_then_trail_trail,
    ),
    "opening_range": StopAlgorithm(
        label="Açılış Aralığı (ORB) Stop",
        initial_stop=opening_range_initial_stop,
        trail=breakeven_atr_structure_trail,
    ),
    "heikin_ashi_exit": StopAlgorithm(
        label="Heikin Ashi Çıkışı",
        initial_stop=heikin_ashi_initial_stop,
        trail=heikin_ashi_trail,
    ),
    "atr_volatility": StopAlgorithm(
        label="Oynaklık (ATR) Stop + R Bazlı Breakeven",
        initial_stop=atr_volatility_initial_stop,
        trail=atr_volatility_trail,
    ),
    "adaptive_dynamic": StopAlgorithm(
        label="Akıllı Dinamik Stop (Yapı + Adaptif ATR)",
        initial_stop=adaptive_dynamic_initial_stop,
        trail=adaptive_dynamic_trail,
    ),
}
DEFAULT_STOP_ALGORITHM = "breakeven_atr_structure"
