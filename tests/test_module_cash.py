"""[2026-10-06] Modül nakit payı: pay canlı nakitten değil hesap değerinden
hesaplanır, modülün aldığı hisselerin maliyeti paydan düşülür."""

import unittest
from unittest import mock

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


class PremiumBuyPointCashTest(unittest.TestCase):
    """[2026-10-06] PBP'nin yüzde payı: bütçe = equity x yüzde, kalan = bütçe -
    PBP hisselerinin maliyeti; modüllerin harcanmamış payları da korunur."""

    def _available(self, client, config, module_pcts=(0.0, 0.0, 0.0), module_holdings=(set(), set(), set())):
        import alpaca_buy_points as abp
        with mock.patch.object(abp, "get_rs_cash_allocation_pct", return_value=module_pcts[0]), \
                mock.patch.object(abp, "get_orb_cash_allocation_pct", return_value=module_pcts[1]), \
                mock.patch.object(abp, "get_ha_cash_allocation_pct", return_value=module_pcts[2]), \
                mock.patch.object(abp, "load_rs_holdings", return_value={s: {} for s in module_holdings[0]}), \
                mock.patch.object(abp, "load_orb_holdings", return_value={s: {} for s in module_holdings[1]}), \
                mock.patch.object(abp, "load_ha_holdings", return_value={s: {} for s in module_holdings[2]}):
            return abp.compute_available_cash_for_buying(client, config)

    def test_budget_from_pct_and_legacy_fallback(self):
        import alpaca_buy_points as abp
        client = FakeClient(equity=100_000.0, cash=60_000.0)
        self.assertEqual(abp.resolve_pbp_budget(client, {"cash_allocation_pct": 50.0, "budget": 1}), 50_000.0)
        self.assertEqual(abp.resolve_pbp_budget(client, {"budget": 1234}), 1234.0)

    def test_pbp_spent_reduces_its_own_share(self):
        # 100k hesap; PBP %50 -> 50k bütçe, PBP hissesi AAA 30k; ORB %10 -> BBB 4k.
        client = FakeClient(
            positions=[_pos("AAA", 300, 100.0), _pos("BBB", 40, 100.0)], equity=100_000.0, cash=66_000.0,
        )
        config = {"cash_allocation_pct": 50.0, "weights": {"AAA": 100.0}}
        available = self._available(client, config, module_pcts=(0.0, 10.0, 0.0), module_holdings=(set(), {"BBB"}, set()))
        self.assertEqual(available, 20_000.0)  # 50k - 30k (nakit 66k - ORB'nin 6k'sı = 60k üst sınır)

    def test_real_cash_still_caps(self):
        client = FakeClient(positions=[], equity=100_000.0, cash=5_000.0)
        self.assertEqual(self._available(client, {"cash_allocation_pct": 50.0, "weights": {}}), 5_000.0)

    def test_without_pct_keeps_old_behaviour(self):
        client = FakeClient(positions=[_pos("AAA", 300, 100.0)], equity=100_000.0, cash=70_000.0)
        self.assertEqual(self._available(client, {"weights": {"AAA": 100.0}}), 70_000.0)


if __name__ == "__main__":
    unittest.main()
