"""
Premium buy-point scanner + auto-buy for the user's watchlist portfolio.

Reads the "premium-buy-portfolio" Alpaca watchlist for symbols and
portfolio_config.json (committed to this repo by the Streamlit page - see
github_config.py) for the total budget and each symbol's weight. For
every watchlisted symbol without an already-open position, finds the
most recent still-valid demand zone (see demand_zones.py) and buys once
price has pulled back into it (reached the zone's top).

Run with --once (used by the GitHub Actions workflow, as an earlier step
than the trailing-stop pass, so a fresh buy gets its initial stop placed
in the same run).
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
    if client.get_position(symbol) is not None:
        return  # already holding it - the trailing-stop step manages it from here

    start = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    bars = get_regular_hours_bars(client, symbol, TIMEFRAME, start)
    if not bars:
        return

    zone = find_buy_point(bars, IMPULSE_PCT, IMPULSE_BARS, MAX_TAPS)
    if zone is None:
        return

    try:
        last_price = client.get_latest_trade_price(symbol)
    except Exception:
        last_price = None
    if last_price is None:
        last_price = bars[-1].c  # fall back to the last completed bar if the live quote fails

    if last_price > zone.top:
        return  # hasn't pulled back into the zone yet

    dollar_amount = budget * (weight_pct / 100)
    if dollar_amount <= 0:
        return

    order = client.place_market_entry_notional(symbol, dollar_amount, "long")
    log(f"{symbol}: buy point hit (zone top {zone.top:.2f}, price {last_price:.2f}) - "
        f"bought ${dollar_amount:.2f} worth. order {order['id']}.")


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
