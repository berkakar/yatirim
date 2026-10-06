"""
Premium buy-point scanner for the user's watchlist portfolio.

Reads the "premium-buy-portfolio" Alpaca watchlist for symbols and
portfolio_config.json (committed to this repo by the Streamlit page - see
github_config.py) for the total budget, each symbol's weight, and which
buy-point algorithm (see buy_algorithms.py) + bar timeframe is active for
each symbol - config["symbol_settings"][symbol] picks the combination the
user chose (in premium_buy_portfolio.py, informed by that symbol's own
BackTest results); a symbol without an entry there falls back to the
portfolio-wide config["algorithm"] / alpaca_trailing_stop.TIMEFRAME. For
every watchlisted symbol without an already-open position, runs that
symbol's algorithm on bars at its own timeframe and keeps a resting GTC
limit buy order at its price - placing it if none exists, updating it if
the signal has moved, canceling it if there's no longer a valid signal. The
actual fill happens on Alpaca's side whenever price reaches the order,
independent of how often this script runs - polling here only keeps the
order in sync with the current signal, it doesn't need to catch the fill
itself (unlike a market-order-on-poll approach, which can only react at
whatever moment it happens to check).

Exception: buy_algorithms.breakout_volume_signal's BuySignal.style is
"breakout", not "pullback" - a resting limit at the breakout bar's close
would either never fill (price keeps running) or only fill on a retest
that arguably invalidates the breakout thesis. check_symbol detects
style=="breakout" and places a market order instead (fills within this
pass, no resting order), arming its protective stop - from the actual
fill price, via the symbol's selected stop algorithm's initial_stop(),
same as every other entry - right after, the same "market order then arm
stop" pattern the top-up branch and buy_stop_rebuy.py's rebuy already use.
See the comment at that branch and check_symbol's own docstring.

A symbol removed from the watchlist (via premium_buy_portfolio.py's symbol
picker) stops being scanned by check_symbol entirely, which used to leave
any still-resting buy-limit order for it orphaned - nothing would ever
cancel it, so it could still fill later even though the user had removed
that symbol from the portfolio. cancel_orphaned_buy_limits runs once at the
start of every pass to clean these up (only orders this system placed,
tagged "algo-..."). An open position for a removed symbol is untouched
either way - alpaca_trailing_stop.py manages every open position's stop
independent of watchlist membership, so it keeps trailing normally.

config["budget"] used to be just a number the user typed in premium_buy_portfolio.py
(now the budget is equity x config["cash_allocation_pct"], see
resolve_pbp_budget) - nothing used to check it against Alpaca's actual cash before this, so a
mass stop-out (many symbols hitting their stop at once, e.g. a broad
sell-off) followed by a mass re-entry (once signals return) could try to
commit more than the account actually has, silently relying on margin (if
enabled) or failing order-by-order with no coordinated response.
compute_available_cash_for_buying now snapshots real cash (minus this
system's own still-resting buy-limit orders) once per pass, and check_symbol
caps every new entry/top-up to whatever fits in it - run_once decrements the
running total across symbols within the same pass so they don't all size
against the same unspent cash.

For a symbol that already has an open position, check_symbol instead runs
an "ilave alım" (top-up) check: if the symbol's target budget (budget *
weight_pct) now exceeds what's actually invested in it (qty * avg entry -
e.g. because the user raised the portfolio's total budget while keeping
weights the same) and the algorithm has a valid signal whose price is
within TOP_UP_MARKET_TOLERANCE_PCT of the live price, it tops up the
position for the shortfall.

This can NOT be a second resting GTC limit order the way a fresh entry is:
the position's existing protective stop-sell is already resting, and
Alpaca rejects any new order on the opposite side of an existing resting
order for the same symbol as a "potential wash trade" (HTTP 403,
"opposite side market/stop order exists" - confirmed in production against
MU, whose top-up silently failed with exactly this error every pass).
Instead, check_symbol cancels the resting stop, buys the shortfall with a
market order (filling in seconds, not sitting open for however long a
limit order might take to reach its price), and immediately re-arms a
stop sized to the new total qty - at the same price as before by default,
or tightened toward the new (top-up-blended) average entry if the user's
"top_up_stop_mode" setting (Premium Buy Point module) asks for that (see
alpaca_trailing_stop.load_top_up_stop_mode). Any failure in that sequence
(the market order itself, or its fill confirmation) re-arms the stop at
its old price/qty before giving up on the top-up, so a position is never
left without a stop.

Every entry is submitted as a bracket order with a stop-loss leg at whatever
level the symbol's selected stop algorithm's initial_stop() prescribes (see
stop_algorithms.py - resolved the same way as the buy algorithm, via
resolve_stop_algorithm: per-symbol config["symbol_settings"][symbol].
stop_algorithm, else the portfolio-wide config["stop_algorithm"], else
DEFAULT_STOP_ALGORITHM) below the limit price (see
alpaca_client.place_limit_entry), so the protective stop exists on Alpaca's
side the instant the entry fills - it doesn't wait for
alpaca_trailing_stop.py's next scheduled run, which GitHub Actions can delay
well past its nominal interval. That script's own initial-stop placement is
now just a fallback for a position that somehow has none (e.g. opened
outside this system); its (same, selected-algorithm) trailing logic still
runs on its own schedule to tighten the stop over time.

config["stop_loss_enabled"] / config["max_loss_pct"] (set in
premium_buy_portfolio.py, same UI as the BackTest module's "Zarar Kes") add
a portfolio-wide circuit breaker on top of that per-trade stop: before
placing a new entry for a symbol, check_symbol compares that symbol's
realized loss over the last STOP_LOSS_LOOKBACK_DAYS days (alpaca_realized_pnl_cache.
get_cached_realized_loss - same FIFO pairing as alpaca_client.compute_realized_loss,
but incrementally cached so it doesn't re-fetch the whole lookback window's
order history from Alpaca on every pass) against its allocated budget (budget
* weight_pct). Past the threshold, no new buy is placed for that symbol - it
stays in cash - though any already-open position keeps being managed by its
own stop as usual.

Run with --once (used by the GitHub Actions workflow, as an earlier step
than the trailing-stop pass).

Separately, --extended-hours-entries (run_extended_hours_entry_scan) lets a
fresh entry's signal fill during pre-market/after-hours too, not just
regular hours: Alpaca doesn't support bracket/OTO orders in extended hours
(only plain limit), so the entry goes out unbracketed and the script polls
for its own fill in a tight internal loop (its own GitHub Actions workflow,
independent of this one), arming a naive protective stop (the symbol's
selected stop algorithm's initial_stop()) the moment it detects a fill - not
instantly like a bracket, but within its poll interval rather than waiting on
the next regular session. That protection is a regular GTC stop order, not an
extended-hours limit-sell: a sell limit below market fills immediately
instead of waiting (see the AMAT note in run_extended_hours_entry_scan).
Alpaca queues the stop outside regular hours; alpaca_trailing_stop's
extended-hours guard watches it and converts it to a marketable limit if
price breaks it before the open. check_symbol's own existing-order check (see
already_bracketed) upgrades that plain order to a proper bracket the moment
regular hours see it still unfilled, so it's never left permanently
unprotected. Top-up during extended hours isn't supported yet - its market
order can't execute outside regular hours either, and would need the same
kind of redesign.

2026-09-28 emir analizi değişiklikleri (ayrıntı: 🧠 Algo Analiz > 📝 Değişiklik
Günlüğü, changelog.py; kodda "[2026-09-28 · Öneri N]" yorumları):
  - Öneri 4: Seans dışında bekleyen pullback limit alışlar iptal edilir,
    pre-market'te giriş yapılmaz, seansın ilk 15 dakikasında hiç alım yapılmaz
    (ENTRY_TIMING_DEFAULTS, cancel_pullback_limit_buys, in_entry_guard).
  - Öneri 5: Adet risk bazlı - (özsermaye x risk%) / (giriş - stop); ağırlık
    bütçesi ve nakit sadece tavan; portföy ısısı sınırı (build_risk_context,
    risk_sizing.py). İlave alımlar da aynı risk tavanına tabi.
"""

