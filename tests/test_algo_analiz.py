import unittest
from datetime import datetime, timezone

from algo_analiz import (
    OTHER_ALGOS_LABEL, module_cash_rows, apply_stop_scenario, count_by, format_stop_moves, open_stop_levels, portfolio_snapshot, resolve_stop_algorithm_id, scenario_totals,
)
from trade_journal import walk_fills
from trade_journal_analysis import STATUS_CLOSED, STATUS_OPEN, Record, closed_records, group_summary, open_records


def _open(symbol, qty, entry, last, algorithm="PBP: ema_cross", realized=0.0):
    return Record(symbol=symbol, algorithm=algorithm, timeframe="1Day", status=STATUS_OPEN, realized=realized,
                  unrealized=(last - entry) * qty, cost_basis=entry * qty, entry_time=None, qty=qty,
                  entry_price=entry, last_price=last)


def _closed(symbol, pnl, algorithm="ORB", r=None, reason="Stop: İlk stop"):
    t = datetime(2026, 9, 1, 14, tzinfo=timezone.utc)
    return Record(symbol=symbol, algorithm=algorithm, timeframe=None, status=STATUS_CLOSED, realized=pnl,
                  unrealized=0.0, cost_basis=1000.0, entry_time=t, exit_time=t, r_multiple=r, exit_reason=reason,
                  entry_session="Seans içi", exit_session="Açılış (ilk 15 dk)")


class StopLevelsTest(unittest.TestCase):
    def test_only_open_sell_stops_and_shield_real_level(self):
        orders = [
            {"symbol": "AAPL", "type": "stop", "side": "sell", "qty": "10", "filled_qty": "0", "stop_price": "190",
             "client_order_id": "stop-breakeven-AAPL-1"},
            # Açılış kalkanı: borsadaki stop 150 ama gerçek seviye 195.00
            {"symbol": "MSFT", "type": "stop", "side": "sell", "qty": "5", "filled_qty": "0", "stop_price": "150",
             "client_order_id": "shield-MSFT-19500-1"},
            {"symbol": "NVDA", "type": "limit", "side": "sell", "qty": "5", "limit_price": "99"},
            {"symbol": "TSLA", "type": "stop", "side": "buy", "qty": "5", "stop_price": "99"},
            {"symbol": "AMD", "type": "stop_limit", "side": "sell", "qty": "4", "filled_qty": "1", "stop_price": "80"},
        ]
        levels = open_stop_levels(orders)
        self.assertEqual(levels["AAPL"], [(190.0, 10.0)])
        self.assertEqual(levels["MSFT"], [(195.0, 5.0)])
        self.assertEqual(levels["AMD"], [(80.0, 3.0)])
        self.assertNotIn("NVDA", levels)
        self.assertNotIn("TSLA", levels)


class StopScenarioTest(unittest.TestCase):
    def test_full_partial_and_missing_stop(self):
        full = _open("AAPL", 10, 200, 220)       # stop 210 -> +100 (şimdi +200)
        partial = _open("MSFT", 10, 100, 120)    # 4 adet 90'da stop, 6 adet anlık -> -40 + 120 = +80
        none = _open("NVDA", 5, 50, 45)          # stop yok
        over = _open("AMD", 2, 10, 12)           # stop adedi pozisyonu aşıyor -> 2 adetle sınırlı
        closed = _closed("X", 30)
        recs = [full, partial, none, over, closed]
        apply_stop_scenario(recs, {"AAPL": [(210.0, 10.0)], "MSFT": [(90.0, 4.0)], "AMD": [(11.0, 5.0)]})
        self.assertAlmostEqual(full.stop_unrealized, 100.0)
        self.assertAlmostEqual(full.current_stop, 210.0)
        self.assertAlmostEqual(partial.stop_unrealized, 80.0)
        self.assertIsNone(none.stop_unrealized)
        self.assertIsNone(none.current_stop)
        self.assertAlmostEqual(over.stop_unrealized, 2.0)
        self.assertIsNone(closed.stop_unrealized)
        # stop_total: kapalıda gerçekleşen, stopsuz açıkta anlık
        self.assertAlmostEqual(closed.stop_total, 30.0)
        self.assertAlmostEqual(none.stop_total, -25.0)

        t = scenario_totals(recs)
        self.assertEqual(t["open_count"], 4)
        self.assertAlmostEqual(t["unrealized"], 200 + 200 - 25 + 4)
        self.assertAlmostEqual(t["stop_unrealized"], 100 + 80 - 25 + 2)
        self.assertAlmostEqual(t["stop_giveback"], (100 + 80 - 25 + 2) - (200 + 200 - 25 + 4))
        self.assertEqual(t["stopless_symbols"], ["NVDA"])
        self.assertEqual(t["closed_count"], 1)
        self.assertAlmostEqual(t["closed_realized"], 30.0)

    def test_group_summary_has_stop_total(self):
        a = _open("AAPL", 10, 200, 220)
        b = _closed("X", -50, algorithm="PBP: ema_cross")
        apply_stop_scenario([a, b], {"AAPL": [(205.0, 10.0)]})
        rows = group_summary([a, b], lambda r: r.algorithm)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["total"], 150.0)
        self.assertAlmostEqual(rows[0]["stop_total"], 0.0)
        self.assertEqual(rows[0]["stopless"], 0)


