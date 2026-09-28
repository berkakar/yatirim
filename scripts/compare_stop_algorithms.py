"""[2026-09-28 · Doğrulama] Eski ve yeni stop/risk kurallarının karşılaştırması.

Repo içindeki önbelleklerle (ağ gerekmez) iki karşılaştırma yapar ve sonucu
Markdown olarak yazdırır (changelog.VERIFICATION_NOTES'a aktarılır):

1. Gerçek işlemlerin yeniden oynatılması: alpaca_realized_pnl_cache_berkakar.json
   içindeki GERÇEK girişler, aktif kural (Breakeven+Yapısal, günlük barda,
   1R breakeven) ve opsiyonel ATR stopuyla günlük önbellek barları üzerinde
   yeniden oynatılır; gerçekleşen sonuçla R cinsinden yan yana konur. Sınırlar: giriş günü içindeki fiyat
   hareketi görülmez (stop kontrolü ertesi günden başlar); önbellek bitiminde
   hâlâ açık olan pozisyon son kapanıştan değerlenir; açılış kalkanı günlük
   çözünürlükte modellenmez.
2. Backtest motoru: günlük önbellekte en az ~6 aylık verisi olan hisselerde
   demand_zone (1 Gün) sinyali ile eski ayarlar (%1 breakeven, tampon yok),
   yeni ayarlar ve atr_volatility varyantları. backtest_engine tüm nakitle
   girdiği için sonuç R cinsinden verilir (canlıdaki risk bazlı adetle
   karşılaştırılabilir ölçü).

Çalıştırma: python scripts/compare_stop_algorithms.py
"""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest_engine import run_backtest  # noqa: E402
from stop_algorithms import STOP_ALGORITHMS, StopContext, resolve_kwargs  # noqa: E402
from structure import Bar  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_daily() -> dict[str, list[Bar]]:
    with open(os.path.join(ROOT, "alpaca_daily_bars_cache_berkakar.json"), encoding="utf-8") as f:
        series = json.load(f)["series"]
    result = {}
    for key, value in series.items():
        raw = value["bars"] if isinstance(value, dict) and "bars" in value else value
        bars = [Bar(t=t, o=b["o"], h=b["h"], l=b["l"], c=b["c"], v=b.get("v", 0)) for t, b in sorted(raw.items())]
        result[key.split(":")[0]] = bars
    return result


def _load_trades() -> list[dict]:
    with open(os.path.join(ROOT, "alpaca_realized_pnl_cache_berkakar.json"), encoding="utf-8") as f:
        data = json.load(f)["symbols"]
    trades = []
    for symbol, info in data.items():
        fills = sorted(info["fills"], key=lambda x: x["filled_at"])
        for buy, sell in zip(fills, fills[1:]):
            if buy["side"] == "buy" and sell["side"] == "sell":
                trades.append({
                    "symbol": symbol, "entry_at": buy["filled_at"], "entry": float(buy["filled_avg_price"]),
                    "exit": float(sell["filled_avg_price"]), "qty": float(buy["filled_qty"]),
                })
    return sorted(trades, key=lambda t: t["entry_at"])


ACTIVE_SETTINGS = {
    "shared": {"atr_period": 14, "atr_multiplier": 0.25, "trend_ema_period": 50, "swing_order": 2},
    "breakeven_atr_structure": {"initial_stop_pct": 1.5, "breakeven_trigger_pct": 1.5, "breakeven_buffer_pct": 0.2},
}
OLD_SETTINGS = {
    "shared": ACTIVE_SETTINGS["shared"],
    "breakeven_atr_structure": {"initial_stop_pct": 1.5, "breakeven_trigger_pct": 1.0, "breakeven_buffer_pct": 0.0},
}
CONFIGS = [
    ("Eski: Breakeven+Yapısal (%1 BE, tampon yok)", "breakeven_atr_structure", OLD_SETTINGS),
    ("Yeni (aktif): Breakeven+Yapısal (%1.5 BE = 1R, %0.2 tampon)", "breakeven_atr_structure", ACTIVE_SETTINGS),
    ("Opsiyonel: Oynaklık (ATR) Stop (2×ATR, 3×ATR chandelier)", "atr_volatility", ACTIVE_SETTINGS),
    ("Opsiyonel: ATR Stop, geniş trail (8×ATR, 4R'den)", "atr_volatility",
     {**ACTIVE_SETTINGS, "atr_volatility": {"trail_atr_mult": 8, "trail_start_r": 4}}),
]


