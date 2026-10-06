"""[2026-10-06] Modül nakit payı: pay canlı nakitten değil hesap değerinden
hesaplanır, modülün aldığı hisselerin maliyeti paydan düşülür."""

import unittest

import module_cash
from tests.fake_client import FakeClient


def _pos(symbol, qty, price):
    return {"symbol": symbol, "qty": str(qty), "avg_entry_price": str(price), "cost_basis": str(qty * price)}


class ModuleAvailableCashTest(unittest.TestCase):
    def test_fresh_account(self):
        client = FakeClient(equity=100_000.0, cash=100_000.0)
        budget, available = module_cash.module_available_cash(client, 10.0, set(), "hai")
        self.assertEqual((budget, available), (10_000.0, 10_000.0))

    def test_other_buys_do_not_shrink_module_share(self):
        # Nakit 100k -> 60k'ya düştü: 36k başka sistemin (PBP), 4k modülün alımı.
        client = FakeClient(
            positions=[_pos("PBP", 360, 100.0), _pos("AAA", 40, 100.0)], equity=100_000.0, cash=60_000.0,
        )
        budget, available = module_cash.module_available_cash(client, 10.0, {"AAA"}, "hai")
        self.assertEqual(budget, 10_000.0)
        self.assertEqual(available, 6_000.0)  # eskiden 60k x %10 = 6k bütçe, 2k kalan

    def test_closed_holdings_free_the_share(self):
        client = FakeClient(positions=[], equity=100_000.0, cash=100_000.0)
        _, available = module_cash.module_available_cash(client, 10.0, {"AAA"}, "hai")
        self.assertEqual(available, 10_000.0)

    def test_pending_orders_and_real_cash_cap(self):
        orders = [{"symbol": "BBB", "qty": "10", "limit_price": "100", "type": "limit", "side": "buy",
                   "status": "new", "client_order_id": "orb-buy-BBB-1"}]
        client = FakeClient(orders=orders, equity=100_000.0, cash=1_500.0)
        _, available = module_cash.module_available_cash(client, 10.0, set(), "orb")
        self.assertEqual(available, 1_500.0)  # 10k - 1k emir = 9k, ama gerçek nakit 1.5k


class UnspentReserveTest(unittest.TestCase):
    def test_only_unspent_part_is_reserved(self):
        account = {"equity": "100000", "cash": "60000"}
        positions = [_pos("AAA", 40, 100.0)]
        reserve = module_cash.unspent_module_reserve(account, positions, [(10.0, {"AAA"}), (0.0, {"ZZZ"})])
        self.assertEqual(reserve, 6_000.0)

    def test_over_spent_module_reserves_nothing(self):
        account = {"equity": "100000", "cash": "60000"}
        positions = [_pos("AAA", 120, 100.0)]
        self.assertEqual(module_cash.unspent_module_reserve(account, positions, [(10.0, {"AAA"})]), 0.0)


if __name__ == "__main__":
    unittest.main()
