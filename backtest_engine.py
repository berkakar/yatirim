"""Walk-forward backtest engine for the premium buy-point algorithms
(buy_algorithms.py) paired with a pluggable stop-loss algorithm
(stop_algorithms.py, same registry the live system uses in
alpaca_trailing_stop.py), replayed against historical bars instead of live
Alpaca orders.

Reuses the exact same decision functions the live system uses
(buy_algorithms.ALGORITHMS, reject_if_marketable, stop_algorithms.
STOP_ALGORITHMS) and the same stop_settings dict (Stop Loss Ayarları -
stop_loss_settings.py, resolved into actual kwargs via stop_algorithms.
resolve_kwargs), so a backtest result reflects what the live bot would
actually have done with the user's currently saved parameters, not a
separate approximation of it.

No look-ahead: at simulated bar i, only bars[:i+1] are visible, and the
daily closes used for the higher-timeframe trend filter / trend_pullback's
SMA200 gate are trimmed to strictly before that bar's calendar date (or
up to and including it, for the "1Day" timeframe itself, since a daily
bar's own close is exactly the information available at its close).

Order simulation mirrors the live system:
  - signal.style == "breakout" (breakout_volume, orb): fills IMMEDIATELY at
    the signal bar's own close (signal.price) - no resting order - mirroring
    alpaca_buy_points.check_symbol()'s market-order branch for these styles.
    A resting limit here would misrepresent them: it only fills on a LATER
    bar whose low retraces back down to the breakout price, i.e. only on a
    retest that arguably invalidates the breakout thesis, understating (or
    badly mistiming) how often/where a live market order would actually fill.
  - Other (pullback) signals: no position, no resting order, a valid signal
    appears -> a resting limit buy is "placed" (not filled the same bar).
  - No position, a resting order exists -> filled if the bar's low
    touches its price; otherwise re-priced or canceled the same way
    alpaca_buy_points.check_symbol() would (signal moved / went invalid).
  - Position open -> stopped out if the bar's low touches the stop
    (filled at the stop price, or the bar's open if it gapped through);
    otherwise the selected stop algorithm's trail() is evaluated, same as
    alpaca_trailing_stop.manage_position() does live, to (only) tighten the
    stop for subsequent bars.
  - A position still open when the fetched window ends is closed at the
    last bar's close ("test_end_close") so P&L is always well-defined.
"""
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from alpaca_trailing_stop import LOOKBACK_DAYS as TRAIL_LOOKBACK_DAYS
from buy_algorithms import ALGORITHMS, reject_if_marketable
from stop_algorithms import DEFAULT_STOP_ALGORITHM, STOP_ALGORITHMS, StopContext, resolve_kwargs
from structure import Bar

SIGNAL_LOOKBACK_DAYS = 60  # matches alpaca_buy_points.py's BUY_LOOKBACK_DAYS default


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _window(bars: list[Bar], end_idx: int, days: float, floor_idx: int = 0) -> list[Bar]:
    """bars[floor_idx:end_idx+1] trimmed to the last `days` calendar days
    ending at bars[end_idx] - mirrors get_management_start / the
    BUY_LOOKBACK_DAYS windowing the live scripts use, so the backtest only
    ever sees what production would have fetched."""
    cutoff = _parse(bars[end_idx].t) - timedelta(days=days)
    lo = floor_idx
    while lo < end_idx and _parse(bars[lo].t) < cutoff:
        lo += 1
    return bars[lo:end_idx + 1]


def _daily_closes_upto(daily_pairs: list[tuple], bar_date, inclusive: bool) -> list[float]:
    if inclusive:
        return [c for d, c in daily_pairs if d <= bar_date]
    return [c for d, c in daily_pairs if d < bar_date]


def _last_index_at_or_before(bars: list[Bar], ts: datetime) -> int:
    """bars (artan zaman sırasında) içinde zaman damgası ts'ye eşit ya da
    ondan önceki SON bar'ın index'i - hiçbiri yoksa (tüm barlar ts'den sonra) 0."""
    idx = 0
    for i, b in enumerate(bars):
        if _parse(b.t) <= ts:
            idx = i
        else:
            break
    return idx


def _first_index_after(bars: list[Bar], ts: datetime, start: int = 0) -> int:
    """bars içinde zaman damgası ts'den KESİN SONRA olan ilk bar'ın index'i
    (start'tan itibaren taranır) - bir pozisyon stop_bars üzerinde kapandıktan
    sonra, alım tarafının (coarse) döngüsü hangi bar'dan devam edecek onu
    bulur, böylece pozisyon açıkken geçen zaman aralığı tekrar bir alım
    sinyaline bakılmaz (look-ahead'e yol açmaz)."""
    idx = start
    while idx < len(bars) and _parse(bars[idx].t) <= ts:
        idx += 1
    return idx


