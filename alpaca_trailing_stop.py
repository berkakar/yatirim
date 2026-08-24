"""
Structure-based trailing-stop bot for Alpaca paper trading.

Manages every open position in the account: on each pass, it pulls the
account's current positions, makes sure each has a resting stop order
(placing an initial fixed-% stop if one doesn't exist yet - the video
trails stops on an existing trade, it doesn't define entry/initial-risk
sizing), then finds the latest break-of-structure-validated swing point
per symbol (see structure.py) and - only if it tightens the stop -
replaces the resting stop order with it. Never loosens a stop.

Run with --once for a single pass (used by the GitHub Actions workflow,
which handles the scheduling). Without --once it loops locally, sleeping
between passes and until the market reopens.
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from alpaca_client import AlpacaClient, DEFAULT_TRADING_URL, DEFAULT_DATA_URL
from structure import Bar, validated_trailing_level

load_dotenv()

TIMEFRAME = os.environ.get("TRADE_TIMEFRAME", "30Min")
SWING_ORDER = int(os.environ.get("TRADE_SWING_ORDER", "2"))
STOP_BUFFER_PCT = float(os.environ.get("TRADE_STOP_BUFFER_PCT", "0.1")) / 100
INITIAL_STOP_PCT = float(os.environ.get("TRADE_INITIAL_STOP_PCT", "1.5")) / 100
POLL_SECONDS = int(os.environ.get("TRADE_POLL_SECONDS", "60"))

ET = ZoneInfo("America/New_York")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def get_regular_hours_bars(client: AlpacaClient, symbol: str, timeframe: str, lookback_days: int = 15) -> list[Bar]:
    start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    raw_bars = client.get_raw_bars(symbol, timeframe, start)

    bars = []
    for b in raw_bars:
        ts = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
        if ts.weekday() >= 5:
            continue
        if not (9, 30) <= (ts.hour, ts.minute) < (16, 0):
            continue
        bars.append(Bar(t=b["t"], o=b["o"], h=b["h"], l=b["l"], c=b["c"], v=b["v"]))
    return bars


def seconds_until_open(clock: dict) -> float:
    next_open = datetime.fromisoformat(clock["next_open"].replace("Z", "+00:00"))
    return max(0.0, (next_open - datetime.now(timezone.utc)).total_seconds())


def manage_position(client: AlpacaClient, pos: dict) -> None:
    symbol = pos["symbol"]
    signed_qty = float(pos["qty"])
    qty = abs(signed_qty)
    side = "long" if signed_qty > 0 else "short"
    entry_price = float(pos["avg_entry_price"])

    stop_order = client.get_open_stop_order(symbol)
    if stop_order is None:
        if side == "long":
            initial_stop = entry_price * (1 - INITIAL_STOP_PCT)
        else:
            initial_stop = entry_price * (1 + INITIAL_STOP_PCT)
        stop_order = client.place_stop_order(symbol, qty, side, initial_stop)
        log(f"{symbol}: no resting stop found, placed initial stop at {initial_stop:.2f} (entry {entry_price:.2f}).")

    current_stop_price = float(stop_order["stop_price"])
    stop_order_id = stop_order["id"]

    bars = get_regular_hours_bars(client, symbol, TIMEFRAME)
    if not bars:
        log(f"{symbol}: no regular-hours bars available, skipping.")
        return

    pivot = validated_trailing_level(bars, side, SWING_ORDER)
    if pivot is None:
        return

    last_price = bars[-1].c
    if side == "long":
        candidate = pivot.price * (1 - STOP_BUFFER_PCT)
        improves = candidate > current_stop_price and candidate < last_price
    else:
        candidate = pivot.price * (1 + STOP_BUFFER_PCT)
        improves = candidate < current_stop_price and candidate > last_price

    if not improves:
        return

    try:
        client.replace_stop_price(stop_order_id, candidate)
        log(f"{symbol}: trailed stop {current_stop_price:.2f} -> {candidate:.2f} "
            f"(validated structure at {pivot.price:.2f}, bar {pivot.t}).")
    except requests.HTTPError as e:
        log(f"{symbol}: failed to replace stop order: {e}")


def run_once(client: AlpacaClient) -> None:
    clock = client.get_clock()
    if not clock["is_open"]:
        log("Market closed, nothing to do.")
        return

    positions = [p for p in client.get_all_positions() if p.get("asset_class") == "us_equity"]
    if not positions:
        log("No open equity positions.")
        return

    for pos in positions:
        manage_position(client, pos)


def run_loop(client: AlpacaClient) -> None:
    log(f"Starting trailing-stop loop (timeframe={TIMEFRAME}).")
    while True:
        clock = client.get_clock()
        if not clock["is_open"]:
            wait_s = min(seconds_until_open(clock), 900)
            log(f"Market closed. Sleeping {int(wait_s)}s.")
            time.sleep(wait_s)
            continue

        run_once(client)
        time.sleep(POLL_SECONDS)


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
    try:
        if args.once:
            run_once(client)
        else:
            run_loop(client)
    except KeyboardInterrupt:
        sys.exit(0)
