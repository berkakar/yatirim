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
weights the same), it places a plain GTC limit buy (no bracket stop) for
the shortfall once the algorithm has a valid signal again - same
price/timing discipline as a fresh entry, just sized to the gap instead of
the full budget. The top-up has no stop-loss leg of its own; the position's
single resting stop already exists, and alpaca_trailing_stop.manage_position
resizes it to the position's current total qty on every pass so it keeps
covering the whole position after the top-up fills.

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
"""

import argparse
import json
import math
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from alpaca_client import AlpacaClient, DEFAULT_TRADING_URL, DEFAULT_DATA_URL
from alpaca_trailing_stop import INITIAL_STOP_PCT, get_regular_hours_bars, TIMEFRAME, log
from buy_algorithms import ALGORITHMS, DEFAULT_ALGORITHM, reject_if_marketable

load_dotenv()

# Tek kullanıcı (berkakar) varsayılıyor - çoklu kullanıcı desteği bu GitHub Action'a
# henüz eklenmedi (Streamlit tarafındaki per-user değişikliklerle tutarlı kalması
# için sadece isimler güncellendi).
WATCHLIST_NAME = "premium-buy-portfolio-berkakar"
CONFIG_PATH = "portfolio_config_berkakar.json"

LOOKBACK_DAYS = int(os.environ.get("BUY_LOOKBACK_DAYS", "60"))
DAILY_LOOKBACK_DAYS = int(os.environ.get("BUY_DAILY_LOOKBACK_DAYS", "400"))
STOP_LOSS_LOOKBACK_DAYS = int(os.environ.get("STOP_LOSS_LOOKBACK_DAYS", "90"))


def load_local_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {"budget": 0, "weights": {}}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def check_symbol(
    client: AlpacaClient, symbol: str, weight_pct: float, budget: float, algorithm: str, timeframe: str,
    max_loss_pct: float | None = None, available_cash: float | None = None,
) -> float:
    """Pozisyon yoksa: sinyale göre yeni bir giriş (bracket buy-limit) açar
    veya bekleyen girişi günceller - aşağıdaki asıl akış budur.

    Pozisyon zaten açıksa: hedef bütçe (budget * weight_pct), o sembole
    şu ana kadar yatırılmış tutarı (adet * ortalama giriş) aştığında ve
    algoritmanın hâlâ bir al sinyali olduğunda, aradaki farkı düz bir
    limit emriyle (bracket stop'suz) tamamlayan bir "ilave alım" dalı
    çalıştırır - böylece kullanıcı nakit/bütçe artırıp ağırlığı sabit
    bıraktığında sistem o hisseye otomatik olarak ek alım yapabilir.
    İlave alımın kendi bracket stop'u yoktur: pozisyonun tek resting
    stop'u zaten var, alpaca_trailing_stop.manage_position bunu her
    pass'te pozisyonun güncel toplam adedine göre yeniden boyutlandırır
    (bkz. o fonksiyondaki qty eşitleme adımı). "Zarar kes" (max_loss_pct)
    sadece YENİ girişleri engeller - zaten açık bir pozisyona ilave alımı
    değil.

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
        order = client.place_limit_entry(symbol, top_up_qty, "long", target_price, client_order_id=client_order_id)
        spent = top_up_qty * target_price
        log(f"{symbol}: bütçe arttı (yatırılan ${invested:.2f} -> hedef ${dollar_amount:.2f}), ilave al "
            f"sinyaliyle ({signal.reason}) {top_up_qty} adet @ {target_price:.2f} limit emri verildi. "
            f"order {order['id']}.")
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
    if abs(current_price - target_price) < 0.01 and abs(current_qty - target_qty) < 0.0001:
        return 0.0  # already correctly placed - order's notional was already counted at pass start

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
                available_cash,
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
    args = parser.parse_args()

    client = build_client()
    run_once(client)