import argparse
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

import storage
from alpaca_bars_cache import DAILY_BARS_CACHE_PATH, INTRADAY_BARS_CACHE_PATH, get_cached_raw_bars, parse_iso
from alpaca_client import AlpacaClient
from alpaca_account import build_job_client
from alpaca_realized_pnl_cache import get_cached_realized_loss
from alpaca_trailing_stop import (
    extended_hours_session, get_bars_for_timeframe, load_stop_loss_settings, load_telegram_settings,
    load_top_up_stop_mode, minutes_since_regular_open, place_protective_stop, resolve_stop_algorithm, TIMEFRAME, log,
)
from buy_algorithms import ALGORITHMS, DEFAULT_ALGORITHM, reject_if_marketable
from heikin_ashi_intraday_core import get_cash_allocation_pct as get_ha_cash_allocation_pct
from heikin_ashi_intraday_core import load_holdings_local as load_ha_holdings
from module_cash import module_budget, module_used_cash, unspent_module_reserve
from orb_core import get_cash_allocation_pct as get_orb_cash_allocation_pct
from orb_core import load_holdings_local as load_orb_holdings
from relative_strength_core import get_cash_allocation_pct as get_rs_cash_allocation_pct
from relative_strength_core import load_holdings_local as load_rs_holdings
from stop_algorithms import DEFAULT_STOP_ALGORITHM, STOP_ALGORITHMS, resolve_kwargs
from risk_sizing import load_risk_settings, position_risk, remaining_portfolio_risk, risk_based_qty, top_up_qty_cap
from stop_tags import parse_shield_real_stop, stop_tag
from telegram_notify import TelegramError, send_telegram_message

load_dotenv()

# Tek kullanıcı (berkakar) varsayılıyor - çoklu kullanıcı desteği bu GitHub Action'a
# henüz eklenmedi (Streamlit tarafındaki per-user değişikliklerle tutarlı kalması
# için sadece isimler güncellendi).
WATCHLIST_NAME = "premium-buy-portfolio-berkakar"
CONFIG_PATH = "portfolio_config_berkakar.json"

# Top-up'ın canlıda gözlemlenen gerçek arızası (MU, 2026-09-08): mevcut
# pozisyonu koruyan resting stop-sell varken top-up için verilen düz
# buy-limit, Alpaca tarafından "potential wash trade" (403) olarak
# reddediliyordu - aynı sembolde zıt yönlü iki bağımsız açık emre izin
# verilmiyor. check_symbol artık top-up'ı market emriyle anında dolduruyor
# (resting bırakmıyor) ve TOP_UP_MARKET_TOLERANCE_PCT içindeyken - bkz.
# check_symbol'ün top-up dalı.
TOP_UP_MARKET_TOLERANCE_PCT = 0.5 / 100

LOOKBACK_DAYS = int(os.environ.get("BUY_LOOKBACK_DAYS", "60"))
DAILY_LOOKBACK_DAYS = int(os.environ.get("BUY_DAILY_LOOKBACK_DAYS", "400"))
STOP_LOSS_LOOKBACK_DAYS = int(os.environ.get("STOP_LOSS_LOOKBACK_DAYS", "90"))

# run_extended_hours_entry_scan - bir dolumu bir sonraki ~10dk'lık GitHub
# Actions tetiklemesine kadar değil, aynı çalıştırma içinde saniyeler
# içinde yakalayıp korumayı hemen kurabilmek için, script kendi içinde bu
# kadar süre boyunca (job'un geri kalanına ve bir sonraki tetiklemeye pay
# bırakacak şekilde) sıkı bir döngüyle emirleri kontrol eder.
EXTENDED_HOURS_ENTRY_POLL_WINDOW_SECONDS = int(os.environ.get("BUY_EXTENDED_HOURS_POLL_WINDOW_SECONDS", "360"))
EXTENDED_HOURS_ENTRY_POLL_INTERVAL_SECONDS = int(os.environ.get("BUY_EXTENDED_HOURS_POLL_INTERVAL_SECONDS", "15"))


# Premium Buy Point'in hiçbir alış algoritmasıyla eşleşmeyen stop
# algoritmaları: "opening_range", pozisyonun açıldığı seansın AÇILIŞ barının
# dibine kurulur ve sadece aynı seansın açılış kırılımıyla (orb_signal)
# anlamlı - ORB artık kendi modülünde (orb_core.py), buy_algorithms.
# ALGORITHMS'te değil. Günlerce bekleyebilen bir pullback limitinde bu
# seviye, emrin dolacağı günle ilgisiz, emrin konduğu günün açılış dibine
# kayar (gözlemlenen gerçek örnek: AAON, 2026-09-24/25 - talep bölgesi
# emrinin stop'u her yeni seansta açılış mumuna göre oynuyordu).
PBP_INCOMPATIBLE_STOP_ALGORITHMS = frozenset({"opening_range"})


def resolve_default_algorithm(config: dict) -> str:
    """Portföy geneli varsayılan alış algoritması - kayıtlı değer artık
    ALGORITHMS'te yoksa (ör. kendi modülüne taşınan "orb") DEFAULT_ALGORITHM'a
    düşülür, ama bu artık SESSİZ değil: her pass'te log'a yazılır. Aksi halde
    kullanıcı, hisselerin config'te görünen algoritmayla alındığını sanıyordu
    (AAON/ACAD/AAT/AMZN/MU "orb" diye kayıtlıyken aslında demand_zone ile
    alınıyordu)."""
    configured = config.get("algorithm") or DEFAULT_ALGORITHM
    if configured in ALGORITHMS:
        return configured
    log(f"⚠️ Portföy varsayılan alış algoritması '{configured}' artık Premium Buy Point'te geçerli değil "
        f"(kaldırılmış ya da kendi modülüne taşınmış) - '{DEFAULT_ALGORITHM}' kullanılıyor. Premium Buy "
        f"Point sayfasında varsayılan algoritmayı seçip portföyü kaydedin.")
    return DEFAULT_ALGORITHM


def warn_if_incompatible_stop_algorithm(symbol: str, algorithm: str, stop_algorithm: str) -> None:
    if stop_algorithm in PBP_INCOMPATIBLE_STOP_ALGORITHMS:
        log(f"⚠️ {symbol}: stop algoritması '{stop_algorithm}' alış algoritması '{algorithm}' ile uyumlu "
            f"değil (sadece ORB kırılımıyla anlamlı) - Premium Buy Point sayfasında başka bir stop "
            f"algoritması seçin.")


# [2026-09-28 · Öneri 4] Giriş zamanlaması - portfolio_config'teki
# "entry_timing" anahtarı (Premium Buy Point sayfasından değiştirilir):
#   pre_open_cancel_enabled: Seans dışında (after-hours ve pre-market) bu
#       sistemin normal seans için bıraktığı pullback limit alışları iptal
#       edilir, pre-market'te yeni giriş yapılmaz. Neden: 2026-09-28
#       analizinde 17 girişin 8'i 09:33-09:36 ET'de doldu - GTC limit emirleri
#       hisse açılışta o seviyenin altına boşlukla açılınca, yani TAM fiyat
#       düştüğü için doluyordu (ters seçim / adverse selection).
#   entry_guard_minutes: Seans açılışından sonraki bu kadar dakika boyunca
#       HİÇBİR yeni alım (limit, kırılım market emri, ilave alım) yapılmaz;
#       sinyal bu süreden sonra (varsayılan 09:45 ET) yeniden değerlendirilir.
ENTRY_TIMING_DEFAULTS = {"pre_open_cancel_enabled": True, "entry_guard_minutes": 15}


def load_entry_timing(config: dict) -> dict:
    saved = config.get("entry_timing") or {}
    return {**ENTRY_TIMING_DEFAULTS, **{k: v for k, v in saved.items() if v is not None}}


