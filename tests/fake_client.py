"""Alpaca REST çağrılarını bellekte taklit eden minimal istemci - açılış
kalkanı / giriş zamanlaması testleri için. Sadece testlerin kullandığı
metotlar var."""

import itertools

_ids = itertools.count(1)


class FakeClient:
    def __init__(self, positions=None, orders=None, last_price=100.0, equity=100_000.0, cash=100_000.0):
        self.positions = {p["symbol"]: p for p in (positions or [])}
        self.orders = list(orders or [])
        self.last_price = last_price
        self.equity = equity
        self.cash = cash
        self.calls = []

    # --- sorgular
    def get_position(self, symbol):
        return self.positions.get(symbol)

    def get_all_positions(self):
        return list(self.positions.values())

    def get_open_orders(self):
        return [o for o in self.orders if o["status"] in ("new", "accepted", "held")]

    def get_open_stop_order(self, symbol):
        for o in self.get_open_orders():
            if o["symbol"] == symbol and o["type"] in ("stop", "stop_limit"):
                return o
        return None

    def get_open_limit_buy_order(self, symbol):
        for o in self.get_open_orders():
            if o["symbol"] == symbol and o["type"] == "limit" and o["side"] == "buy":
                return o
        return None

    def has_open_exit_order(self, symbol, side):
        return any(o["symbol"] == symbol and o["side"] == "sell" for o in self.get_open_orders())

    def get_latest_trade_price(self, symbol):
        return self.last_price

    def get_account(self):
        return {"equity": str(self.equity), "cash": str(self.cash)}

    def get_stop_order_history(self, symbol, limit=50):
        return [o for o in self.orders if o["symbol"] == symbol and o["type"] in ("stop", "stop_limit")]

    # --- emirler
    def _new(self, **kw):
        order = {"id": f"o{next(_ids)}", "status": "new", "created_at": "2026-09-28T13:00:00Z", **kw}
        self.orders.append(order)
        return order

    def place_stop_order(self, symbol, qty, side, stop_price, client_order_id=None):
        self.calls.append(("place_stop", symbol, round(stop_price, 2), client_order_id))
        return self._new(symbol=symbol, qty=str(qty), side="sell", type="stop",
                         stop_price=f"{stop_price:.2f}", client_order_id=client_order_id)

    def replace_stop_price(self, order_id, stop_price, client_order_id=None, allow_untagged_fallback=True):
        old = next(o for o in self.orders if o["id"] == order_id)
        old["status"] = "replaced"
        self.calls.append(("replace_stop", old["symbol"], round(stop_price, 2), client_order_id))
        return self._new(symbol=old["symbol"], qty=old["qty"], side=old["side"], type=old["type"],
                         stop_price=f"{stop_price:.2f}", client_order_id=client_order_id)

    def replace_stop_qty(self, order_id, qty):
        old = next(o for o in self.orders if o["id"] == order_id)
        old["status"] = "replaced"
        return self._new(**{**{k: v for k, v in old.items() if k not in ("id", "status")}, "qty": str(qty),
                            "client_order_id": None})

    def cancel_order(self, order_id):
        order = next(o for o in self.orders if o["id"] == order_id)
        order["status"] = "canceled"
        self.calls.append(("cancel", order["symbol"], order["type"]))

    def place_market_exit(self, symbol, qty, client_order_id=None):
        self.calls.append(("market_exit", symbol, qty, client_order_id))
        return self._new(symbol=symbol, qty=str(qty), side="sell", type="market", client_order_id=client_order_id)

    def place_extended_hours_limit(self, symbol, qty, side, limit_price):
        self.calls.append(("ext_limit", symbol, round(limit_price, 2)))
        return self._new(symbol=symbol, qty=str(qty), side="sell", type="limit",
                         limit_price=f"{limit_price:.2f}", extended_hours=True)
