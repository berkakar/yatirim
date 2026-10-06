"""[2026-10-06 · MDB incelemesi] Oynaklık (ATR) Stop varyantlarının karşılaştırması.

MDB'de stop güncel fiyatın ~%12 gerisinde kaldı: 28.09 kazanç boşluğu (110$
gerçek aralık) günlük ATR'yi 18.6'dan 27.7'ye şişirdi; 1R %12 tavanına dayandı
ve 3xATR chandelier ~%23 geriden gelecekti. İki düzeltme ölçülür:

  1. Aykırı gün ayıklama (atr_outlier_mult, yalnızca günlük barlar): gerçek
     aralığı pencere medyanının N katını aşan gün ATR ortalamasına katılmaz.
  2. Kademeli daralma (trail_tighten_per_r): chandelier çarpanı 2R'de 3x'ten
     başlar, ötesindeki her R için 0.25 daralır, 2x'in altına inmez
     (2R 3x, 4R 2.5x, 6R 2x).

Parametreler a priori seçildi (tarama yok) - bu yüzden tüm veri tek parça
değerlendirilir; yine de dönem dönem (ilk/ikinci yarı) tutarlılık gösterilir.
Girişler ve simülasyon scripts/backtest_adaptive_stop.py ile aynı (trend,
rastgele ve canlı sinyal girişleri; aynı girişler her varyantla oynatılır,
farklar yalnızca stoptan gelir).

Çalıştırma: python scripts/compare_atr_trail_variants.py [--intraday] [--pack backtest_data]
"""

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest_adaptive_stop import (  # noqa: E402
    HEADER, WARMUP, _row, load_daily, load_intraday, load_pack, load_saved_settings, paired_bootstrap,
    run_config, signal_entries, summarize, trend_and_random_entries,
)

ALGO = "atr_volatility"


def variants(saved: dict) -> list[tuple[str, dict]]:
    base = dict(saved.get(ALGO) or {})
    return [
        ("Önceki (aykırı gün dahil)", {**base, "atr_outlier_mult": 0.0}),
        ("Aykırı gün hariç (3x medyan)", {**base, "atr_outlier_mult": 3.0}),
        ("Kademeli daralma 3x→2x", {**base, "atr_outlier_mult": 0.0, "trail_tighten_per_r": 0.25, "trail_min_atr_mult": 2.0}),
        ("Aykırı hariç + kademeli", {**base, "atr_outlier_mult": 3.0, "trail_tighten_per_r": 0.25, "trail_min_atr_mult": 2.0}),
        # Duyarlılık: seçilen değerlerin komşuları da aynı yönde mi?
        ("  (duyarlılık) aykırı eşiği 2.5x", {**base, "atr_outlier_mult": 2.5}),
        ("  (duyarlılık) aykırı eşiği 4x", {**base, "atr_outlier_mult": 4.0}),
    ]


def evaluate(name: str, data, args) -> list[str]:
    saved = load_saved_settings()
    shared = saved.get("shared") or {}
    entries = []
    for symbol, bars in data.items():
        entries += trend_and_random_entries(symbol, bars, step=args.step)
        entries += signal_entries(symbol, bars)
    cut = {s: WARMUP + (len(b) - WARMUP) // 2 for s, b in data.items()}
    configs = [(label, {"shared": shared, ALGO: settings}) for label, settings in variants(saved)]
    lines = [f"## {name}", "", f"{len(data)} hisse, {sum(len(b) for b in data.values())} bar, {len(entries)} giriş.", ""]

    groups = [("Tüm girişler", entries),
              ("Canlı sinyal girişleri", [e for e in entries if e.kind.startswith("sinyal")]),
              ("Trend girişleri", [e for e in entries if e.kind == "trend"]),
              ("Rastgele girişler", [e for e in entries if e.kind == "rastgele"]),
              ("Dönem 1 (ilk yarı)", [e for e in entries if e.idx < cut[e.symbol]]),
              ("Dönem 2 (ikinci yarı)", [e for e in entries if e.idx >= cut[e.symbol]])]
    for label, subset in groups:
        if not subset:
            continue
        outcomes = {c[0]: run_config(data, subset, ALGO, c[1]) for c in configs}
        lines += [f"**{label}** ({len(subset)} giriş)", "", HEADER]
        lines += [_row(c[0], summarize(outcomes[c[0]])) for c in configs]
        lines.append("")
        base = configs[0][0]
        for c in configs[1:4]:
            m, lo, hi = paired_bootstrap(subset, outcomes[c[0]], outcomes[base], data)
            mr, lor, hir = paired_bootstrap(subset, outcomes[c[0]], outcomes[base], data, key="r")
            changed = sum(1 for a, b in zip(outcomes[c[0]], outcomes[base])
                          if a and b and abs(a["equity_pct"] - b["equity_pct"]) > 1e-9)
            verdict = "anlamlı ✅" if lo > 0 else ("anlamlı ❌" if hi < 0 else "anlamlı değil")
            lines.append(f"- {c[0]} − mevcut: işlem başına özs. **{m:+.4f}%** (%95 GA {lo:+.4f} … {hi:+.4f}, "
                         f"{verdict}); R farkı {mr:+.3f} ({lor:+.3f} … {hir:+.3f}); sonucu değişen işlem {changed}")
        lines.append("")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--intraday", action="store_true")
    parser.add_argument("--pack", default="")
    parser.add_argument("--step", type=int, default=3)
    args = parser.parse_args()
    started = datetime.now()
    lines = [f"# Oynaklık (ATR) Stop varyantları ({started:%d.%m.%Y})", ""]
    if args.pack:
        lines += evaluate("Günlük bar (veri paketi)", load_pack(os.path.abspath(args.pack), "1Day", 120), args)
    else:
        lines += evaluate("Günlük bar (Alpaca önbelleği)", load_daily(), args)
    if args.intraday:
        lines += evaluate("30 dakikalık bar (Alpaca önbelleği)", load_intraday(), args)
    lines.append(f"_Süre: {(datetime.now() - started).seconds} sn_")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