def cancel_pullback_limit_buys(client: AlpacaClient, include_extended_hours_orders: bool) -> list[str]:
    """[2026-09-28 · Öneri 4] Bu sistemin ("algo-" etiketli) açık limit
    alışlarını iptal eder. include_extended_hours_orders=False iken sadece
    normal seans emirleri (GTC bracket) iptal edilir - after-hours'ta
    run_extended_hours_entry_scan'in kendi day+extended_hours emirlerine
    dokunulmaz. İptal edilen sembolleri döner."""
    canceled = []
    try:
        open_orders = client.get_open_orders()
    except Exception as e:
        log(f"açılış öncesi limit iptali: açık emirler alınamadı: {e}")
        return canceled
    for order in open_orders:
        if order["type"] != "limit" or order["side"] != "buy":
            continue
        if not (order.get("client_order_id") or "").startswith("algo-"):
            continue
        if order.get("extended_hours") and not include_extended_hours_orders:
            continue
        try:
            client.cancel_order(order["id"])
            canceled.append(order["symbol"])
        except Exception as e:
            log(f"{order['symbol']}: açılış öncesi limit iptali başarısız: {e}")
    if canceled:
        log(f"Açılış öncesi limit iptali: {', '.join(sorted(canceled))} için bekleyen limit alış iptal edildi "
            f"(sinyal seans açılışından sonra yeniden değerlendirilecek).")
    return canceled


def load_local_config() -> dict:
    return storage.load_json(CONFIG_PATH, {"budget": 0, "weights": {}})


def _get_daily_closes(client: AlpacaClient, symbol: str) -> list[float]:
    """trend_pullback algoritmasının günlük SMA(200) kontrolü için - cache'li
    (alpaca_daily_bars_cache_berkakar.json) günlük bar'lardan kapanışları
    çıkarır. Günlük bar sadece günde bir kez kapandığı için, bu pencere
    Alpaca'dan gün içindeki her pass'te değil, sadece yeni bir gün açıldığında
    yeniden çekilir."""
    daily_start = datetime.now(timezone.utc) - timedelta(days=DAILY_LOOKBACK_DAYS)
    try:
        daily_bars = get_cached_raw_bars(client, DAILY_BARS_CACHE_PATH, symbol, "1Day", daily_start)
        return [b["c"] for b in daily_bars if parse_iso(b["t"]) >= daily_start]
    except Exception:
        return []