def _manage_position(
    position: dict, stop_bars: list[Bar], entry_ts: datetime, daily_pairs: list[tuple], is_daily_tf_stop: bool,
    stop_algo, algo_settings: dict, shared_settings: dict, max_loss_pct: float | None,
    result: "BacktestResult", cash: float, starting_budget: float,
) -> tuple[float, dict | None, datetime]:
    """Bir pozisyon açıldığı andan (entry_ts) itibaren, alım sinyalinin
    üretildiği (coarse) mum periyodundan BAĞIMSIZ olarak stop_bars üzerinde -
    genelde daha küçük bir periyotta - ilerler: stop tetiklenene ya da
    stop_bars tükenene kadar. run_backtest'in tek-periyotlu (stop_bars is
    bars) haldeki eski inline mantığıyla birebir aynı davranışı üretir -
    farkı sadece "hangi bar listesi üzerinde ilerlediği" ve pozisyonun
    "entry_idx"ının o listedeki (coarse index yerine) karşılığı olması.

    Döner: (güncel cash, pozisyon (stop ile kapandıysa None, yoksa hâlâ açık
    hâliyle position dict'i), son işlenen stop_bars zaman damgası - çağıran
    coarse döngü, YENİ sinyal aramasını bu zamandan KESİN SONRAKİ ilk coarse
    mumdan devam ettirir)."""
    entry_idx = _last_index_at_or_before(stop_bars, entry_ts)
    position = {**position, "entry_idx": entry_idx}
    last_ts = entry_ts

    # Kontrol, entry_idx+1'den değil entry_ts'den KESİN SONRAKİ ilk bar'dan
    # başlar: stop_bars, entry_ts'den önce/eşit hiçbir bar içermiyorsa (ör.
    # daha küçük periyotlu veri entry anından sonra başlıyorsa) entry_idx
    # geriye düşerek (0'a) ilk bar'ı atlamasın diye ikisi ayrı hesaplanır -
    # tek periyotlu modda (stop_bars is bars) ikisi zaten aynı index'e denk gelir.
    check_start_idx = _first_index_after(stop_bars, entry_ts)
    for j in range(check_start_idx, len(stop_bars)):
        bar = stop_bars[j]
        last_ts = _parse(bar.t)

        if bar.l <= position["stop_price"]:
            fill = bar.o if bar.o < position["stop_price"] else position["stop_price"]
            cash += position["qty"] * fill
            result.trades.append(
                Trade("sell", bar.t, round(fill, 4), position["qty"], f"stop - {position['stop_reason']}")
            )
            if max_loss_pct and not result.stop_loss_triggered and starting_budget:
                loss_pct = (starting_budget - cash) / starting_budget * 100
                if loss_pct >= max_loss_pct:
                    result.stop_loss_triggered = True
                    result.stop_loss_triggered_at = bar.t
                    result.trades[-1].reason += f" · zarar kes tetiklendi (toplam zarar %{loss_pct:.2f})"
            return cash, None, last_ts

        bar_date = last_ts.date()
        struct_bars = _window(stop_bars, j, TRAIL_LOOKBACK_DAYS, floor_idx=entry_idx)
        daily_closes = _daily_closes_upto(daily_pairs, bar_date, inclusive=is_daily_tf_stop)
        ctx = StopContext(
            side="long", entry_price=position["entry_price"], current_stop_price=position["stop_price"],
            bars=struct_bars, daily_closes=daily_closes,
        )
        decision = stop_algo.trail(ctx, **resolve_kwargs(stop_algo.trail, algo_settings, shared_settings))
        if decision is not None and decision.price > position["stop_price"]:
            position["stop_price"] = decision.price
            position["stop_reason"] = decision.reason

    return cash, position, last_ts


@dataclass
class Trade:
    side: str  # "buy" | "sell"
    time: str
    price: float
    qty: float
    reason: str


@dataclass
class BacktestResult:
    symbol: str
    algorithm: str
    timeframe: str
    days_of_data: int
    days_before_trading: int
    starting_budget: float
    stop_algorithm: str = DEFAULT_STOP_ALGORITHM
    trades: list = field(default_factory=list)
    final_value: float = 0.0
    stop_loss_triggered: bool = False
    stop_loss_triggered_at: str | None = None

    @property
    def pnl(self) -> float:
        return round(self.final_value - self.starting_budget, 2)

    @property
    def pnl_pct(self) -> float:
        return round(self.pnl / self.starting_budget * 100, 2) if self.starting_budget else 0.0


