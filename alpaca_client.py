"""Thin Alpaca REST client shared by alpaca_trailing_stop.py (the scheduled
job) and alpaca_dashboard.py (the Streamlit page). No Streamlit dependency
here so the trailing-stop script can run standalone under GitHub Actions.
"""

import sys
import time

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

    def get_raw_bars(self, symbol: str, timeframe: str, start_iso: str, feed: str = "iex") -> list[dict]:
        r = requests.get(
            f"{self.data_url}/stocks/{symbol}/bars",
            headers=self.headers,
            params={"timeframe": timeframe, "start": start_iso, "limit": 1000, "feed": feed, "adjustment": "raw"},
        )
        r.raise_for_status()
        return r.json().get("bars") or []