def check_symbol(
    client: AlpacaClient, symbol: str, weight_pct: float, budget: float, algorithm: str, timeframe: str,
    max_loss_pct: float | None = None, available_cash: float | None = None,
    top_up_stop_mode: str = "keep", stop_algorithm: str = DEFAULT_STOP_ALGORITHM,
    stop_settings: dict | None = None, in_entry_guard: bool = False, risk: dict | None = None,
) -> float:
    """Pozisyon yoksa: sinyale göre yeni bir giriş (bracket buy-limit) açar
    veya bekleyen girişi günceller - aşağıdaki asıl akış budur. TEK istisna:
    sinyal.style == "breakout" (buy_algorithms.breakout_volume_signal) ise
    resting limit yerine anında market emriyle girilir, çünkü kırılım sinyali
    "fiyat şu anda yukarı kırıyor" demektir - günlerce sinyal fiyatında
    bekleyen bir limit emri fiyatı ya hiç yakalayamaz ya da ancak fiyat geri
    çekilip o seviyeye dönerse (kırılımın büyük ölçüde geçersiz kaldığı bir
    "retest" durumunda) dolar. Market emri dolduktan hemen sonra, sinyaldeki
    değil GERÇEK dolma fiyatına göre - ama diğer tüm girişlerle AYNI seçili
    stop_algorithm/initial_stop() ile - ayrı bir stop-sell emri kurulur
    (bracket değil - market emirlere bu kod tabanında henüz bracket
    eklenmiyor; aynı "market + sonradan stop kur" örüntüsü ilave alım
    (top-up) ve buy_stop_rebuy.py'nin rebuy dalıyla aynı).

    Pozisyon zaten açıksa: hedef bütçe (budget * weight_pct), o sembole
    şu ana kadar yatırılmış tutarı (adet * ortalama giriş) aştığında ve
    algoritmanın hâlâ bir al sinyali olduğunda (ve güncel fiyat sinyal
    fiyatına TOP_UP_MARKET_TOLERANCE_PCT içindeyken), aradaki farkı bir
    "ilave alım" dalıyla tamamlar. Bu, resting bir buy-limit DEĞİL, market
    emridir: pozisyonu koruyan stop-sell zaten resting durumdayken zıt
    yönde ikinci bir resting emir Alpaca tarafından "wash trade" olarak
    reddediliyor (canlıda gözlemlendi) - o yüzden stop kısa süreliğine
    iptal edilip top-up market emriyle anında dolduruluyor, sonra yeni
    toplam adede göre (top_up_stop_mode'a göre fiyatı da güncellenerek
    veya aynı bırakılarak - bkz. alpaca_trailing_stop.py) hemen yeniden
    kuruluyor. "Zarar kes" (max_loss_pct) sadece YENİ girişleri engeller -
    zaten açık bir pozisyona ilave alımı değil.

    `available_cash` verilmişse (run_once, pass başında Alpaca'dan çekip
    her yeni emrin tutarını düşerek geçirir), hedeflenen adet bu sınırı
    aşamaz - aşarsa kullanılabilir nakde göre kısılır, hiç yer yoksa emir
    hiç verilmez. None ise sınır uygulanmaz (ör. testler). Dönüş değeri: bu
    çağrıda YENİ verilen emrin tutarı ($), emir verilmediyse 0.0 - run_once
    bunu available_cash'ten düşerek aynı pass'teki diğer sembollere de
    yansıtır."""
    stop_algo = STOP_ALGORITHMS[stop_algorithm]
    stop_settings = stop_settings or {}
    stop_shared_settings = stop_settings.get("shared") or {}
    stop_algo_settings = stop_settings.get(stop_algorithm) or {}
    existing_order = client.get_open_limit_buy_order(symbol)
    position = client.get_position(symbol)

    if position is not None and existing_order is not None:
        client.cancel_order(existing_order["id"])
        existing_order = None
        log(f"{symbol}: position already open, canceled stale buy-limit order.")

    if in_entry_guard:
        # [2026-09-28 · Öneri 4] Seansın ilk dakikaları: yeni alım yok, bekleyen
        # limit de (pre-market iptalinden kaçmış olabilir) iptal edilir.
        if existing_order is not None:
            client.cancel_order(existing_order["id"])
            log(f"{symbol}: açılış koruma süresi - bekleyen limit alış iptal edildi.")
        return 0.0

    dollar_amount = budget * (weight_pct / 100)
    if dollar_amount <= 0:
        return 0.0

    if max_loss_pct and position is None:
        realized_loss = get_cached_realized_loss(client, symbol, STOP_LOSS_LOOKBACK_DAYS)
        loss_pct = realized_loss / dollar_amount * 100
        if loss_pct >= max_loss_pct:
            log(f"{symbol}: zarar kes tetiklendi (gerçekleşen zarar %{loss_pct:.2f} >= %{max_loss_pct:g} eşik), "
                "nakitte kalınıyor, yeni alım yapılmıyor.")
            return 0.0

    if position is not None:
        invested = float(position["qty"]) * float(position["avg_entry_price"])
        top_up_amount = dollar_amount - invested
        if top_up_amount <= 0:
            return 0.0  # bütçe henüz yatırılan tutarı aşmıyor, ilave alıma gerek yok

    start = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    bars = get_bars_for_timeframe(
        client, symbol, timeframe, start, exclude_forming=True, cache_file=INTRADAY_BARS_CACHE_PATH,
    )
    if not bars:
        return 0.0

    daily_closes = _get_daily_closes(client, symbol) if algorithm == "trend_pullback" else None

    _, algo_fn = ALGORITHMS[algorithm]
    signal = algo_fn(bars, daily_closes)

    if signal is not None:
        try:
            live_price = client.get_latest_trade_price(symbol)
        except Exception:
            live_price = None
        if live_price is None:
            live_price = bars[-1].c
        signal = reject_if_marketable(signal, live_price)

    if signal is None:
        if position is None and existing_order is not None:
            client.cancel_order(existing_order["id"])
            log(f"{symbol}: no valid buy signal ({algorithm}), canceled resting buy-limit order.")
        return 0.0

    target_price = signal.price

    # Tags the order with which algorithm + timeframe produced it (parsed
    # back out in alpaca_dashboard.py's history table) -
    # "algo-<id>-<timeframe>-<symbol>-<epoch>", dash-separated since neither
    # algorithm ids (underscores) nor timeframes ("15Min", "1Day", ...)
    # contain dashes. Older orders placed before the timeframe was added to
    # this tag have the 4-part "algo-<id>-<symbol>-<epoch>" form instead -
    # _parse_order_tag in alpaca_dashboard.py handles both.
    client_order_id = f"algo-{algorithm}-{timeframe}-{symbol}-{int(datetime.now(timezone.utc).timestamp())}"

    if position is not None:
        top_up_qty = math.floor(top_up_amount / target_price)
        if available_cash is not None:
            top_up_qty = min(top_up_qty, math.floor(available_cash / target_price))
        if top_up_qty <= 0:
            if available_cash is not None:
                log(f"{symbol}: ilave alım için nakit yetersiz (kullanılabilir ${available_cash:.2f}), "
                    "bu pass'te atlanıyor.")
            return 0.0

        # canlıda gözlemlenen arıza: resting bir buy-limit, pozisyonun stop-sell'iyle
        # zıt yönde aynı anda açık kalamıyor (Alpaca "potential wash trade" ile
        # reddediyor - bkz. modül üstü not). O yüzden top-up resting limit emir
        # DEĞİL, fiyat hedefe (target_price) yeterince yakınken market emriyle
        # anında dolduruluyor - stop'suz kalan pencere market emrinin dolma
        # süresiyle sınırlı (saniyeler), bir resting limitin günlerce
        # korumasız bırakabileceğinden çok daha güvenli.
        if abs(live_price - target_price) / target_price > TOP_UP_MARKET_TOLERANCE_PCT:
            log(f"{symbol}: ilave al sinyali var ama güncel fiyat ({live_price:.2f}) hedeften "
                f"({target_price:.2f}) uzak, bu pass'te bekleniyor.")
            return 0.0

        stop_order = client.get_open_stop_order(symbol)
        if stop_order is None:
            log(f"{symbol}: ilave alım için resting stop bulunamadı, güvenlik için bu pass'te atlanıyor "
                "(alpaca_trailing_stop.py'nin bir sonraki geçişi stop'u kuracaktır).")
            return 0.0
        if parse_shield_real_stop(stop_order.get("client_order_id")) is not None:
            # [2026-09-28 · Öneri 3] Stop hâlâ açılış kalkanında (felaket seviyesinde) -
            # iptal edip yeniden kurmak etikette saklanan gerçek seviyeyi kaybettirirdi.
            log(f"{symbol}: açılış kalkanı henüz kaldırılmadı, ilave alım bu pass'te atlanıyor.")
            return 0.0

        old_stop_price = float(stop_order["stop_price"])
        old_qty = float(position["qty"])
        if risk is not None:
            # [2026-09-28 · Öneri 5] İlave alım sonrası pozisyonun toplam riski
            # (ortalama - stop) x adet, işlem başına risk$'ı ve kalan portföy
            # ısısını aşmasın.
            cap = top_up_qty_cap(
                risk["equity"], risk["risk_per_trade_pct"], float(position["avg_entry_price"]), old_qty,
                old_stop_price, live_price, risk["remaining"],
            )
            if cap < top_up_qty:
                log(f"{symbol}: ilave alım risk tavanıyla {top_up_qty} -> {cap} adede indirildi "
                    f"(stop {old_stop_price:.2f}, işlem başına risk %{risk['risk_per_trade_pct']:g}).")
                top_up_qty = cap
            if top_up_qty <= 0:
                return 0.0
        client.cancel_order(stop_order["id"])
        try:
            buy_order = client.place_market_entry(symbol, top_up_qty, "long")
            filled = client.wait_for_fill(buy_order["id"], timeout=30)
        except Exception as e:
            # Stop iptal edildi ama alım başarısız/zaman aşımına uğradı - pozisyon
            # korumasız kalmasın, eski adet/fiyatla stop'u hemen geri kur.
            log(f"{symbol}: ilave alım market emri başarısız/zaman aşımı ({e}), stop ${old_stop_price:.2f} "
                "olarak geri kuruldu, ilave alım yapılmadı.")
            place_protective_stop(
                client, symbol, old_qty, "long", old_stop_price, entry_price=float(position["avg_entry_price"]),
                client_order_id=stop_tag("restore", symbol), context="ilave alım başarısız, eski stop geri kuruluyor",
            )
            return 0.0

        fill_price = float(filled["filled_avg_price"])
        new_position = client.get_position(symbol)
        new_qty = float(new_position["qty"])

        if top_up_stop_mode == "tighten_to_new_entry":
            new_entry = float(new_position["avg_entry_price"])
            new_naive_stop = stop_algo.initial_stop(
                new_entry, "long", bars=bars,
                **resolve_kwargs(stop_algo.initial_stop, stop_algo_settings, stop_shared_settings),
            )
            new_stop_price = max(old_stop_price, round(new_naive_stop, 2))
        else:
            new_stop_price = old_stop_price

        new_stop_order = place_protective_stop(
            client, symbol, new_qty, "long", new_stop_price, entry_price=float(new_position["avg_entry_price"]),
            client_order_id=stop_tag("topup", symbol), context="ilave alım sonrası",
        )
        new_stop_price = float(new_stop_order.get("stop_price") or new_stop_price)
        if risk is not None:
            risk["remaining"] -= max(0.0, fill_price - new_stop_price) * top_up_qty
        spent = top_up_qty * fill_price
        log(f"{symbol}: bütçe arttı (yatırılan ${invested:.2f} -> hedef ${dollar_amount:.2f}), ilave al "
            f"sinyaliyle ({signal.reason}) {top_up_qty} adet market emriyle @ {fill_price:.2f} alındı "
            f"(order {buy_order['id']}), stop ${old_stop_price:.2f} -> ${new_stop_price:.2f} yeniden kuruldu.")
        return spent

    target_qty = math.floor(dollar_amount / target_price)
    if available_cash is not None:
        affordable_qty = math.floor(available_cash / target_price)
        if affordable_qty < target_qty:
            log(f"{symbol}: hedeflenen {target_qty} adet (${dollar_amount:.2f}) için nakit yetersiz "
                f"(kullanılabilir ${available_cash:.2f}), {affordable_qty} adede kısıtlandı.")
            target_qty = affordable_qty
    if target_qty <= 0:
        return 0.0

    if risk is not None:
        # [2026-09-28 · Öneri 5] Adet, stopa kadar olan risk işlem başına
        # risk$'ı aşmayacak şekilde hesaplanır; yukarıdaki ağırlık bütçesi ve
        # nakit artık sadece TAVAN. Kırılımda dolum fiyatı henüz bilinmediği
        # için güncel fiyatla tahmin edilir (stop yine gerçek dolumdan kurulur).
        sizing_price = live_price if signal.style == "breakout" else target_price
        estimated_stop = stop_algo.initial_stop(
            sizing_price, "long", bars=bars,
            **resolve_kwargs(stop_algo.initial_stop, stop_algo_settings, stop_shared_settings),
        )
        sizing = risk_based_qty(
            risk["equity"], sizing_price, estimated_stop, risk["risk_per_trade_pct"],
            risk["max_position_pct"], risk["remaining"],
        )
        if sizing.qty < target_qty:
            log(f"{symbol}: risk bazlı büyüklük - {sizing.explanation} (ağırlık bütçesi {target_qty} adet izin veriyordu).")
            target_qty = sizing.qty
        if target_qty <= 0:
            return 0.0
        risk["remaining"] -= target_qty * sizing.risk_per_share

    if signal.style == "breakout":
        # Diğer algoritmalar "pullback" tarzı olduğundan resting bir bracket
        # limit emri sinyal fiyatında beklemek sorun değil - fiyatın geri
        # çekilip o seviyeye gelmesi zaten beklenen senaryo. Kırılım ise tam
        # tersi: sinyal, fiyatın YUKARI kırıldığı anı işaret ediyor - resting
        # bir limit emir koyup günlerce sinyal fiyatında (kırılım barının
        # kapanışında) beklemek, fiyat yükselmeye devam ederse emri hiç
        # doldurmaz, geri çekilip o seviyeye dönerse de aslında kırılımın
        # geçersiz kaldığı bir "retest" anında doldurur - ikisi de kırılımı
        # kovalamak yerine tam tersini yapar. Bu yüzden kırılım sinyalinde
        # resting limit yerine, ilave alım (top-up) ve Alım-Stop-Alım
        # (buy_stop_rebuy._process_pending) dallarıyla AYNI örüntüyle
        # ("market emri ver -> dol -> gerçek dolma fiyatından seçili stop
        # algoritmasıyla stop kur"), anında market emri kullanılır.
        if existing_order is not None:
            # Bu style'a geçmeden önce ya da bir önceki pass'te bırakılmış
            # olabilecek bekleyen bir limit emri - artık geçersiz, iptal.
            client.cancel_order(existing_order["id"])
            log(f"{symbol}: kırılım sinyali market emriyle karşılanacak, bekleyen limit emri iptal edildi.")
        try:
            buy_order = client.place_market_entry(symbol, target_qty, "long", client_order_id=client_order_id)
            filled = client.wait_for_fill(buy_order["id"], timeout=30)
        except Exception as e:
            # Market emri normalde saniyeler içinde dolar - zaman aşımı/hata
            # (ör. trading halt) sıra dışı bir durum. Burada YENİ bir pozisyon
            # açılıyor (top-up'taki gibi geri kurulacak eski bir stop yok), o
            # yüzden yapacak bir şey kalmıyor: emir gerçekten hiç dolmadıysa
            # zaten açılmış bir pozisyon yok; olağandışı şekilde gecikip
            # sonradan dolarsa, alpaca_trailing_stop.py'nin "stop'u olmayan
            # pozisyon" fallback'i bir sonraki geçişinde koruma kurar.
            log(f"{symbol}: kırılım market emri başarısız/zaman aşımı ({e}), bu pass'te vazgeçildi.")
            return 0.0

        fill_price = float(filled["filled_avg_price"])
        filled_qty = float(filled["filled_qty"])
        # Stop, sinyaldeki (kırılım barının kapanış) fiyatına değil GERÇEK
        # dolma fiyatına göre, o hisse için seçili olan (portföydeki diğer
        # girişlerle AYNI) stop-loss algoritmasıyla kuruluyor. `bars=bars`,
        # "opening_range" (ORB) stop algoritmasının seansın açılış barını
        # bulabilmesi için geçiriliyor - diğer algoritmalar bu kwarg'ı
        # yoksayar (bkz. stop_algorithms.py).
        stop_loss_price = round(stop_algo.initial_stop(
            fill_price, "long", bars=bars,
            **resolve_kwargs(stop_algo.initial_stop, stop_algo_settings, stop_shared_settings),
        ), 2)
        stop_msg_suffix = f", stop ${stop_loss_price:.2f} kuruldu."
        try:
            stop_order = place_protective_stop(
                client, symbol, filled_qty, "long", stop_loss_price, entry_price=fill_price,
                client_order_id=stop_tag("initial", symbol), context="kırılım girişi",
            )
            stop_loss_price = float(stop_order.get("stop_price") or stop_loss_price)
            stop_msg_suffix = f", stop ${stop_loss_price:.2f} kuruldu."
        except Exception as e:
            stop_msg_suffix = f" ama koruma stopu KURULAMADI, pozisyon KORUMASIZ: {e}"
            log(f"{symbol}: kırılım sonrası stop kurulamadı: {e}")
        log(f"{symbol}: kırılım sinyali ({signal.reason}) market emriyle @ {fill_price:.2f} dolduruldu "
            f"(order {buy_order['id']}), {filled_qty:g} adet (${filled_qty * fill_price:.2f}){stop_msg_suffix}")
        return filled_qty * fill_price

    # Bracket stop-loss leg, relative to the limit (expected fill) price - see
    # alpaca_client.place_limit_entry and the module docstring. bars=bars: bkz.
    # kırılım dalındaki aynı not - "opening_range" (ORB) stop algoritması için.
    stop_loss_price = round(stop_algo.initial_stop(
        target_price, "long", bars=bars,
        **resolve_kwargs(stop_algo.initial_stop, stop_algo_settings, stop_shared_settings),
    ), 2)

    if existing_order is None:
        order = client.place_limit_entry(
            symbol, target_qty, "long", target_price,
            client_order_id=client_order_id, stop_loss_price=stop_loss_price,
        )
        log(f"{symbol}: placed buy-limit at {target_price:.2f} ({signal.reason}) with bracket stop at "
            f"{stop_loss_price:.2f}, qty {target_qty} (${dollar_amount:.2f}). order {order['id']}.")
        return target_qty * target_price

    current_price = float(existing_order["limit_price"])
    current_qty = float(existing_order["qty"])
    already_bracketed = existing_order.get("order_class") == "oto"
    if already_bracketed and abs(current_price - target_price) < 0.01 and abs(current_qty - target_qty) < 0.0001:
        return 0.0  # already correctly placed - order's notional was already counted at pass start
    # already_bracketed=False burada, fiyat/adet aynı kalsa bile aşağı düşüp
    # iptal+yeniden-yerleştiriyor: run_extended_hours_entry_scan'in bıraktığı
    # düz (bracket'sız) bir emir olabilir - normal seans onu görür görmez
    # bracket'a "yükseltmeliyiz", yoksa daha sonra regular hours'ta dolarsa
    # hiç stopu olmayan bir pozisyon açılırdı.

    # Alpaca rejects qty changes on fractional-qty orders via replace ("qty
    # must be an integer") - cancel and re-place instead, which works for
    # both fractional and whole-share quantities. Canceling the still-open
    # bracket parent takes its pending (not yet activated) stop-loss child
    # leg with it, so the replacement order's own bracket leg is the only
    # one left standing.
    client.cancel_order(existing_order["id"])
    order = client.place_limit_entry(
        symbol, target_qty, "long", target_price,
        client_order_id=client_order_id, stop_loss_price=stop_loss_price,
    )
    log(f"{symbol}: updated buy-limit {current_price:.2f} -> {target_price:.2f} "
        f"(bracket stop -> {stop_loss_price:.2f}, {signal.reason}). new order {order['id']}.")
    # Conservative double-count: the old order's notional was already part of
    # the pass-start snapshot (reserved), so treating the full new notional as
    # freshly spent under-states available_cash for later symbols rather than
    # over-stating it - never risks exceeding the real cash limit.
    return target_qty * target_price


