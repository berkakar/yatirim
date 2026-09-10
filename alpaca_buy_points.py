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

A symbol removed from the watchlist (via premium_buy_portfolio.py's symbol
picker) stops being scanned by check_symbol entirely, which used to leave
any still-resting buy-limit order for it orphaned - nothing would ever
cancel it, so it could still fill later even though the user had removed
that symbol from the portfolio. cancel_orphaned_buy_limits runs once at the
start of every pass to clean these up (only orders this system placed,
tagged "algo-..."). An open position for a removed symbol is untouched
either way - alpaca_trailing_stop.py manages every open position's stop
independent of watchlist membership, so it keeps trailing normally.

config["budget"] is just a number the user typed in premium_buy_portfolio.py
- nothing used to check it against Alpaca's actual cash before this, so a
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

Every entry is submitted as a bracket order with a stop-loss leg at
INITIAL_STOP_PCT below the limit price (see alpaca_client.place_limit_entry),
so the protective stop exists on Alpaca's side the instant the entry fills -
it doesn't wait for alpaca_trailing_stop.py's next scheduled run, which
GitHub Actions can delay well past its nominal interval. That script's own
initial-stop placement is now just a fallback for a position that somehow
has none (e.g. opened outside this system); its structure-based trailing
still runs on its own schedule to tighten the stop over time.