class StopAlgorithmResolutionTest(unittest.TestCase):
    MODULE_ALGOS = {"Relative Strength": "atr_volatility", "ORB": "opening_range", "Heikin Ashi Gün İçi": "heikin_ashi_exit"}

    def test_module_label_then_pbp_override_then_default(self):
        pbp = {"stop_algorithm": "wait_then_trail", "symbol_settings": {"MSFT": {"stop_algorithm": "adaptive_dynamic"}}}
        self.assertEqual(resolve_stop_algorithm_id(_closed("A", 1, algorithm="ORB"), pbp, self.MODULE_ALGOS), "opening_range")
        self.assertEqual(resolve_stop_algorithm_id(_closed("A", 1, algorithm="PBP: x"), pbp, self.MODULE_ALGOS),
                         "wait_then_trail")
        msft = _closed("MSFT", 1, algorithm="Alım-Stop-Alım: x")
        self.assertEqual(resolve_stop_algorithm_id(msft, pbp, self.MODULE_ALGOS), "adaptive_dynamic")

    def test_open_position_uses_module_holdings_first(self):
        rec = _open("CRWD", 2, 250, 260, algorithm="Bilinmiyor (giriş emri bulunamadı)")
        got = resolve_stop_algorithm_id(rec, {}, self.MODULE_ALGOS, {"Relative Strength": {"CRWD"}, "ORB": set()})
        self.assertEqual(got, "atr_volatility")


class PortfolioSnapshotTest(unittest.TestCase):
    def test_with_and_without_initial_capital(self):
        acct = {"equity": "11000", "cash": "4000", "long_market_value": "7000", "last_equity": "10000"}
        s = portfolio_snapshot(acct, 10000.0)
        self.assertAlmostEqual(s["pl"], 1000.0)
        self.assertAlmostEqual(s["pl_pct"], 10.0)
        self.assertAlmostEqual(s["day_pl"], 1000.0)
        s2 = portfolio_snapshot(acct, None)
        self.assertIsNone(s2["pl"])
        self.assertIsNone(s2["pl_pct"])

    def test_count_by_skips_empty(self):
        recs = [_closed("A", 1), _closed("B", -1, reason=None)]
        self.assertEqual(count_by(recs, "exit_reason"), {"Stop: İlk stop": 1})


def _stop(id_, symbol, price, created, coid, status="replaced"):
    return {"id": id_, "symbol": symbol, "side": "sell", "type": "stop", "status": status, "qty": "10",
            "filled_qty": "0", "stop_price": price, "filled_avg_price": None, "filled_at": None,
            "created_at": created, "client_order_id": coid}


def _fill(id_, symbol, side, price, at, coid=None, qty="10", type_="market"):
    return {"id": id_, "symbol": symbol, "side": side, "type": type_, "status": "filled", "qty": qty,
            "filled_qty": qty, "filled_avg_price": price, "filled_at": at, "created_at": at, "client_order_id": coid}


