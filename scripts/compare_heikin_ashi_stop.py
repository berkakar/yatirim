"""[2026-09-28 · Doğrulama] Heikin Ashi Gün İçi: ilk stop tabanı ve çıkış kuralı.

İki şeyi karşılaştırır:
  1. heikin_ashi_initial_stop'un oynaklık tabanı (min_atr_mult: stop girişten
     en az bu kadar ATR uzakta; 0 = sadece sinyal barının low'u).
  2. Kırmızı HA çıkışı için art arda gereken kırmızı mum sayısı
     (exit_red_candles: 1 = ilk kırmızı mumda çık, orijinal kural).

backtest_engine.run_backtest BİLEREK kullanılmıyor: o motor Heikin Ashi Gün
İçi modülünün (heikin_ashi_intraday_core.run_pass) kurallarını modellemiyor -
pozisyonu geceye taşıyor (MDB 2026-08-05 örneğinde açılış boşluğuyla -%4.6
yazdı; canlıda gün sonunda kapanırdı) ve modülün kendi market çıkışını
görmüyor. Buradaki simülasyon canlı modülü izler:
  - Giriş: sinyal barı kapandıktan sonraki barın açılışından (cron :02/:32),
    kapanışa 60 dakikadan az kalmışsa giriş yok (NO_NEW_ENTRY_MINUTES).
  - İlk stop: seçili ayarla heikin_ashi_initial_stop, sinyal barına kadarki
    barlarla.
  - Her bar: önce stop (bar low'u stopa değdiyse min(açılış, stop)'tan dolar),
    sonra bar kapanışında modülün çıkış kontrolü (long_exit_reason, son 10
    günlük barlarla) - sinyal varsa bir sonraki barın açılışından satış; yoksa
    stopu heikin_ashi_trail ile sıkılaştır (alpaca_trailing_stop'un 5 dk botu).
  - Gün sonu: 15:30 ET barında (modül ~15:45'te kapatır) stop tetiklenmediyse
    o barın kapanışından çıkılır (yaklaşık - EOD_FLATTEN_MINUTES).
Sınırlar: max_positions / evren sıralaması modellenmiyor (her hisse bağımsız),
komisyon ve kayma yok; önbellekte sadece ~2 aylık 30dk veri var, işlem sayısı
az - sonuç yön göstericidir.

Çalıştırma: python scripts/compare_heikin_ashi_stop.py
"""

import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from buy_algorithms import heikin_ashi_stoch_signal  # noqa: E402
from heikin_ashi import long_exit_reason  # noqa: E402
from stop_algorithms import STOP_ALGORITHMS, StopContext, resolve_kwargs  # noqa: E402
from structure import Bar  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ET = ZoneInfo("America/New_York")
ALGO_ID = "heikin_ashi_exit"
BARS_LOOKBACK_DAYS = 10  # heikin_ashi_intraday_core.BARS_LOOKBACK_DAYS
LAST_ENTRY_BAR = (14, 0)  # 14:00 barı 14:30'da kapanır, giriş 14:32 -> kapanışa 88 dk
EOD_BAR = (15, 30)

# (etiket, heikin_ashi_exit ayar override'ları)
FLOOR_VARIANTS = [
    ("Taban yok", {"min_atr_mult": 0.0}), ("0.5 x ATR", {"min_atr_mult": 0.5}),
    ("1 x ATR (varsayılan)", {"min_atr_mult": 1.0}), ("1.5 x ATR", {"min_atr_mult": 1.5}),
]
EXIT_VARIANTS = [
    (f"{red} kırmızı mum, {label}", {"exit_red_candles": red, "min_atr_mult": mult})
    for mult, label in ((1.0, "1 x ATR taban"), (0.0, "taban yok"))
    for red in (1, 2, 3)
]


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _et(bar: Bar) -> datetime:
    return _parse(bar.t).astimezone(ET)


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
            bars = [b for b in bars if _et(b).weekday() < 5 and (9, 30) <= (_et(b).hour, _et(b).minute) < (16, 0)]
        result[symbol] = bars
    return result


def _settings(overrides: dict) -> dict:
    with open(os.path.join(ROOT, "stop_loss_settings_berkakar.json"), encoding="utf-8") as f:
        saved = json.load(f)
    return {**saved, ALGO_ID: {**(saved.get(ALGO_ID) or {}), **overrides}}


def _window(bars: list[Bar], end: int, days: float) -> list[Bar]:
    cutoff = _parse(bars[end].t) - timedelta(days=days)
    lo = end
    while lo > 0 and _parse(bars[lo - 1].t) >= cutoff:
        lo -= 1
    return bars[lo:end + 1]