config["stop_loss_enabled"] / config["max_loss_pct"] (set in
premium_buy_portfolio.py, same UI as the BackTest module's "Zarar Kes") add
a portfolio-wide circuit breaker on top of that per-trade stop: before
placing a new entry for a symbol, check_symbol compares that symbol's
realized loss over the last STOP_LOSS_LOOKBACK_DAYS days (alpaca_client.
AlpacaClient.compute_realized_loss, paired from its own fill history) against
its allocated budget (budget * weight_pct). Past the threshold, no new buy
is placed for that symbol - it stays in cash - though any already-open
position keeps being managed by its own stop as usual.

Run with --once (used by the GitHub Actions workflow, as an earlier step
than the trailing-stop pass).

Separately, --extended-hours-entries (run_extended_hours_entry_scan) lets a
fresh entry's signal fill during pre-market/after-hours too, not just
regular hours: Alpaca doesn't support bracket/OTO orders in extended hours
(only plain limit), so the entry goes out unbracketed and the script polls
for its own fill in a tight internal loop (its own GitHub Actions workflow,
independent of this one), arming a naive-%INITIAL_STOP_PCT protective
extended-hours limit-sell the moment it detects a fill - not instantly like
a bracket, but within its poll interval rather than waiting on the next
regular session. check_symbol's own existing-order check (see
already_bracketed) upgrades that plain order to a proper bracket the moment
regular hours see it still unfilled, so it's never left permanently
unprotected. Top-up during extended hours isn't supported yet - its market
order can't execute outside regular hours either, and would need the same
kind of redesign.
"""

import argparse
import json
import math
import os
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from alpaca_client import AlpacaClient, DEFAULT_TRADING_URL, DEFAULT_DATA_URL
from alpaca_trailing_stop import (
    INITIAL_STOP_PCT, extended_hours_session, get_regular_hours_bars, load_telegram_settings,
    load_top_up_stop_mode, TIMEFRAME, log,
)
from buy_algorithms import ALGORITHMS, DEFAULT_ALGORITHM, reject_if_marketable
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


def load_local_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {"budget": 0, "weights": {}}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def check_symbol(
    client: AlpacaClient, symbol: str, weight_pct: float, budget: float, algorithm: str, timeframe: str,
    max_loss_pct: float | None = None, available_cash: float | None = None,
    top_up_stop_mode: str = "keep",
) -> float:
    """Pozisyon yoksa: sinyale göre yeni bir giriş (bracket buy-limit) açar
    veya bekleyen girişi günceller - aşağıdaki asıl akış budur.

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
    existing_order = client.get_open_limit_buy_order(symbol)
    position = client.get_position(symbol)

    if position is not None and existing_order is not None:
        client.cancel_order(existing_order["id"])
        existing_order = None
        log(f"{symbol}: position already open, canceled stale buy-limit order.")

    dollar_amount = budget * (weight_pct / 100)
    if dollar_amount <= 0:
        return 0.0

    if max_loss_pct and position is None:
        realized_loss = client.compute_realized_loss(symbol, STOP_LOSS_LOOKBACK_DAYS)
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
    bars = get_regular_hours_bars(client, symbol, timeframe, start, exclude_forming=True)
    if not bars:
        return 0.0

    daily_closes = None
    if algorithm == "trend_pullback":
        daily_start = datetime.now(timezone.utc) - timedelta(days=DAILY_LOOKBACK_DAYS)
        try:
            daily_closes = [b["c"] for b in client.get_raw_bars(symbol, "1Day", daily_start.isoformat())]
        except Exception:
            daily_closes = []

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

        old_stop_price = float(stop_order["stop_price"])
        old_qty = float(position["qty"])
        client.cancel_order(stop_order["id"])
        try:
            buy_order = client.place_market_entry(symbol, top_up_qty, "long")
            filled = client.wait_for_fill(buy_order["id"], timeout=30)
        except Exception as e:
            # Stop iptal edildi ama alım başarısız/zaman aşımına uğradı - pozisyon
            # korumasız kalmasın, eski adet/fiyatla stop'u hemen geri kur.
            log(f"{symbol}: ilave alım market emri başarısız/zaman aşımı ({e}), stop ${old_stop_price:.2f} "
                "olarak geri kuruldu, ilave alım yapılmadı.")
            client.place_stop_order(symbol, old_qty, "long", old_stop_price)
            return 0.0

        fill_price = float(filled["filled_avg_price"])
        new_position = client.get_position(symbol)
        new_qty = float(new_position["qty"])

        if top_up_stop_mode == "tighten_to_new_entry":
            new_entry = float(new_position["avg_entry_price"])
            new_stop_price = max(old_stop_price, round(new_entry * (1 - INITIAL_STOP_PCT), 2))
        else:
            new_stop_price = old_stop_price

        client.place_stop_order(symbol, new_qty, "long", new_stop_price)
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

    # Bracket stop-loss leg, relative to the limit (expected fill) price - see
    # alpaca_client.place_limit_entry and the module docstring.
    stop_loss_price = round(target_price * (1 - INITIAL_STOP_PCT), 2)

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
    max_loss_pct: float | None = None, available_cash: float | None = None,
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
        realized_loss = client.compute_realized_loss(symbol, STOP_LOSS_LOOKBACK_DAYS)
        loss_pct = realized_loss / dollar_amount * 100
        if loss_pct >= max_loss_pct:
            return 0.0, None

    start = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    bars = get_regular_hours_bars(client, symbol, timeframe, start, exclude_forming=True)
    if not bars:
        return 0.0, None

    daily_closes = None
    if algorithm == "trend_pullback":
        daily_start = datetime.now(timezone.utc) - timedelta(days=DAILY_LOOKBACK_DAYS)
        try:
            daily_closes = [b["c"] for b in client.get_raw_bars(symbol, "1Day", daily_start.isoformat())]
        except Exception:
            daily_closes = []

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
    Actions tetiklemesini beklemeden) hemen naif %INITIAL_STOP_PCT'lik bir
    koruma (day+extended-hours limit-sell) kurar ve Telegram'dan bildirir.

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
    budget = float(config.get("budget") or 0)
    weights = config.get("weights") or {}
    default_algorithm = config.get("algorithm") or DEFAULT_ALGORITHM
    if default_algorithm not in ALGORITHMS:
        default_algorithm = DEFAULT_ALGORITHM
    symbol_settings = config.get("symbol_settings") or {}
    max_loss_pct = float(config["max_loss_pct"]) if config.get("stop_loss_enabled") and config.get("max_loss_pct") else None

    try:
        available_cash = compute_available_cash_for_buying(client)
    except Exception as e:
        log(f"{session}: hesap nakti alınamadı, bu pass atlanıyor: {e}")
        return

    pending: dict[str, str] = {}  # symbol -> order_id, dolumu izlenecek
    for symbol in sorted(watchlist_symbols):
        settings = symbol_settings.get(symbol) or {}
        algorithm = settings.get("algorithm") or default_algorithm
        if algorithm not in ALGORITHMS:
            algorithm = default_algorithm
        timeframe = settings.get("timeframe") or TIMEFRAME
        try:
            spent, order_id = check_symbol_extended_hours_entry(
                client, symbol, float(weights.get(symbol, 0)), budget, algorithm, timeframe,
                max_loss_pct, available_cash,
            )
            available_cash -= spent
            if order_id is not None:
                pending[symbol] = order_id
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
        for symbol, order_id in list(pending.items()):
            try:
                order = client.get_order(order_id)
            except Exception as e:
                log(f"{symbol}: emir durumu sorgulanamadı, bu tur atlanıyor: {e}")
                continue

            if order["status"] == "filled":
                entry_price = float(order["filled_avg_price"])
                qty = float(order["filled_qty"])
                stop_price = round(entry_price * (1 - INITIAL_STOP_PCT), 2)
                try:
                    client.place_extended_hours_limit(symbol, qty, "long", stop_price)
                    msg = (
                        f"✅ {symbol}: extended hours girişi {entry_price:.2f}'den doldu (adet {qty:g}), "
                        f"koruma stopu hemen {stop_price:.2f} seviyesinden (day+extended-hours limit) kuruldu."
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


def compute_available_cash_for_buying(client: AlpacaClient) -> float:
    """Alpaca'daki gerçek nakit bakiyesinden (marjin/kaldıraç değil), bu
    sistemin hâlâ açık/bekleyen ("algo-" etiketli) buy-limit emirlerinin
    toplam tutarını düşerek, bu pass'te YENİ bir giriş/top-up emri için
    gerçekten kullanılabilir nakti hesaplar - aksi halde aynı nakit hem eski
    bekleyen emirler hem de bu pass'te verilecek yeni emirler tarafından iki
    kez sayılmış olurdu. run_once bunu pass başında bir kez çeker ve her
    check_symbol çağrısının harcadığı tutarı düşerek sıradaki sembollere
    yansıtır - "tüm stoplar aynı anda kırılıp sonra hepsi aynı anda yeniden
    sinyal verirse" senaryosunda, portföyün gerçek nakdinin üstüne çıkmayı
    önler."""
    cash = float(client.get_account()["cash"])
    reserved = sum(
        float(o["qty"]) * float(o["limit_price"])
        for o in client.get_open_orders()
        if o["type"] == "limit" and o["side"] == "buy" and (o.get("client_order_id") or "").startswith("algo-")
    )
    return max(0.0, cash - reserved)


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
    budget = float(config.get("budget") or 0)
    weights = config.get("weights") or {}
    default_algorithm = config.get("algorithm") or DEFAULT_ALGORITHM
    if default_algorithm not in ALGORITHMS:
        default_algorithm = DEFAULT_ALGORITHM
    symbol_settings = config.get("symbol_settings") or {}
    max_loss_pct = float(config["max_loss_pct"]) if config.get("stop_loss_enabled") and config.get("max_loss_pct") else None
    top_up_stop_mode = load_top_up_stop_mode()

    try:
        available_cash = compute_available_cash_for_buying(client)
    except Exception as e:
        log(f"failed to fetch account cash, skipping this pass to avoid buying blind: {e}")
        return

    for symbol in sorted(watchlist_symbols):
        settings = symbol_settings.get(symbol) or {}
        algorithm = settings.get("algorithm") or default_algorithm
        if algorithm not in ALGORITHMS:
            algorithm = default_algorithm
        timeframe = settings.get("timeframe") or TIMEFRAME
        try:
            spent = check_symbol(
                client, symbol, float(weights.get(symbol, 0)), budget, algorithm, timeframe, max_loss_pct,
                available_cash, top_up_stop_mode,
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
    key_id = os.environ["APCA_API_KEY_ID"]
    secret_key = os.environ["APCA_API_SECRET_KEY"]
    trading_url = os.environ.get("APCA_API_BASE_URL", DEFAULT_TRADING_URL)
    data_url = os.environ.get("APCA_API_DATA_URL", DEFAULT_DATA_URL)
    return AlpacaClient(key_id, secret_key, trading_url, data_url)


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
