"""
Premium buy-point scanner for the user's watchlist portfolio.

Reads the "premium-buy-portfolio" Alpaca watchlist for symbols and
portfolio_config.json (committed to this repo by the Streamlit page - see
github_config.py) for the total budget, each symbol's weight, and which
buy-point algorithm (see buy_algorithms.py) is active - the same single
choice applies to every symbol. For every watchlisted symbol without an
already-open position, runs that algorithm and keeps a resting GTC limit
buy order at its price - placing it if none exists, updating it if the
signal has moved, canceling it if there's no longer a valid signal. The
actual fill happens on Alpaca's side whenever price reaches the order,
independent of how often this script runs - polling here only keeps the
order in sync with the current signal, it doesn't need to catch the fill
itself (unlike a market-order-on-poll approach, which can only react at
whatever moment it happens to check).

Every entry is submitted as a bracket order with a stop-loss leg at
INITIAL_STOP_PCT below the limit price (see alpaca_client.place_limit_entry),
so the protective stop exists on Alpaca's side the instant the entry fills -
it doesn't wait for alpaca_trailing_stop.py's next scheduled run, which
GitHub Actions can delay well past its nominal interval. That script's own
initial-stop placement is now just a fallback for a position that somehow
has none (e.g. opened outside this system); its structure-based trailing
still runs on its own schedule to tighten the stop over time.

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


def load_local_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {"budget": 0, "weights": {}}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def check_symbol(client: AlpacaClient, symbol: str, weight_pct: float, budget: float, algorithm: str) -> None:
    existing_order = client.get_open_limit_buy_order(symbol)

    if client.get_position(symbol) is not None:
        if existing_order is not None:
            client.cancel_order(existing_order["id"])
            log(f"{symbol}: position already open, canceled stale buy-limit order.")
        return

    start = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    bars = get_regular_hours_bars(client, symbol, TIMEFRAME, start, exclude_forming=True)
    if not bars:
        return

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
            log(f"{symbol}: no valid buy signal ({algorithm}), canceled resting buy-limit order.")
        return

    dollar_amount = budget * (weight_pct / 100)
    if dollar_amount <= 0:
        return

    target_price = signal.price
    target_qty = math.floor(dollar_amount / target_price)
    if target_qty <= 0:
        return

    # Tags the order with which algorithm produced it (parsed back out in
    # alpaca_dashboard.py's history table) - "algo-<id>-<symbol>-<epoch>",
    # dash-separated since algorithm ids use underscores.
    client_order_id = f"algo-{algorithm}-{symbol}-{int(datetime.now(timezone.utc).timestamp())}"
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
        return

    current_price = float(existing_order["limit_price"])
    current_qty = float(existing_order["qty"])
    if abs(current_price - target_price) < 0.01 and abs(current_qty - target_qty) < 0.0001:
        return  # already correctly placed

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


def run_once(client: AlpacaClient) -> None:
    clock = client.get_clock()
    if not clock["is_open"]:
        log("Market closed, skipping buy-point scan.")
        return

    watchlist = client.get_watchlist_by_name(WATCHLIST_NAME)
    if watchlist is None or not watchlist.get("assets"):
        log("No premium-buy-portfolio watchlist, or it's empty.")
        return

    config = load_local_config()
    budget = float(config.get("budget") or 0)
    weights = config.get("weights") or {}
    algorithm = config.get("algorithm") or DEFAULT_ALGORITHM
    if algorithm not in ALGORITHMS:
        algorithm = DEFAULT_ALGORITHM

    for asset in watchlist["assets"]:
        symbol = asset["symbol"]
        try:
            check_symbol(client, symbol, float(weights.get(symbol, 0)), budget, algorithm)
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