def check_symbol_extended_hours_entry(
    client: AlpacaClient, symbol: str, weight_pct: float, budget: float, algorithm: str, timeframe: str,
    max_loss_pct: float | None = None, available_cash: float | None = None, risk: dict | None = None,
    stop_algorithm: str = DEFAULT_STOP_ALGORITHM, stop_settings: dict | None = None,
) -> tuple[float, str | None]:
    """check_symbol'ün fresh-entry dalının extended-hours varyantı - top-up
    burada YOK: top-up'ın market emri extended hours'ta hiç çalışmıyor,
    ayrı bir tasarım gerektirir, şimdilik kapsam dışı.

    Zaten pozisyonu olan semboller atlanır (yukarıdaki gerekçeyle). Zaten
    resting bir bracket (order_class "oto") emri olan semboller de atlanır -
    o emir zaten normal seansta kendi başına yönetiliyor; üstüne ikinci bir
    emir eklemek aynı sembolde çift dolma riski yaratırdı. Sinyal
    hesaplaması (bar çekme, algoritma, reject_if_marketable) check_symbol
    ile birebir aynı - farkı sadece SON adım: Alpaca extended hours'ta
    bracket desteklemediği için emir düz (order_class'sız) bir limit-buy
    olarak gönderilir; koruma, dolduğu tespit edildiğinde ayrı bir adımda
    (run_extended_hours_entry_scan'ın poll döngüsü) kurulur.

    Dönüş: (bu çağrıda yeni harcanan/rezerve edilen tutar, izlenecek açık
    emrin id'si ya da None)."""
    position = client.get_position(symbol)
    if position is not None:
        return 0.0, None  # top-up extended hours'ta henüz desteklenmiyor

    existing_order = client.get_open_limit_buy_order(symbol)
    if existing_order is not None and existing_order.get("order_class") == "oto":
        return 0.0, None  # zaten normal (bracket) bir emir resting - dokunma

    dollar_amount = budget * (weight_pct / 100)
    if dollar_amount <= 0:
        return 0.0, None

    if max_loss_pct:
        realized_loss = get_cached_realized_loss(client, symbol, STOP_LOSS_LOOKBACK_DAYS)
        loss_pct = realized_loss / dollar_amount * 100
        if loss_pct >= max_loss_pct:
            return 0.0, None

    start = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    bars = get_bars_for_timeframe(
        client, symbol, timeframe, start, exclude_forming=True, cache_file=INTRADAY_BARS_CACHE_PATH,
    )
    if not bars:
        return 0.0, None

    daily_closes = _get_daily_closes(client, symbol) if algorithm == "trend_pullback" else None

    _, algo_fn = ALGORITHMS[algorithm]
    signal = algo_fn(bars, daily_closes)

    if signal is not None:
        try:
            live_price = client.get_latest_trade_price(symbol)
        except Exception:
            live_price = None
        if live_price is None:
            live_price = bars[-1].c
        signal = reject_if_marketable(signal, live_price)

    if signal is None:
        if existing_order is not None:
            client.cancel_order(existing_order["id"])
            log(f"{symbol}: extended hours - artık geçerli bir al sinyali yok, resting emir iptal edildi.")
        return 0.0, None

    target_price = signal.price
    target_qty = math.floor(dollar_amount / target_price)
    if available_cash is not None:
        target_qty = min(target_qty, math.floor(available_cash / target_price))
    if risk is not None and target_qty > 0:
        # [2026-09-28 · Öneri 5] check_symbol ile aynı risk bazlı tavan.
        stop_algo = STOP_ALGORITHMS[stop_algorithm]
        stop_settings = stop_settings or {}
        estimated_stop = stop_algo.initial_stop(
            target_price, "long", bars=bars if stop_algorithm == "atr_volatility" else None,
            **resolve_kwargs(stop_algo.initial_stop, stop_settings.get(stop_algorithm) or {}, stop_settings.get("shared") or {}),
        )
        sizing = risk_based_qty(
            risk["equity"], target_price, estimated_stop, risk["risk_per_trade_pct"],
            risk["max_position_pct"], risk["remaining"],
        )
        target_qty = min(target_qty, sizing.qty)
        if target_qty > 0:
            risk["remaining"] -= target_qty * sizing.risk_per_share
    if target_qty <= 0:
        return 0.0, None

    if existing_order is not None:
        current_price = float(existing_order["limit_price"])
        current_qty = float(existing_order["qty"])
        if abs(current_price - target_price) < 0.01 and abs(current_qty - target_qty) < 0.0001:
            return 0.0, existing_order["id"]  # zaten doğru fiyatta, izlemeye devam
        client.cancel_order(existing_order["id"])

    client_order_id = f"algo-{algorithm}-{timeframe}-{symbol}-{int(datetime.now(timezone.utc).timestamp())}"
    order = client.place_extended_hours_entry_limit(symbol, target_qty, target_price, client_order_id)
    log(f"{symbol}: extended hours - {target_price:.2f}'den limit-buy gönderildi ({signal.reason}), "
        f"adet {target_qty} (${target_qty * target_price:.2f}), order {order['id']}.")
    return target_qty * target_price, order["id"]