def replay(trade: dict, bars: list[Bar], algo_id: str, settings: dict) -> dict | None:
    """Gerçek girişi günlük barlarda seçili stop kuralıyla oynatır."""
    algo = STOP_ALGORITHMS[algo_id]
    shared = settings.get("shared") or {}
    algo_settings = settings.get(algo_id) or {}
    entry_day = trade["entry_at"][:10]
    history = [b for b in bars if b.t[:10] < entry_day]
    after = [b for b in bars if b.t[:10] >= entry_day]
    if len(history) < 15 or len(after) < 2:
        return None
    entry = trade["entry"]
    initial = algo.initial_stop(entry, "long", bars=history, **resolve_kwargs(algo.initial_stop, algo_settings, shared))
    stop = initial
    closes = [b.c for b in history]
    for i in range(1, len(after)):
        bar = after[i]
        if bar.l <= stop:
            return {"exit": min(bar.o, stop), "exit_day": bar.t[:10], "status": "stop", "initial": initial}
        window = after[: i + 1]
        ctx = StopContext(side="long", entry_price=entry, current_stop_price=stop, bars=window,
                          daily_closes=closes + [b.c for b in window], initial_stop_price=initial,
                          history_bars=history + window)
        decision = algo.trail(ctx, **resolve_kwargs(algo.trail, algo_settings, shared))
        if decision is not None:
            stop = decision.price
    return {"exit": after[-1].c, "exit_day": after[-1].t[:10], "status": "açık", "initial": initial}


def part1(daily: dict[str, list[Bar]]) -> list[str]:
    lines = ["**1) Gerçek girişlerin yeniden oynatılması** (günlük bar; R = (çıkış − giriş) / (giriş − ilk stop); "
             "'açık' = önbellek sonunda hâlâ açık, son kapanıştan)", "",
             "| Hisse | Giriş | Gerçekleşen K/Z | Aktif kural (günlük, 1R BE) | ATR stop |", "|---|---|---|---|---|"]
    totals = [0.0, 0.0, 0.0]
    n = 0
    for t in _load_trades():
        bars = daily.get(t["symbol"])
        if not bars:
            continue
        a = replay(t, bars, "breakeven_atr_structure", ACTIVE_SETTINGS)
        b = replay(t, bars, "atr_volatility", ACTIVE_SETTINGS)
        if a is None or b is None:
            continue
        n += 1
        real_r = (t["exit"] - t["entry"]) / (t["entry"] * 0.015)
        r_a = (a["exit"] - t["entry"]) / (t["entry"] - a["initial"])
        r_b = (b["exit"] - t["entry"]) / (t["entry"] - b["initial"])
        totals[0] += real_r
        totals[1] += r_a
        totals[2] += r_b
        lines.append(
            f"| {t['symbol']} | {t['entry_at'][:10]} @ {t['entry']:.2f} | {(t['exit'] - t['entry']) * t['qty']:+.2f}$ ({real_r:+.2f}R) | "
            f"{a['exit']:.2f} {a['status']} ({r_a:+.2f}R) | {b['exit']:.2f} {b['status']} ({r_b:+.2f}R) |"
        )
    lines += ["", f"{n} işlem toplamı: gerçekleşen **{totals[0]:+.2f}R**, aktif kural **{totals[1]:+.2f}R**, "
              f"ATR stop **{totals[2]:+.2f}R** (işlem başına 500$ riskle R × 500$).", ""]
    return lines


def _trade_r(result, bars: list[Bar], algo_id: str, settings: dict) -> list[float]:
    algo = STOP_ALGORITHMS[algo_id]
    shared = settings.get("shared") or {}
    algo_settings = settings.get(algo_id) or {}
    rs = []
    trades = result.trades
    for buy, sell in zip(trades[0::2], trades[1::2]):
        history = [b for b in bars if b.t[:10] <= buy.time[:10]][-60:]
        initial = algo.initial_stop(buy.price, "long", bars=history, **resolve_kwargs(algo.initial_stop, algo_settings, shared))
        if buy.price > initial:
            rs.append((sell.price - buy.price) / (buy.price - initial))
    return rs


def part2(daily: dict[str, list[Bar]]) -> list[str]:
    symbols = [s for s, b in daily.items() if len(b) >= 130]
    lines = [f"**2) Backtest motoru** - demand_zone (1 Gün) sinyali, stop günlük barlarda, {len(symbols)} hisse "
             f"({', '.join(symbols)}), ~6-13 aylık günlük önbellek. R bazlı (canlıdaki risk bazlı adetle "
             "karşılaştırılabilir):", "",
             "| Stop kuralı | İşlem | İsabet | Toplam R | En büyük 3 işlem hariç R | En büyük işlem |",
             "|---|---|---|---|---|---|"]
    for label, algo_id, settings in CONFIGS:
        rs: list[float] = []
        for symbol in symbols:
            bars = daily[symbol]
            pairs = [(datetime.fromisoformat(b.t.replace("Z", "+00:00")).date(), b.c) for b in bars]
            result = run_backtest(symbol, "demand_zone", "1Day", bars, pairs, len(bars), 60, 10_000.0,
                                  stop_algorithm=algo_id, stop_settings=settings)
            rs += _trade_r(result, bars, algo_id, settings)
        ordered = sorted(rs)
        win = sum(1 for r in rs if r > 0) / len(rs) * 100 if rs else 0.0
        lines.append(f"| {label} | {len(rs)} | %{win:.0f} | {sum(rs):+.1f}R | {sum(ordered[:-3]):+.1f}R | "
                     f"{ordered[-1] if ordered else 0:+.1f}R |")
    lines.append("")
    return lines


def main() -> None:
    daily = _load_daily()
    print("\n".join(part1(daily) + part2(daily)))


if __name__ == "__main__":
    main()
