"""
Structure-based trailing-stop bot for Alpaca paper trading.

Manages every open position in the account. Each pass, per position:

  1. Makes sure a stop order is resting (places an initial fixed-% stop
     if none exists yet - the video trails an existing trade, it doesn't
     define entry/initial-risk sizing). In practice this is now a fallback:
     alpaca_buy_points.py submits entries as bracket orders with this same
     stop already attached, so it activates the instant the entry fills
     instead of waiting on this script's next (GitHub Actions-scheduled,
     and not always promptly delivered) run. This still covers a position
     that ends up with no stop some other way (opened outside this system).
  2. Structure is evaluated only from bars since we started managing this
     position (its first stop order's timestamp), not an arbitrary fixed
     lookback - otherwise "reference" swing points from market phases
     that happened before entry can permanently block trailing.
  3. Breakeven floor: once price has moved far enough in our favor, the
     stop is guaranteed to be at least at entry, even if structure hasn't
     validated a trail yet (this is the video's simpler first step).
  4. Structure-based trail: finds the latest break-of-structure-validated
     swing point (see structure.py - including its stale-reference reset,
     so a reference that goes unbroken for too long doesn't freeze
     trailing forever) and, gated by a higher-timeframe (daily EMA) trend
     filter, trails the stop just past it using an ATR-scaled buffer.
  5. Of whatever candidates apply, only the one that most tightens the
     stop (and stays on the correct side of the current price) is sent -
     never loosens, never sent past current price.

Run with --once for a single pass (used by the GitHub Actions workflow,
which handles the scheduling). Without --once it loops locally, sleeping
between passes and until the market reopens.
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from alpaca_client import AlpacaClient, DEFAULT_TRADING_URL, DEFAULT_DATA_URL
from indicators import atr, ema
from structure import Bar, validated_trailing_level

load_dotenv()

TIMEFRAME = os.environ.get("TRADE_TIMEFRAME", "30Min")
SWING_ORDER = int(os.environ.get("TRADE_SWING_ORDER", "2"))
INITIAL_STOP_PCT = float(os.environ.get("TRADE_INITIAL_STOP_PCT", "1.5")) / 100
POLL_SECONDS = int(os.environ.get("TRADE_POLL_SECONDS", "60"))

LOOKBACK_DAYS = int(os.environ.get("TRADE_LOOKBACK_DAYS", "15"))
ATR_PERIOD = int(os.environ.get("TRADE_ATR_PERIOD", "14"))
ATR_MULTIPLIER = float(os.environ.get("TRADE_ATR_MULTIPLIER", "0.25"))
BREAKEVEN_TRIGGER_PCT = float(os.environ.get("TRADE_BREAKEVEN_TRIGGER_PCT", "1.0")) / 100
STALE_REFERENCE_DAYS = float(os.environ.get("TRADE_STALE_REFERENCE_DAYS", "10"))
TREND_EMA_PERIOD = int(os.environ.get("TRADE_TREND_EMA_PERIOD", "50"))

FALLBACK_BUFFER_PCT = 0.001  # only used if ATR can't be computed yet (too few bars)

ET = ZoneInfo("America/New_York")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


_TIMEFRAME_UNITS = {"Min": timedelta(minutes=1), "Hour": timedelta(hours=1), "Day": timedelta(days=1)}


def _timeframe_duration(timeframe: str) -> timedelta:
    match = re.match(r"^(\d+)(Min|Hour|Day)$", timeframe)
    if not match:
        raise ValueError(f"Unrecognized timeframe: {timeframe!r}")
    n, unit = match.groups()
    return int(n) * _TIMEFRAME_UNITS[unit]


def get_regular_hours_bars(
    client: AlpacaClient, symbol: str, timeframe: str, start: datetime, exclude_forming: bool = False,
) -> list[Bar]:
    raw_bars = client.get_raw_bars(symbol, timeframe, start.isoformat())

    bars = []
    for b in raw_bars:
        ts = _parse_iso(b["t"]).astimezone(ET)
        if ts.weekday() >= 5:
            continue
        if not (9, 30) <= (ts.hour, ts.minute) < (16, 0):
            continue
        bars.append(Bar(t=b["t"], o=b["o"], h=b["h"], l=b["l"], c=b["c"], v=b["v"]))

    if exclude_forming and bars:
        last_start = _parse_iso(bars[-1].t)
        if last_start + _timeframe_duration(timeframe) > datetime.now(timezone.utc):
            bars.pop()  # still-forming bar - buy-point signals shouldn't chase intra-bar noise

    return bars


def get_management_start(client: AlpacaClient, symbol: str, lookback_days: int) -> datetime:
    """The earlier of `lookback_days` ago and when we first started
    managing this position's stop - whichever is more recent wins, so
    structure from before we ever held the trade can't anchor the
    reference point."""
    default_start = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    history = client.get_stop_order_history(symbol, limit=200)
    if not history:
        return default_start
    earliest = min(_parse_iso(o["created_at"]) for o in history)
    return max(default_start, earliest)


def check_trend_filter(client: AlpacaClient, symbol: str, side: str) -> bool:
    """True if the higher-timeframe (daily) trend still supports trailing
    further in this direction. Defaults to True (don't block) when there
    isn't enough daily history yet, or the filter is disabled (period<=0)."""
    if TREND_EMA_PERIOD <= 0:
        return True

    start = datetime.now(timezone.utc) - timedelta(days=TREND_EMA_PERIOD * 4)
    raw_bars = client.get_raw_bars(symbol, "1Day", start.isoformat())
    closes = [b["c"] for b in raw_bars]
    trend_ema = ema(closes, TREND_EMA_PERIOD)
    if trend_ema is None:
        return True

    last_close = closes[-1]
    return last_close > trend_ema if side == "long" else last_close < trend_ema


def seconds_until_open(clock: dict) -> float:
    next_open = _parse_iso(clock["next_open"])
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
        log(f"{symbol}: no resting stop found (not opened as a bracket order here), "
            f"placed fallback initial stop at {initial_stop:.2f} (entry {entry_price:.2f}).")

    current_stop_price = float(stop_order["stop_price"])
    stop_order_id = stop_order["id"]

    management_start = get_management_start(client, symbol, LOOKBACK_DAYS)
    bars = get_regular_hours_bars(client, symbol, TIMEFRAME, management_start)
    if not bars:
        log(f"{symbol}: no regular-hours bars available, skipping.")
        return

    last_price = bars[-1].c
    candidates = []  # (price, reason) - the caller picks whichever tightens the stop most

    if side == "long":
        gain_pct = (last_price - entry_price) / entry_price
    else:
        gain_pct = (entry_price - last_price) / entry_price

    if gain_pct >= BREAKEVEN_TRIGGER_PCT:
        if side == "long" and entry_price > current_stop_price and entry_price < last_price:
            candidates.append((entry_price, "breakeven"))
        elif side == "short" and entry_price < current_stop_price and entry_price > last_price:
            candidates.append((entry_price, "breakeven"))

    if check_trend_filter(client, symbol, side):
        pivot = validated_trailing_level(bars, side, SWING_ORDER, STALE_REFERENCE_DAYS)
        if pivot is not None:
            atr_value = atr(bars, ATR_PERIOD)
            buffer_amount = atr_value * ATR_MULTIPLIER if atr_value is not None else pivot.price * FALLBACK_BUFFER_PCT

            if side == "long":
                candidate = pivot.price - buffer_amount
                if candidate < last_price:
                    candidates.append((candidate, f"structure@{pivot.price:.2f}"))
            else:
                candidate = pivot.price + buffer_amount
                if candidate > last_price:
                    candidates.append((candidate, f"structure@{pivot.price:.2f}"))

    if not candidates:
        return

    if side == "long":
        best_price, reason = max(candidates, key=lambda c: c[0])
        improves = best_price > current_stop_price
    else:
        best_price, reason = min(candidates, key=lambda c: c[0])
        improves = best_price < current_stop_price

    if not improves:
        return

    try:
        client.replace_stop_price(stop_order_id, best_price)
        log(f"{symbol}: trailed stop {current_stop_price:.2f} -> {best_price:.2f} ({reason}).")
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