def run_extended_hours_entry_scan(client: AlpacaClient) -> None:
    """Pre-market/after-hours'ta watchlist'teki (pozisyonu olmayan)
    sembolleri tarar, geçerli sinyali olanlar için düz bir extended-hours
    limit-buy gönderir/günceller (bkz. check_symbol_extended_hours_entry),
    sonra kendi içinde EXTENDED_HOURS_ENTRY_POLL_WINDOW_SECONDS boyunca her
    EXTENDED_HOURS_ENTRY_POLL_INTERVAL_SECONDS'de bir bu emirlerin dolup
    dolmadığını kontrol eder - dolduğu anda (bir sonraki ~10dk'lık GitHub
    Actions tetiklemesini beklemeden) hemen sembolün seçili stop algoritmasının
    naif ilk stop'uyla bir koruma (normal GTC stop - seans dışında
    extended-hours guard izler) kurar ve
    Telegram'dan bildirir.

    Poll penceresi bitene kadar dolmayan emirler olduğu gibi resting kalır -
    bir sonraki tetiklemede (ya da regular hours başladığında check_symbol
    tarafından, bkz. already_bracketed düzeltmesi) izlenmeye/yönetilmeye
    devam eder."""
    session = extended_hours_session(client)
    if session is None:
        log("Extended-hours penceresi dışında, giriş taraması atlanıyor.")
        return

    watchlist = client.get_watchlist_by_name(WATCHLIST_NAME)
    watchlist_symbols = {a["symbol"] for a in watchlist["assets"]} if watchlist else set()
    if not watchlist_symbols:
        log(f"{session}: premium-buy-portfolio watchlist boş, giriş taraması atlanıyor.")
        return

    config = load_local_config()
    weights = config.get("weights") or {}
    default_algorithm = resolve_default_algorithm(config)
    symbol_settings = config.get("symbol_settings") or {}
    max_loss_pct = float(config["max_loss_pct"]) if config.get("stop_loss_enabled") and config.get("max_loss_pct") else None
    stop_settings = load_stop_loss_settings()
    stop_shared_settings = stop_settings.get("shared") or {}

    # [2026-09-28 · Öneri 4] Normal seans için bırakılmış GTC pullback limitleri
    # seans dışında iptal edilir (açılış boşluğunda dolmasınlar); pre-market'te
    # yeni giriş de yapılmaz - sinyal seans açılışından sonra yeniden değerlendirilir.
    if load_entry_timing(config)["pre_open_cancel_enabled"]:
        cancel_pullback_limit_buys(client, include_extended_hours_orders=(session == "pre-market"))
        if session == "pre-market":
            log("pre-market: açılış öncesi iptal açık - pre-market girişi yapılmıyor.")
            return

    try:
        budget = resolve_pbp_budget(client, config)
        available_cash = compute_available_cash_for_buying(client, config)
        risk = build_risk_context(client, config)
    except Exception as e:
        log(f"{session}: hesap nakti alınamadı, bu pass atlanıyor: {e}")
        return

    pending: dict[str, tuple[str, str, str]] = {}  # symbol -> (order_id, stop_algorithm, timeframe), dolumu izlenecek
    for symbol in sorted(watchlist_symbols):
        settings = symbol_settings.get(symbol) or {}
        algorithm = settings.get("algorithm") or default_algorithm
        if algorithm not in ALGORITHMS:
            algorithm = default_algorithm
        timeframe = settings.get("timeframe") or TIMEFRAME
        stop_algorithm = resolve_stop_algorithm(config, symbol)
        try:
            spent, order_id = check_symbol_extended_hours_entry(
                client, symbol, float(weights.get(symbol, 0)), budget, algorithm, timeframe,
                max_loss_pct, available_cash, risk=risk, stop_algorithm=stop_algorithm, stop_settings=stop_settings,
            )
            available_cash -= spent
            if order_id is not None:
                pending[symbol] = (order_id, stop_algorithm, timeframe)
        except Exception as e:
            log(f"{symbol}: extended-hours check_symbol failed, atlanıyor: {e}")

    if not pending:
        return

    bot_token, chat_id = load_telegram_settings()
    log(f"{session}: {len(pending)} sembol için dolum bekleniyor, "
        f"{EXTENDED_HOURS_ENTRY_POLL_WINDOW_SECONDS}s boyunca her "
        f"{EXTENDED_HOURS_ENTRY_POLL_INTERVAL_SECONDS}s'de bir kontrol edilecek.")

    deadline = time.monotonic() + EXTENDED_HOURS_ENTRY_POLL_WINDOW_SECONDS
    while pending and time.monotonic() < deadline:
        for symbol, (order_id, stop_algorithm, timeframe) in list(pending.items()):
            try:
                order = client.get_order(order_id)
            except Exception as e:
                log(f"{symbol}: emir durumu sorgulanamadı, bu tur atlanıyor: {e}")
                continue

            if order["status"] == "filled":
                entry_price = float(order["filled_avg_price"])
                qty = float(order["filled_qty"])
                stop_algo = STOP_ALGORITHMS[stop_algorithm]
                stop_algo_settings = stop_settings.get(stop_algorithm) or {}
                # [2026-09-28 · Öneri 1] bars: ATR tabanlı stop (atr_volatility) girişin
                # periyodundaki ATR'yi kullansın - risk bazlı adet de aynı stopla hesaplandı.
                try:
                    entry_bars = get_bars_for_timeframe(
                        client, symbol, timeframe, datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS),
                        exclude_forming=True, cache_file=INTRADAY_BARS_CACHE_PATH,
                    )
                except Exception:
                    entry_bars = None
                naive_stop = stop_algo.initial_stop(
                    # Sadece ATR stopuna geçirilir: ORB/HA stopları barları "son seans"
                    # olarak yorumlar, seans dışında eski davranış (bars yok) korunur.
                    entry_price, "long", bars=(entry_bars or None) if stop_algorithm == "atr_volatility" else None,
                    **resolve_kwargs(stop_algo.initial_stop, stop_algo_settings, stop_shared_settings),
                )
                stop_price = round(naive_stop, 2)
                try:
                    # Koruma BİLEREK normal bir GTC stop emri, extended-hours
                    # limit-sell DEĞİL: piyasanın altındaki bir limit-sell
                    # "stop" gibi beklemez, anında en iyi alış fiyatından
                    # dolar (gözlemlenen gerçek örnek: AMAT, 2026-09-24 -
                    # 464.93'ten alınıp 446.33 limitli "koruma" 6 saniye sonra
                    # 462.11'den doldu). Alpaca seans dışında gönderilen stop
                    # emrini kabul edip sıraya alıyor: seviye Alpaca'da
                    # saklanıyor, extended-hours guard (guard_position) onu
                    # resting stop olarak görüp kırılırsa marketable limite
                    # çeviriyor, normal seans açılınca da kendisi devreye giriyor.
                    stop_order = place_protective_stop(
                        client, symbol, qty, "long", stop_price, entry_price=entry_price,
                        client_order_id=stop_tag("initial", symbol), context="seans dışı giriş",
                    )
                    stop_price = float(stop_order.get("stop_price") or stop_price)
                    msg = (
                        f"✅ {symbol}: extended hours girişi {entry_price:.2f}'den doldu (adet {qty:g}), "
                        f"koruma stopu {stop_price:.2f} seviyesinden (GTC stop - seans dışında "
                        f"extended-hours guard izliyor) kuruldu."
                    )
                except Exception as e:
                    msg = (
                        f"🚨 {symbol}: extended hours girişi {entry_price:.2f}'den doldu ama koruma stopu "
                        f"kurulamadı, pozisyon şu an KORUMASIZ: {e}"
                    )
                log(msg)
                if bot_token and chat_id:
                    try:
                        send_telegram_message(bot_token, chat_id, msg)
                    except TelegramError:
                        pass
                del pending[symbol]
            elif order["status"] in ("canceled", "expired", "rejected"):
                del pending[symbol]

        if pending:
            time.sleep(EXTENDED_HOURS_ENTRY_POLL_INTERVAL_SECONDS)

    if pending:
        log(f"{session}: {len(pending)} sembol hâlâ dolmadı, bir sonraki taramada izlenmeye devam edilecek: "
            f"{', '.join(pending)}.")


