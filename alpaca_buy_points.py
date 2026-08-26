"""
Premium buy-point scanner for the user's watchlist portfolio.

Reads the "premium-buy-portfolio" Alpaca watchlist for symbols and
portfolio_config.json (committed to this repo by the Streamlit page - see
github_config.py) for the total budget and each symbol's weight. For
every watchlisted symbol without an already-open position, finds the
most recent still-valid demand zone (see demand_zones.py) and keeps a
resting GTC limit buy order at the zone's top - placing it if none
exists, updating it if the zone has moved, canceling it if the zone is
no longer valid. The actual fill happens on Alpaca's side whenever price
reaches the order, independent of how often this script runs - polling
here only keeps the order in sync with the current zone, it doesn't need
to catch the fill itself (unlike a market-order-on-poll approach, which
can only react at whatever moment it happens to check).

Run with --once (used by the GitHub Actions workflow, as an earlier step
than the trailing-stop pass, so a fresh fill gets its initial stop
placed in the same run).
"""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from alpaca_client import AlpacaClient, DEFAULT_TRADING_URL, DEFAULT_DATA_URL
from alpaca_trailing_stop import get_regular_hours_bars, TIMEFRAME, log
from demand_zones import find_buy_point

load_dotenv()

WATCHLIST_NAME = "premium-buy-portfolio"
CONFIG_PATH = "portfolio_config.json"

LOOKBACK_DAYS = int(os.environ.get("BUY_LOOKBACK_DAYS", "60"))
IMPULSE_PCT = float(os.environ.get("BUY_IMPULSE_PCT", "3")) / 100
IMPULSE_BARS = int(os.environ.get("BUY_IMPULSE_BARS", "3"))
MAX_TAPS = int(os.environ.get("BUY_MAX_TAPS", "2"))


def load_local_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {"budget": 0, "weights": {}}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def check_symbol(client: AlpacaClient, symbol: str, weight_pct: float, budget: float) -> None:
    existing_order = client.get_open_limit_buy_order(symbol)

    if client.get_position(symbol) is not None:
        if existing_order is not None:
            client.cancel_order(existing_order["id"])
            log(f"{symbol}: position already open, canceled stale buy-limit order.")
        return

    start = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    bars = get_regular_hours_bars(client, symbol, TIMEFRAME, start)
    if not bars:
        return

    zone = find_buy_point(bars, IMPULSE_PCT, IMPULSE_BARS, MAX_TAPS)

    if zone is not None:
        try:
            live_price = client.get_latest_trade_price(symbol)
        except Exception:
            live_price = None
        if live_price is None:
            live_price = bars[-1].c
        if zone.top >= live_price:
            # A zone at or above the current price isn't a pullback target -
            # a limit buy there would be marketable (fills near live_price
            # instead of the intended discount). Treat it the same as no
            # zone at all rather than ever placing an aggressive order.
            zone = None

    if zone is None:
        if existing_order is not None:
            client.cancel_order(existing_order["id"])
            log(f"{symbol}: no valid pullback zone below current price, canceled resting buy-limit order.")
        return

    dollar_amount = budget * (weight_pct / 100)
    if dollar_amount <= 0:
        return

    target_price = round(zone.top, 2)
    target_qty = round(dollar_amount / target_price, 4)
    if target_qty <= 0:
        return

    if existing_order is None:
        order = client.place_limit_entry(symbol, target_qty, "long", target_price)
        log(f"{symbol}: placed buy-limit at {target_price:.2f} (zone top), qty {target_qty} (${dollar_amount:.2f}). order {order['id']}.")
        return

    current_price = float(existing_order["limit_price"])
    current_qty = float(existing_order["qty"])
    if abs(current_price - target_price) < 0.01 and abs(current_qty - target_qty) < 0.0001:
        return  # already correctly placed

    # Alpaca rejects qty changes on fractional-qty orders via replace ("qty
    # must be an integer") - cancel and re-place instead, which works for
    # both fractional and whole-share quantities.
    client.cancel_order(existing_order["id"])
    order = client.place_limit_entry(symbol, target_qty, "long", target_price)
    log(f"{symbol}: updated buy-limit {current_price:.2f} -> {target_price:.2f} "
        f"(zone changed). new order {order['id']}.")


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

    for asset in watchlist["assets"]:
        symbol = asset["symbol"]
        check_symbol(client, symbol, float(weights.get(symbol, 0)), budget)


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