def run_backtest(
    symbol: str, algorithm: str, timeframe: str, bars: list[Bar], daily_pairs: list[tuple],
    days_of_data: int, days_before_trading: int, starting_budget: float, max_loss_pct: float | None = None,
    stop_algorithm: str = DEFAULT_STOP_ALGORITHM, stop_settings: dict | None = None, bicak_pencere: int = 30,
    stop_bars: list[Bar] | None = None, stop_timeframe: str | None = None,
) -> BacktestResult:
    """daily_pairs: [(date, close), ...] sorted ascending, spanning at least
    from (bars[0] - ~400 days) to bars[-1] so SMA200-style daily gates have
    enough history at every simulated point.

    max_loss_pct ("zarar kes"): verildiğinde, başlangıç bütçesine göre
    gerçekleşmiş (kapanmış işlemlerdeki) toplam zarar bu yüzdeye ulaştığı
    anda yeni alım/satım durdurulur - o ana kadar açık kalan pozisyon
    kendi stop'uyla (veya test sonunda kapanışla) yönetilmeye devam eder,
    sadece yeni giriş sinyalleri artık işleme alınmaz.

    stop_algorithm: stop_algorithms.STOP_ALGORITHMS'ten seçilen id - canlı
    sistemin (alpaca_trailing_stop.manage_position) hangi algoritmayla
    çalıştığının aynısı simüle edilir.

    stop_settings: Stop Loss Ayarları sayfasında kullanıcının kaydettiği
    {"shared": {...}, "<algo_id>": {...}} - verilmezse (ya da bir alan hiç
    kaydedilmemişse) stop_algorithms.py'deki ilgili fonksiyonun kod-varsayılanı
    kullanılır (bkz. stop_algorithms.resolve_kwargs).

    bicak_pencere: algorithm="bicak_kanali" olduğunda buy_algorithms.
    bicak_kanali_signal'e geçirilen "pencere" (düşüş bacağı taramasının en
    güncel kaç bar ile sınırlanacağı) - Bıçak Kanalı Test modülündeki
    (bicak_kanali_test.py) aynı ayar, BackTest arayüzünden ayarlanabilir.
    Diğer algoritmalar için görmezden gelinir.

    stop_bars / stop_timeframe: verilirse, stop-loss tetiklenmesi (bir mumun
    low'u stop fiyatına değdi mi) ve trail hesaplaması alım sinyalinin
    üretildiği `bars`/`timeframe` yerine BU ayrı - genelde daha küçük
    periyotlu - bar listesi ve periyodu üzerinden yapılır. Bu, canlı sistemde
    alımın (alpaca_buy_points.py, sembole özel periyot) ve stop/trail'in
    (alpaca_trailing_stop.py, ayrı TRADE_TIMEFRAME) zaten farklı periyotlarda
    çalışmasıyla aynı ayrımı backtest'e taşır - alım sinyali hâlâ `bars` ile
    üretilir, sadece bir pozisyon açıldıktan sonra onun stop'u `stop_bars`
    ile izlenir. Verilmezse (None/boş), `bars`/`timeframe` her ikisi için de
    kullanılır - eski (tek periyotlu) davranış birebir korunur."""
    result = BacktestResult(symbol, algorithm, timeframe, days_of_data, days_before_trading, starting_budget,
                             stop_algorithm=stop_algorithm)
    result.final_value = starting_budget
    if not bars:
        return result

    _, algo_fn = ALGORITHMS[algorithm]
    stop_algo = STOP_ALGORITHMS[stop_algorithm]
    stop_settings = stop_settings or {}
    shared_settings = stop_settings.get("shared") or {}
    algo_settings = stop_settings.get(stop_algorithm) or {}
    is_daily_tf = timeframe == "1Day"

    stop_bars_eff = stop_bars if stop_bars else bars
    is_daily_tf_stop = (stop_timeframe or timeframe) == "1Day"

    trading_start = _parse(bars[0].t) + timedelta(days=days_before_trading)
    start_idx = 0
    while start_idx < len(bars) and _parse(bars[start_idx].t) < trading_start:
        start_idx += 1

    cash = starting_budget
    # stop_reason: seçili stop algoritmasının hangi aşaması şu anki stop_price'ı
    # kurdu (bkz. StopDecision.reason) - "ilk stop" (pozisyon yeni açıldı),
    # "breakeven", "kâr kilidi (+%X)", "structure@<fiyat>" gibi. Stop tetiklendiğinde
    # işlem tablosunda bu, sadece "stop" değil, HANGİ aşamanın sattırdığını gösterir.
    position = None  # {"entry_price", "qty", "stop_price", "stop_reason", "entry_idx"} - entry_idx, stop_bars_eff içinde
    resting = None   # {"price", "qty", "stop_price", "stop_reason", "reason"}

    i = start_idx
    while i < len(bars):
        bar = bars[i]
        bar_date = _parse(bar.t).date()

        if result.stop_loss_triggered:
            resting = None
            i += 1
            continue

        # No open position - check the resting entry order (if any) for a fill first,
        # using the price it already had going into this bar.
        if resting is not None and bar.l <= resting["price"]:
            qty = resting["qty"]
            cash -= qty * resting["price"]
            position = {"entry_price": resting["price"], "qty": qty,
                        "stop_price": resting["stop_price"], "stop_reason": resting["stop_reason"]}
            result.trades.append(Trade("buy", bar.t, resting["price"], qty, resting["reason"]))
            resting = None

            # Pozisyonun TÜM ömrü boyunca (stop tetiklenene ya da veri
            # tükenene kadar) stop_bars_eff üzerinde ilerler - alım tarafının
            # (bars/timeframe) coarse döngüsü bu süre boyunca devre dışı.
            cash, position, last_ts = _manage_position(
                position, stop_bars_eff, _parse(bar.t), daily_pairs, is_daily_tf_stop,
                stop_algo, algo_settings, shared_settings, max_loss_pct, result, cash, starting_budget,
            )
            if position is None:
                i = _first_index_after(bars, last_ts, start=i + 1)
                continue
            break  # stop_bars_eff tükendi, pozisyon hâlâ açık - test sonu kapanışı en altta yapılır

        sig_bars = _window(bars, i, SIGNAL_LOOKBACK_DAYS)
        daily_closes = _daily_closes_upto(daily_pairs, bar_date, inclusive=is_daily_tf) if algorithm == "trend_pullback" else None
        if algorithm == "bicak_kanali":
            signal = algo_fn(sig_bars, daily_closes, pencere=bicak_pencere)
        else:
            signal = algo_fn(sig_bars, daily_closes)
        if signal is not None:
            signal = reject_if_marketable(signal, bar.c)

        if signal is not None and signal.style == "breakout":
            # Kırılım sinyali - canlı sistemdeki (alpaca_buy_points.check_symbol)
            # market emri davranışını simüle eder: resting bir emir olarak bir
            # sonraki bara ERTELENMEZ, bu barın kapanışında (signal.price zaten
            # last.c) HEMEN doluyor - aksi halde backtest, fiyat kırılımdan
            # sonra geri çekilip tekrar o seviyeye DÖNMEDİKÇE hiç girmeyen,
            # gerçek davranışın tam tersi bir model kurardı.
            resting = None
            qty = math.floor(cash / signal.price) if signal.price > 0 else 0
            if qty > 0:
                cash -= qty * signal.price
                initial_stop = stop_algo.initial_stop(
                    signal.price, "long", bars=sig_bars,
                    **resolve_kwargs(stop_algo.initial_stop, algo_settings, shared_settings),
                )
                position = {"entry_price": signal.price, "qty": qty,
                            "stop_price": round(initial_stop, 2), "stop_reason": "ilk stop"}
                result.trades.append(Trade("buy", bar.t, signal.price, qty, signal.reason))
                cash, position, last_ts = _manage_position(
                    position, stop_bars_eff, _parse(bar.t), daily_pairs, is_daily_tf_stop,
                    stop_algo, algo_settings, shared_settings, max_loss_pct, result, cash, starting_budget,
                )
                if position is None:
                    i = _first_index_after(bars, last_ts, start=i + 1)
                    continue
                break
            i += 1
            continue

        if resting is not None:
            if signal is None:
                resting = None
            elif abs(signal.price - resting["price"]) >= 0.01:
                qty = math.floor(cash / signal.price) if signal.price > 0 else 0
                initial_stop = stop_algo.initial_stop(
                    signal.price, "long", bars=sig_bars,
                    **resolve_kwargs(stop_algo.initial_stop, algo_settings, shared_settings),
                )
                resting = ({"price": round(signal.price, 2), "qty": qty,
                            "stop_price": round(initial_stop, 2), "stop_reason": "ilk stop", "reason": signal.reason}
                           if qty > 0 else None)
        elif signal is not None:
            qty = math.floor(cash / signal.price) if signal.price > 0 else 0
            if qty > 0:
                initial_stop = stop_algo.initial_stop(
                    signal.price, "long", bars=sig_bars,
                    **resolve_kwargs(stop_algo.initial_stop, algo_settings, shared_settings),
                )
                resting = {"price": round(signal.price, 2), "qty": qty,
                           "stop_price": round(initial_stop, 2), "stop_reason": "ilk stop", "reason": signal.reason}

        i += 1

    if position is not None:
        last_bar = stop_bars_eff[-1]
        cash += position["qty"] * last_bar.c
        result.trades.append(Trade("sell", last_bar.t, round(last_bar.c, 4), position["qty"], "test_end_close"))

    result.final_value = round(cash, 2)
    return result