def cancel_orphaned_buy_limits(client: AlpacaClient, watchlist_symbols: set[str]) -> None:
    """Bir sembol Premium Buy Point portföyünden (watchlist) çıkarıldığında
    check_symbol artık o sembol için hiç çalışmıyor - eğer o sembolde henüz
    dolmamış, bu sistemin açtığı ("algo-" ile başlayan client_order_id'li)
    bir GTC buy-limit emri kalmışsa, kimse onu iptal etmiyordu ve fiyat oraya
    gelirse hâlâ dolabiliyordu. Her --once taramasının başında, artık
    watchlist'te olmayan sembollerin bu tür emirlerini temizler. Elle
    (bu sistem dışında) açılmış emirlere ya da açık pozisyonlara dokunmaz -
    stop yönetimi zaten alpaca_trailing_stop.py'de watchlist'ten bağımsız."""
    try:
        open_orders = client.get_open_orders()
    except Exception as e:
        log(f"failed to fetch open orders for orphan cleanup, skipping: {e}")
        return

    for order in open_orders:
        if order["type"] != "limit" or order["side"] != "buy":
            continue
        if not (order.get("client_order_id") or "").startswith("algo-"):
            continue  # bu sistemin açmadığı bir emir - dokunma
        symbol = order["symbol"]
        if symbol in watchlist_symbols:
            continue  # hâlâ takip ediliyor, check_symbol kendi yönetir
        try:
            client.cancel_order(order["id"])
            log(f"{symbol}: portföyden çıkarılmış, kalan buy-limit emri iptal edildi ({order['id']}).")
        except Exception as e:
            log(f"{symbol}: orphan buy-limit cleanup failed, skipping: {e}")


def build_risk_context(client: AlpacaClient, config: dict) -> dict | None:
    """[2026-09-28 · Öneri 5] Pass başında bir kez: özsermaye ve portföy
    ısısı (açık pozisyonların stopa kadar riski + bekleyen giriş emirleri).
    Risk bazlı büyüklük kapalıysa None - check_symbol eski (sadece ağırlık
    bütçesi) davranışa döner. Dönen dict'in "remaining" alanı check_symbol
    tarafından her yeni emirde azaltılır."""
    settings = load_risk_settings(config)
    if not settings["risk_sizing_enabled"]:
        return None
    equity = float(client.get_account()["equity"])
    per_trade = equity * float(settings["risk_per_trade_pct"]) / 100
    orders = client.get_open_orders()
    stops: dict[str, float] = {}
    pending_risk = 0.0
    for o in orders:
        if o["type"] in ("stop", "stop_limit") and o["side"] == "sell":
            real = parse_shield_real_stop(o.get("client_order_id"))
            stops[o["symbol"]] = real if real is not None else float(o["stop_price"])
        elif o["type"] == "limit" and o["side"] == "buy" and (o.get("client_order_id") or "").startswith("algo-"):
            # Bekleyen girişler zaten risk bazlı boyutlandığı için her biri ~1 risk$ sayılır.
            pending_risk += per_trade
    open_risk = pending_risk
    for pos in client.get_all_positions():
        qty = float(pos["qty"])
        if qty > 0 and pos.get("asset_class", "us_equity") == "us_equity":
            open_risk += position_risk(float(pos["avg_entry_price"]), qty, stops.get(pos["symbol"]))
    remaining = remaining_portfolio_risk(equity, float(settings["max_portfolio_risk_pct"]), open_risk)
    log(f"Risk bağlamı: özsermaye {equity:,.0f}$, açık risk {open_risk:,.0f}$, kalan portföy riski "
        f"{remaining:,.0f}$ (işlem başına %{settings['risk_per_trade_pct']:g} = {per_trade:,.0f}$).")
    return {
        "equity": equity,
        "risk_per_trade_pct": float(settings["risk_per_trade_pct"]),
        "max_position_pct": float(settings["max_position_pct"]),
        "remaining": remaining,
    }


def load_module_risk_context(client: AlpacaClient) -> dict | None:
    """ORB / Relative Strength / Heikin Ashi modüllerinin kendi taramalarında
    kullandığı risk bağlamı - Premium Buy Point'in risk ayarlarıyla
    (portfolio_config "risk_sizing") aynı. Hata olursa None: modül eski
    davranışla (sadece nakit payı) devam eder, alım durmaz."""
    try:
        return build_risk_context(client, load_local_config())
    except Exception as e:
        log(f"risk bağlamı alınamadı, bu taramada risk tavanı uygulanmıyor: {e}")
        return None


