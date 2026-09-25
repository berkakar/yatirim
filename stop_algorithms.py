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

Dört algoritma var:
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
"""

import inspect
from dataclasses import dataclass
from typing import Callable

from heikin_ashi import long_exit_reason as heikin_ashi_long_exit_reason
from indicators import atr, ema
from structure import Bar, validated_trailing_level


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
_INT_PARAM_NAMES = frozenset({"atr_period", "trend_ema_period", "swing_order", "stoch_k_period", "stoch_d_period"})


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
BREAKEVEN_TRIGGER_PCT = 0.01
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

    gain_pct = ((last_price - ctx.entry_price) / ctx.entry_price if side == "long"
                else (ctx.entry_price - last_price) / ctx.entry_price)
    if gain_pct >= breakeven_trigger_pct:
        if side == "long" and ctx.entry_price > ctx.current_stop_price and ctx.entry_price < last_price:
            candidates.append((ctx.entry_price, "breakeven"))
        elif side == "short" and ctx.entry_price < ctx.current_stop_price and ctx.entry_price > last_price:
            candidates.append((ctx.entry_price, "breakeven"))

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

    gain_pct = ((last_price - ctx.entry_price) / ctx.entry_price if side == "long"
                else (ctx.entry_price - last_price) / ctx.entry_price)
    if gain_pct >= breakeven_trigger_pct:
        if side == "long" and ctx.entry_price > ctx.current_stop_price and ctx.entry_price < last_price:
            candidates.append((ctx.entry_price, "breakeven"))
        elif side == "short" and ctx.entry_price < ctx.current_stop_price and ctx.entry_price > last_price:
            candidates.append((ctx.entry_price, "breakeven"))

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


def heikin_ashi_initial_stop(
    entry_price: float, side: str, bars: list[Bar] | None = None,
    buffer_pct: float = HEIKIN_ASHI_STOP_BUFFER_PCT, fallback_pct: float = INITIAL_STOP_PCT,
) -> float:
    """Sinyal barının (bars[-1]) low'unun buffer_pct altı (short için high'ın
    üstü). `bars` yoksa ya da seviye girişin yanlış tarafında kalırsa
    fallback_pct'lik sabit-% stopa düşülür."""
    naive_stop = entry_price * (1 - fallback_pct) if side == "long" else entry_price * (1 + fallback_pct)
    if not bars:
        return naive_stop
    if side == "long":
        structural_stop = bars[-1].l * (1 - buffer_pct)
        return structural_stop if structural_stop < entry_price else naive_stop
    structural_stop = bars[-1].h * (1 + buffer_pct)
    return structural_stop if structural_stop > entry_price else naive_stop


def heikin_ashi_trail(
    ctx: StopContext,
    exit_buffer_pct: float = HEIKIN_ASHI_EXIT_BUFFER_PCT,
    stoch_k_period: int = 14,
    stoch_d_period: int = 3,
    stoch_overbought: float = 80.0,
    initial_stop_pct: float = INITIAL_STOP_PCT,
    atr_period: int = ATR_PERIOD,
    atr_multiplier: float = ATR_MULTIPLIER,
    breakeven_trigger_pct: float = BREAKEVEN_TRIGGER_PCT,
    stale_reference_days: float = STALE_REFERENCE_DAYS,
    trend_ema_period: int = TREND_EMA_PERIOD,
    swing_order: int = SWING_ORDER,
    fallback_buffer_pct: float = FALLBACK_BUFFER_PCT,
) -> StopDecision | None:
    """HA/Stokastik çıkış sinyali varsa stopu son kapanışın exit_buffer_pct
    altına çeker; yoksa (ya da o aday daha gevşekse) breakeven_atr_structure_
    trail'in kararını kullanır. Strateji sadece long tanımlı olduğundan
    short pozisyonlarda yalnızca yapısal trail çalışır."""
    base = breakeven_atr_structure_trail(
        ctx, initial_stop_pct, atr_period, atr_multiplier, breakeven_trigger_pct,
        stale_reference_days, trend_ema_period, swing_order, fallback_buffer_pct,
    )
    if ctx.side != "long" or not ctx.bars:
        return base
    reason = heikin_ashi_long_exit_reason(ctx.bars, stoch_k_period, stoch_d_period, stoch_overbought)
    if reason is None:
        return base
    exit_price = round(ctx.bars[-1].c * (1 - exit_buffer_pct), 2)
    best_so_far = base.price if base is not None else ctx.current_stop_price
    if exit_price <= best_so_far:
        return base
    return StopDecision(price=exit_price, reason=f"HA çıkış sinyali - {reason}")


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
}
DEFAULT_STOP_ALGORITHM = "breakeven_atr_structure"