def _simulate_symbol(bars: list[Bar], daily: list[Bar], settings: dict) -> list[dict]:
    algo = STOP_ALGORITHMS[ALGO_ID]
    algo_settings, shared = settings.get(ALGO_ID) or {}, settings.get("shared") or {}
    init_kwargs = resolve_kwargs(algo.initial_stop, algo_settings, shared)
    trail_kwargs = resolve_kwargs(algo.trail, algo_settings, shared)
    red = int(algo_settings.get("exit_red_candles", 1))
    daily_pairs = [(_et(b).date(), b.c) for b in daily]

    trades = []
    i = 0
    while i < len(bars) - 1:
        et = _et(bars[i])
        if (et.hour, et.minute) > LAST_ENTRY_BAR or heikin_ashi_stoch_signal(_window(bars, i, BARS_LOOKBACK_DAYS)) is None:
            i += 1
            continue
        entry_idx = i + 1
        if _et(bars[entry_idx]).date() != et.date():
            i += 1
            continue
        entry = bars[entry_idx].o
        history = _window(bars, i, BARS_LOOKBACK_DAYS)
        stop = initial = round(algo.initial_stop(entry, "long", bars=history, **init_kwargs), 2)
        exit_price, reason, j = None, None, entry_idx
        while j < len(bars):
            bar = bars[j]
            if bar.l <= stop:
                exit_price, reason = min(bar.o, stop), "stop"
                break
            if (_et(bar).hour, _et(bar).minute) >= EOD_BAR:
                exit_price, reason = bar.c, "gün sonu"
                break
            if long_exit_reason(_window(bars, j, BARS_LOOKBACK_DAYS), red_candles=red) is not None:
                exit_price, reason = bars[j + 1].o, "HA çıkış"
                j += 1
                break
            ctx = StopContext(
                side="long", entry_price=entry, current_stop_price=stop, bars=bars[entry_idx:j + 1],
                daily_closes=[c for d, c in daily_pairs if d < _et(bar).date()],
                initial_stop_price=initial, history_bars=_window(bars, j, BARS_LOOKBACK_DAYS),
            )
            decision = algo.trail(ctx, **trail_kwargs)
            if decision is not None and decision.price > stop:
                stop = round(decision.price, 2)
            j += 1
        if exit_price is None:
            break  # veri bitti, pozisyon açık - sayılmaz
        trades.append({"time": bars[entry_idx].t, "entry": entry, "exit": exit_price, "initial": initial,
                       "reason": reason, "held": j - entry_idx + 1})
        i = j + 1
    return trades


def _run(dataset: dict[str, list[Bar]], daily: dict[str, list[Bar]], overrides: dict) -> tuple[dict, list[str]]:
    settings = _settings(overrides)
    pcts, rs, dists, holds, rows = [], [], [], [], []
    reasons: dict[str, int] = {}
    for symbol, bars in dataset.items():
        if len(bars) < 100:
            continue
        for t in _simulate_symbol(bars, daily.get(symbol) or [], settings):
            pct = (t["exit"] / t["entry"] - 1) * 100
            risk = t["entry"] - t["initial"]
            pcts.append(pct)
            holds.append(t["held"])
            reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
            if risk > 0:
                rs.append((t["exit"] - t["entry"]) / risk)
                dists.append(risk / t["entry"] * 100)
            rows.append(f"| {symbol} | {t['time'][:16]} | {t['entry']:.2f} | {t['initial']:.2f} | {t['exit']:.2f} | "
                        f"{pct:+.2f}% | {t['held']} | {t['reason']} |")
    n = len(pcts)
    metrics = {
        "n": n, "win": sum(1 for p in pcts if p > 0) / n * 100 if n else 0.0,
        "pct": sum(pcts), "avg_pct": sum(pcts) / n if n else 0.0, "r": sum(rs),
        "dist": sorted(dists)[len(dists) // 2] if dists else 0.0,
        "hold": sum(holds) / n if n else 0.0,
        "reasons": ", ".join(f"{k} {v}" for k, v in sorted(reasons.items())),
    }
    return metrics, rows


def _table(title: str, variants: list, dataset, daily) -> list[str]:
    symbols = [s for s, b in dataset.items() if len(b) >= 100]
    lines = [f"**{title}** - {len(symbols)} hisse", "",
             "| Varyant | İşlem | İsabet | Medyan stop mesafesi | Ort. tutma (bar) | Çıkış sebepleri | "
             "Toplam getiri % | İşlem başı % | Toplam R |",
             "|---|---|---|---|---|---|---|---|---|"]
    for label, overrides in variants:
        m, _ = _run(dataset, daily, overrides)
        lines.append(f"| {label} | {m['n']} | %{m['win']:.0f} | %{m['dist']:.2f} | {m['hold']:.1f} | {m['reasons']} | "
                     f"{m['pct']:+.1f}% | {m['avg_pct']:+.2f}% | {m['r']:+.1f}R |")
    lines.append("")
    return lines


def _details(label: str, overrides: dict, dataset, daily) -> list[str]:
    _, rows = _run(dataset, daily, overrides)
    return [f"*{label}*", "", "| Hisse | Giriş | Alış | İlk stop | Satış | K/Z | Bar | Çıkış |",
            "|---|---|---|---|---|---|---|---|", *rows, ""]


def main() -> None:
    daily = _load_series("alpaca_daily_bars_cache_berkakar.json", "1Day")
    intraday = _load_series("alpaca_intraday_bars_cache_berkakar.json", "30Min")
    lines = _table("1) İlk stop tabanı (1 kırmızı mumda çıkış)", FLOOR_VARIANTS, intraday, daily)
    lines += _table("2) Kırmızı mum çıkışı", EXIT_VARIANTS, intraday, daily)
    for label, overrides in EXIT_VARIANTS[:2]:
        lines += _details(label, overrides, intraday, daily)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