def resolve_pbp_budget(client: AlpacaClient, config: dict, account: dict | None = None) -> float:
    """Premium Buy Point'in toplam bütçesi (hisse ağırlıkları bunun yüzdesi).

    [2026-10-06] config["cash_allocation_pct"] varsa diğer modüllerle aynı
    mantık: bütçe = hesap değeri (equity) x yüzde - alımlar nakdi azalttıkça
    küçülmez (bkz. module_cash.py). Yoksa (yüzde ayarı kaydedilmemiş eski
    config) eski sabit config["budget"] tutarı kullanılır."""
    pct = config.get("cash_allocation_pct")
    if pct is None:
        return float(config.get("budget") or 0)
    return module_budget(account if account is not None else client.get_account(), float(pct))


def compute_available_cash_for_buying(client: AlpacaClient, config: dict | None = None) -> float:
    """Alpaca'daki gerçek nakit bakiyesinden (marjin/kaldıraç değil), bu
    sistemin hâlâ açık/bekleyen ("algo-" etiketli) buy-limit emirlerinin
    toplam tutarını düşerek, bu pass'te YENİ bir giriş/top-up emri için
    gerçekten kullanılabilir nakti hesaplar - aksi halde aynı nakit hem eski
    bekleyen emirler hem de bu pass'te verilecek yeni emirler tarafından iki
    kez sayılmış olurdu. run_once bunu pass başında bir kez çeker ve her
    check_symbol çağrısının harcadığı tutarı düşerek sıradaki sembollere
    yansıtır - "tüm stoplar aynı anda kırılıp sonra hepsi aynı anda yeniden
    sinyal verirse" senaryosunda, portföyün gerçek nakdinin üstüne çıkmayı
    önler.

    Relative Strength Rotasyonu, Açılış Aralığı Kırılımı (ORB) VE Heikin Ashi Gün İçi modülleri
    etkinleştirilmişse, o modüllerin HENÜZ HARCANMAMIŞ payları (hesap
    değeri x yüzde - modülün elindeki pozisyonların alış maliyeti, bkz.
    module_cash.py) nakitten DÜŞÜLÜR - aksi halde bağımsız sistemler aynı
    gerçek nakti birbirinden habersiz harcamaya çalışırdı. Modülün zaten
    hisseye dönmüş kısmı nakitte yer almadığı için ikinci kez düşülmez. Bir
    modül hiç açılmamışsa/devre dışıysa payı 0'dır, davranış o modül hiç
    yokmuş gibi aynı kalır (geriye dönük uyumlu).

    [2026-10-06] Premium Buy Point'in kendi nakit payı (config
    "cash_allocation_pct") tanımlıysa kullanılabilir nakit ayrıca
    bütçe - PBP hisselerinin (ağırlık listesindeki, modüllere ait olmayan
    semboller) alış maliyeti - bekleyen emirler ile sınırlanır."""
    if config is None:
        config = load_local_config()
    modules = [
        (get_rs_cash_allocation_pct("berkakar"), set(load_rs_holdings("berkakar"))),
        (get_orb_cash_allocation_pct("berkakar"), set(load_orb_holdings("berkakar"))),
        (get_ha_cash_allocation_pct("berkakar"), set(load_ha_holdings("berkakar"))),
    ]
    own_pct = config.get("cash_allocation_pct")
    account = client.get_account()
    need_positions = own_pct is not None or any(pct > 0 for pct, _ in modules)
    positions = client.get_all_positions() if need_positions else []
    cash = float(account["cash"]) - unspent_module_reserve(account, positions, modules)
    reserved = sum(
        float(o["qty"]) * float(o["limit_price"])
        for o in client.get_open_orders()
        if o["type"] == "limit" and o["side"] == "buy" and (o.get("client_order_id") or "").startswith("algo-")
    )
    available = cash - reserved
    if own_pct is not None:
        module_symbols = set().union(*(symbols for _, symbols in modules))
        own_symbols = set(config.get("weights") or {}) - module_symbols
        used = module_used_cash(positions, own_symbols)
        available = min(available, module_budget(account, float(own_pct)) - used - reserved)
    return max(0.0, available)


def run_once(client: AlpacaClient) -> None:
    clock = client.get_clock()
    if not clock["is_open"]:
        log("Market closed, skipping buy-point scan.")
        return

    watchlist = client.get_watchlist_by_name(WATCHLIST_NAME)
    watchlist_symbols = {a["symbol"] for a in watchlist["assets"]} if watchlist else set()
    cancel_orphaned_buy_limits(client, watchlist_symbols)

    if not watchlist_symbols:
        log("No premium-buy-portfolio watchlist, or it's empty.")
        return

    config = load_local_config()
    weights = config.get("weights") or {}
    default_algorithm = resolve_default_algorithm(config)
    symbol_settings = config.get("symbol_settings") or {}
    max_loss_pct = float(config["max_loss_pct"]) if config.get("stop_loss_enabled") and config.get("max_loss_pct") else None
    top_up_stop_mode = load_top_up_stop_mode(config)
    stop_settings = load_stop_loss_settings()

    try:
        budget = resolve_pbp_budget(client, config)
        available_cash = compute_available_cash_for_buying(client, config)
        risk = build_risk_context(client, config)
    except Exception as e:
        log(f"failed to fetch account cash, skipping this pass to avoid buying blind: {e}")
        return

    # [2026-09-28 · Öneri 4] Seansın ilk entry_guard_minutes dakikasında yeni alım yok.
    timing = load_entry_timing(config)
    minutes_since_open = minutes_since_regular_open(client)
    in_entry_guard = (
        float(timing["entry_guard_minutes"]) > 0 and minutes_since_open is not None
        and minutes_since_open < float(timing["entry_guard_minutes"])
    )
    if in_entry_guard:
        log(f"Açılış koruma süresi ({minutes_since_open:.0f}/{timing['entry_guard_minutes']} dk) - "
            "bu pass'te yeni alım yapılmıyor, bekleyen limit alışlar iptal ediliyor.")

    for symbol in sorted(watchlist_symbols):
        settings = symbol_settings.get(symbol) or {}
        algorithm = settings.get("algorithm") or default_algorithm
        if algorithm not in ALGORITHMS:
            algorithm = default_algorithm
        timeframe = settings.get("timeframe") or TIMEFRAME
        stop_algorithm = resolve_stop_algorithm(config, symbol)
        warn_if_incompatible_stop_algorithm(symbol, algorithm, stop_algorithm)
        try:
            spent = check_symbol(
                client, symbol, float(weights.get(symbol, 0)), budget, algorithm, timeframe, max_loss_pct,
                available_cash, top_up_stop_mode, stop_algorithm, stop_settings,
                in_entry_guard=in_entry_guard, risk=risk,
            )
            available_cash -= spent
        except Exception as e:
            # One symbol's order getting rejected (or any other failure) must
            # never take the rest of the watchlist down with it - and, since
            # this script's --once run shares a job with alpaca_trailing_stop.py
            # (the next step, only reached if this one exits 0), letting an
            # exception escape here would silently cancel stop-loss management
            # for every open position too.
            log(f"{symbol}: check_symbol failed, skipping this symbol this run: {e}")


def build_client() -> AlpacaClient:
    # Adres ve anahtarlar kullanıcının hesap türü ayarından (Sanal Para / Gerçek Para) -
    # bkz. alpaca_account.build_job_client.
    return build_job_client("berkakar")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit (used by GitHub Actions).")
    parser.add_argument(
        "--extended-hours-entries", action="store_true",
        help="Pre-market/after-hours'ta yeni giriş sinyallerini tara, dolanları hemen koru ve çık "
             "(ayrı bir GitHub Actions workflow'u tarafından kullanılır).",
    )
    args = parser.parse_args()

    client = build_client()
    if args.extended_hours_entries:
        run_extended_hours_entry_scan(client)
    else:
        run_once(client)
