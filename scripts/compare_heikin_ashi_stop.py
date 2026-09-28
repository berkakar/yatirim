"""[2026-09-28 · Doğrulama] Heikin Ashi Çıkışı stopunun ilk stop tabanı.

heikin_ashi_initial_stop'a eklenen oynaklık tabanını (stop girişten en az
min_atr_mult x ATR uzakta) eski davranışla (min_atr_mult=0, sadece sinyal
barının low'u) karşılaştırır. Repo içindeki önbelleklerle çalışır (ağ
gerekmez), heikin_ashi_stoch alış sinyali + heikin_ashi_exit stopuyla
backtest_engine.run_backtest'i 30dk önbellekte (alpaca_intraday_bars_cache_
berkakar.json - Heikin Ashi Gün İçi modülünün canlıda kullandığı periyot)
koşturur. Günlük önbellek KULLANILMIYOR: backtest canlı sistem gibi son 60
günü (~42 günlük bar) görür, SMA50 hiç hesaplanamaz, sinyal oluşmaz.
Sınır: önbellekte sadece ~2 aylık 30dk veri var, işlem sayısı az - sonuç
yön göstericidir. Ayrıntı için her işlem de satır satır yazdırılır.

İki ölçü verilir: backtest_engine her işleme tüm nakitle girdiği için
"Getiri %" eşit tutarlı işlemleri gösterir; "Toplam R" ise her işlemi o
varyantın KENDİ ilk stop mesafesine böler - canlıdaki risk bazlı adetle
(geniş stop = küçük pozisyon) karşılaştırılabilir ölçü budur.

Çalıştırma: python scripts/compare_heikin_ashi_stop.py
"""

import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest_engine import run_backtest  # noqa: E402
from stop_algorithms import STOP_ALGORITHMS, resolve_kwargs  # noqa: E402
from structure import Bar  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET = ZoneInfo("America/New_York")
ALGO_ID = "heikin_ashi_exit"
VARIANTS = [("Eski (taban yok)", 0.0), ("0.5 x ATR", 0.5), ("1 x ATR (yeni varsayılan)", 1.0), ("1.5 x ATR", 1.5)]


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _load_series(path: str, timeframe: str) -> dict[str, list[Bar]]:
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        series = json.load(f)["series"]
    result = {}
    for key, value in series.items():
        symbol, tf = key.split(":")
        if tf != timeframe:
            continue
        raw = value["bars"] if isinstance(value, dict) and "bars" in value else value
        bars = [Bar(t=t, o=b["o"], h=b["h"], l=b["l"], c=b["c"], v=b.get("v", 0)) for t, b in sorted(raw.items())]
        if timeframe != "1Day":
            # Canlı sistemle aynı: sadece normal seans (09:30-16:00 ET) barları.
            bars = [b for b in bars if (lambda t: t.weekday() < 5 and (9, 30) <= (t.hour, t.minute) < (16, 0))(
                _parse(b.t).astimezone(ET))]
        result[symbol] = bars
    return result


def _settings(min_atr_mult: float) -> dict:
    with open(os.path.join(ROOT, "stop_loss_settings_berkakar.json"), encoding="utf-8") as f:
        saved = json.load(f)
    algo = dict(saved.get(ALGO_ID) or {})
    algo["min_atr_mult"] = min_atr_mult
    return {**saved, ALGO_ID: algo}


def _run(dataset: dict[str, list[Bar]], daily: dict[str, list[Bar]], timeframe: str, warmup_days: int,
         min_atr_mult: float) -> dict:
    settings = _settings(min_atr_mult)
    algo = STOP_ALGORITHMS[ALGO_ID]
    kwargs = resolve_kwargs(algo.initial_stop, settings.get(ALGO_ID) or {}, settings.get("shared") or {})
    pcts, rs, stop_dist, stop_outs = [], [], [], 0
    for symbol, bars in dataset.items():
        if len(bars) < 100:
            continue
        daily_bars = daily.get(symbol) or []
        pairs = [(_parse(b.t).date(), b.c) for b in daily_bars]
        result = run_backtest(symbol, "heikin_ashi_stoch", timeframe, bars, pairs, len(bars), warmup_days, 10_000.0,
                              stop_algorithm=ALGO_ID, stop_settings=settings)
        for buy, sell in zip(result.trades[0::2], result.trades[1::2]):
            history = [b for b in bars if b.t <= buy.time]
            initial = algo.initial_stop(buy.price, "long", bars=history, **kwargs)
            risk = buy.price - initial
            pcts.append((sell.price - buy.price) / buy.price * 100)
            if risk > 0:
                rs.append((sell.price - buy.price) / risk)
                stop_dist.append(risk / buy.price * 100)
            if sell.price < buy.price and sell.reason != "test_end_close":
                stop_outs += 1
    n = len(pcts)
    return {
        "n": n,
        "win": sum(1 for p in pcts if p > 0) / n * 100 if n else 0.0,
        "pct": sum(pcts),
        "avg_pct": sum(pcts) / n if n else 0.0,
        "r": sum(rs),
        "stop_dist": sorted(stop_dist)[len(stop_dist) // 2] if stop_dist else 0.0,
        "losses": stop_outs,
    }


def _table(title: str, dataset: dict[str, list[Bar]], daily: dict[str, list[Bar]], timeframe: str,
           warmup_days: int) -> list[str]:
    symbols = [s for s, b in dataset.items() if len(b) >= 100]
    lines = [f"**{title}** - {len(symbols)} hisse", "",
             "| İlk stop | İşlem | İsabet | Zararla kapanan | Medyan stop mesafesi | Toplam getiri % | İşlem başı % | Toplam R |",
             "|---|---|---|---|---|---|---|---|"]
    for label, mult in VARIANTS:
        m = _run(dataset, daily, timeframe, warmup_days, mult)
        lines.append(f"| {label} | {m['n']} | %{m['win']:.0f} | {m['losses']} | %{m['stop_dist']:.2f} | "
                     f"{m['pct']:+.1f}% | {m['avg_pct']:+.2f}% | {m['r']:+.1f}R |")
    lines.append("")
    return lines


def main() -> None:
    daily = _load_series("alpaca_daily_bars_cache_berkakar.json", "1Day")
    intraday = _load_series("alpaca_intraday_bars_cache_berkakar.json", "30Min")
    print("\n".join(_table("30dk barlar (HA Gün İçi periyodu)", intraday, daily, "30Min", 5)))


if __name__ == "__main__":
    main()
