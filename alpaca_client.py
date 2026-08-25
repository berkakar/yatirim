"""Thin Alpaca REST client shared by alpaca_trailing_stop.py (the scheduled
job) and alpaca_dashboard.py (the Streamlit page). No Streamlit dependency
here so the trailing-stop script can run standalone under GitHub Actions.
"""

import sys
import time
from datetime import datetime, timedelta, timezone

if sys.platform == "win32":
    # Some Windows setups (corporate AV/security software) inject a root CA
    # into the OS trust store that certifi's bundled CA list doesn't know
    # about, breaking TLS verification. Using the OS trust store directly
    # fixes it. Harmless no-op on Linux (GitHub Actions, Streamlit Cloud).
    import truststore
    truststore.inject_into_ssl()

import requests

DEFAULT_TRADING_URL = "https://paper-api.alpaca.markets/v2"
DEFAULT_DATA_URL = "https://data.alpaca.markets/v2"


class AlpacaClient:
    def __init__(self, key_id: str, secret_key: str,
                 trading_url: str = DEFAULT_TRADING_URL, data_url: str = DEFAULT_DATA_URL):
        self.headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
        self.trading_url = trading_url
        self.data_url = data_url

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        return requests.get(f"{self.trading_url}{path}", headers=self.headers, params=params)

    def _post(self, path: str, json: dict) -> dict:
        r = requests.post(f"{self.trading_url}{path}", headers=self.headers, json=json)
        r.raise_for_status()
        return r.json()

    def _patch(self, path: str, json: dict) -> dict:
        r = requests.patch(f"{self.trading_url}{path}", headers=self.headers, json=json)
        r.raise_for_status()
        return r.json()

    def get_clock(self) -> dict:
        r = self._get("/clock")
        r.raise_for_status()
        return r.json()

    def get_position(self, symbol: str) -> dict | None:
        r = self._get(f"/positions/{symbol}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def get_all_positions(self) -> list[dict]:
        r = self._get("/positions")
        r.raise_for_status()
        return r.json()

    def get_open_stop_order(self, symbol: str) -> dict | None:
        r = self._get("/orders", params={"status": "open", "symbols": symbol})
        r.raise_for_status()
        for order in r.json():
            if order["type"] in ("stop", "stop_limit"):
                return order
        return None

    def place_market_entry(self, symbol: str, qty: float, side: str) -> dict:
        return self._post("/orders", {
            "symbol": symbol,
            "qty": qty,
            "side": "buy" if side == "long" else "sell",
            "type": "market",
            "time_in_force": "day",
        })

    def place_market_entry_notional(self, symbol: str, notional: float, side: str) -> dict:
        """Buy/sell a dollar amount rather than a share count - Alpaca
        fills at the real execution price, no stale-bar-close guessing."""
        return self._post("/orders", {
            "symbol": symbol,
            "notional": f"{notional:.2f}",
            "side": "buy" if side == "long" else "sell",
            "type": "market",
            "time_in_force": "day",
        })

    def place_stop_order(self, symbol: str, qty: float, side: str, stop_price: float) -> dict:
        return self._post("/orders", {
            "symbol": symbol,
            "qty": qty,
            "side": "sell" if side == "long" else "buy",
            "type": "stop",
            "stop_price": f"{stop_price:.2f}",
            "time_in_force": "gtc",
        })

    def replace_stop_price(self, order_id: str, stop_price: float) -> dict:
        return self._patch(f"/orders/{order_id}", {"stop_price": f"{stop_price:.2f}"})

    def get_stop_order_history(self, symbol: str, limit: int = 50) -> list[dict]:
        """Every stop order ever placed for this symbol (initial + each
        trail, since replacing a stop creates a new order and marks the old
        one 'replaced') - Alpaca already keeps this history, no separate
        logging needed."""
        r = self._get("/orders", params={
            "status": "all", "symbols": symbol, "direction": "desc", "limit": limit,
        })
        r.raise_for_status()
        return [o for o in r.json() if o["type"] in ("stop", "stop_limit")]

    def get_recent_orders(self, days: int = 30, limit: int = 500) -> list[dict]:
        """Every order (any symbol, any status - open, filled, replaced,
        canceled) submitted in the last `days` days, across the whole
        account. Unlike get_stop_order_history this isn't scoped to
        currently-open positions, so closed-out trades still show up."""
        after = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        r = self._get("/orders", params={
            "status": "all", "after": after, "direction": "desc", "limit": limit,
        })
        r.raise_for_status()
        return r.json()

    def wait_for_fill(self, order_id: str, timeout: float = 30) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = self._get(f"/orders/{order_id}")
            r.raise_for_status()
            order = r.json()
            if order["status"] == "filled":
                return order
            time.sleep(1)
        raise TimeoutError(f"Order {order_id} did not fill within {timeout}s")

    def get_watchlist(self, watchlist_id: str) -> dict:
        r = self._get(f"/watchlists/{watchlist_id}")
        r.raise_for_status()
        return r.json()

    def get_watchlist_by_name(self, name: str) -> dict | None:
        r = self._get("/watchlists")
        r.raise_for_status()
        for wl in r.json():
            if wl["name"] == name:
                return self.get_watchlist(wl["id"])  # the list endpoint omits "assets"
        return None

    def get_or_create_watchlist(self, name: str) -> dict:
        wl = self.get_watchlist_by_name(name)
        if wl is not None:
            return wl
        return self._post("/watchlists", {"name": name, "symbols": []})

    def set_watchlist_symbols(self, watchlist_id: str, symbols: list[str]) -> dict:
        r = requests.put(
            f"{self.trading_url}/watchlists/{watchlist_id}",
            headers=self.headers,
            json={"symbols": symbols},
        )
        r.raise_for_status()
        return r.json()

    def get_raw_bars(self, symbol: str, timeframe: str, start_iso: str, feed: str = "iex") -> list[dict]:
        r = requests.get(
            f"{self.data_url}/stocks/{symbol}/bars",
            headers=self.headers,
            params={"timeframe": timeframe, "start": start_iso, "limit": 1000, "feed": feed, "adjustment": "raw"},
        )
        r.raise_for_status()
        return r.json().get("bars") or []