class StopHistoryTest(unittest.TestCase):
    ORDERS = [
        # Önceki işlemin stopu - bu işleme sayılmamalı
        _stop("s0", "MU", "80", "2026-08-30T14:00:00Z", "stop-initial-MU-0"),
        _fill("b1", "MU", "buy", "100", "2026-09-01T14:00:00Z", "algo-ema_cross-1Day-MU-1"),
        _stop("s1", "MU", "97", "2026-09-01T14:00:01Z", "stop-initial-MU-1"),
        # Aynı seviye, sadece adet değişti -> tek hareket
        _stop("s1b", "MU", "97", "2026-09-01T15:00:00Z", "stop-initial-MU-2"),
        _stop("s2", "MU", "100.2", "2026-09-03T14:00:00Z", "stop-breakeven-MU-3"),
        _stop("s3", "MU", "96", "2026-09-04T00:30:00Z", "shield-MU-10500-4"),
        _stop("s4", "MU", "105", "2026-09-04T13:45:00Z", "stop-restore-MU-5", status="filled"),
        _fill("x1", "MU", "sell", "105", "2026-09-04T15:00:00Z", "stop-restore-MU-5", type_="stop"),
        # Çıkıştan sonraki stop - sayılmamalı
        _stop("s5", "MU", "120", "2026-09-05T14:00:00Z", "stop-initial-MU-6"),
        # Açık pozisyon
        _fill("b2", "AMD", "buy", "50", "2026-09-10T14:00:00Z", "orb-buy-AMD-1"),
        _stop("a1", "AMD", "48", "2026-09-10T14:00:01Z", "stop-initial-AMD-1"),
        _stop("a2", "AMD", "50.1", "2026-09-11T14:00:00Z", "stop-breakeven-AMD-2", status="new"),
    ]

    def test_closed_and_open_moves(self):
        trips, lots = walk_fills(self.ORDERS, now=datetime(2026, 9, 12, tzinfo=timezone.utc))
        self.assertEqual(len(trips), 1)
        moves = trips[0].extra["stop_moves"]
        self.assertEqual([(m.price, m.kind) for m in moves],
                         [(97.0, "initial"), (100.2, "breakeven"), (96.0, "shield"), (105.0, "restore")])
        self.assertEqual(moves[2].real_price, 105.0)
        self.assertEqual([m.price for m in lots["AMD"].stop_moves], [48.0, 50.1])

        recs = closed_records(trips) + open_records(
            [{"symbol": "AMD", "qty": "10", "avg_entry_price": "50", "current_price": "52", "unrealized_pl": "20"}], lots)
        self.assertEqual(len(recs[0].stop_moves), 4)
        self.assertEqual(len(recs[1].stop_moves), 2)

        text = format_stop_moves(moves, timezone.utc)
        self.assertEqual(text, "01.09 97.00 İlk → 03.09 100.20 BE → 04.09 96.00 Kalkan (gerçek 105.00) "
                               "→ 04.09 105.00 Kalkan sonrası")
        self.assertEqual(format_stop_moves([], timezone.utc), "")



class ModuleCashRowsTest(unittest.TestCase):
    def test_budget_spent_remaining_and_pnl(self):
        # 100k hesap, 60k nakit; ORB %10 -> AAA (4k maliyet), geri kalan PBP: BBB (36k).
        account = {"equity": "100000", "cash": "60000"}
        positions = [
            {"symbol": "AAA", "qty": "40", "avg_entry_price": "100", "cost_basis": "4000"},
            {"symbol": "BBB", "qty": "360", "avg_entry_price": "100", "cost_basis": "36000"},
        ]
        records = [
            _open("AAA", 40, 100, 105, algorithm="bilinmiyor"),  # sembol ORB'de -> ORB'ye yazılır
            _open("BBB", 360, 100, 99),
            _closed("X", 50, algorithm="ORB"),
            _closed("Y", -20, algorithm="Relative Strength"),
            _closed("Z", 10, algorithm="PBP: ema_cross"),
        ]
        modules = [("Relative Strength", 0.0, set()), ("ORB", 10.0, {"AAA"}), ("Heikin Ashi Gün İçi", 0.0, set())]
        rows = {r["label"]: r for r in module_cash_rows(account, positions, modules, records)}

        orb = rows["ORB"]
        self.assertEqual((orb["budget"], orb["spent"], orb["remaining"]), (10_000.0, 4_000.0, 6_000.0))
        self.assertAlmostEqual(orb["usage_pct"], 40.0)
        self.assertAlmostEqual(orb["unrealized"], 200.0)
        self.assertAlmostEqual(orb["realized"], 50.0)
        self.assertAlmostEqual(orb["return_pct"], 2.5)

        rs = rows["Relative Strength"]
        self.assertEqual(rs["budget"], 0.0)
        self.assertIsNone(rs["usage_pct"])
        self.assertAlmostEqual(rs["realized"], -20.0)

        other = rows[OTHER_ALGOS_LABEL]
        self.assertEqual(other["pct"], 90.0)
        self.assertEqual(other["budget"], 90_000.0)
        self.assertEqual(other["spent"], 36_000.0)
        self.assertEqual(other["remaining"], 54_000.0)  # 60k nakit - ORB'nin harcanmamış 6k'sı
        self.assertAlmostEqual(other["unrealized"], -360.0)
        self.assertAlmostEqual(other["realized"], 10.0)


if __name__ == "__main__":
    unittest.main()
