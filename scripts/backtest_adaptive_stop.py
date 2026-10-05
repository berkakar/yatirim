"""Akıllı Dinamik Stop (adaptive_dynamic) doğrulama backtesti.

Stop kuralını giriş kuralından AYIRARAK ölçer: aynı giriş listesi her stop
kuralıyla ayrı ayrı oynatılır, böylece farklar yalnızca stoptan gelir ve
işlem işlem eşleştirilmiş karşılaştırma yapılabilir.

Girişler (hepsi long, her giriş bağımsız bir işlem olarak oynatılır):
  - "trend":    kapanış > EMA20 > EMA50 olan barlar (canlı sistemin long/trend
                eğilimine yakın), her 3 barda bir.
  - "rastgele": koşulsuz, her 3 barda bir (Van Tharp tarzı rastgele giriş -
                stopun giriş kalitesinden bağımsız değeri).
  - "sinyal":   buy_algorithms'deki canlı sinyaller (demand_zone,
                volatility_support, breakout_volume, heikin_ashi_stoch);
                kırılım bar kapanışında, pullback ertesi barda limit
                değerse dolar.

Ölçütler:
  - R = (çıkış - giriş) / (giriş - ilk stop), giriş/çıkışta %0.05 kayma
    düşülerek.
  - Özsermaye %: canlıdaki risk bazlı adetle (risk_sizing: işlem başına %0.5
    risk, tek pozisyon tavanı %20) işlemin özsermayeye katkısı. Dar stoplar
    tavana takıldığı için R'yi şişirir; bu ölçüt o şişmeyi düzeltir ve asıl
    karar ölçütüdür.
  - Eşleştirilmiş bootstrap: aynı girişlerde (yeni - referans) farkının
    ortalaması için %95 güven aralığı; (hisse, ay) kümeleriyle yeniden
    örnekleme (örtüşen işlemlerin korelasyonu için).

Walk-forward: tarih ekseninde ortadan bölünür. adaptive_dynamic
parametreleri yalnızca EĞİTİM girişlerinde (ve eğitim bitişinde kesilen
fiyat verisiyle) taranır; seçilen ayar ve tüm referans kurallar TEST
döneminde, hiç görülmemiş veride karşılaştırılır.

Veri: varsayılan repo içindeki Alpaca önbellekleri (ağ gerekmez). --yahoo
verilirse (yfinance kurulu ve erişim varsa) belirtilen hisselerin 5 yıllık
günlük verisi indirilir - sunucuda daha uzun geçmişle tekrar doğrulamak için.

Çalıştırma:
  python scripts/backtest_adaptive_stop.py                  # günlük önbellek
  python scripts/backtest_adaptive_stop.py --intraday       # + 30dk önbellek
  python scripts/backtest_adaptive_stop.py --yahoo AAPL,MSFT,NVDA --period 5y
  python scripts/backtest_adaptive_stop.py --no-grid        # tarama yok, varsayılanlar
  python scripts/backtest_adaptive_stop.py --pack backtest_data --intraday   # uygulamadaki veri paketi
"""

import argparse
import itertools
import json
import math
import os
import random
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from buy_algorithms import ALGORITHMS, reject_if_marketable  # noqa: E402
from indicators import ema_series  # noqa: E402
from stop_algorithms import STOP_ALGORITHMS, StopContext, resolve_kwargs  # noqa: E402
from structure import Bar  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLIPPAGE = 0.0005
RISK_PCT = 0.5
MAX_POSITION_PCT = 20.0
WARMUP = 40
HISTORY = 80          # trail'e verilen geçmiş pencere (ATR/ER için)
STRUCT_WINDOW = 60    # yapısal trail için pozisyon barları penceresi
SIGNAL_ALGOS = ("demand_zone", "volatility_support", "breakout_volume", "heikin_ashi_stoch")


# ---------------------------------------------------------------- veri

def _bars_from_cache(path: str, suffix: str, min_bars: int) -> dict[str, list[Bar]]:
    with open(path if os.path.isabs(path) else os.path.join(ROOT, path), encoding="utf-8") as f:
        series = json.load(f)["series"]
    result = {}
    for key, value in series.items():
        if not key.endswith(suffix):
            continue
        raw = value["bars"] if isinstance(value, dict) and "bars" in value else value
        bars = [Bar(t=t, o=b["o"], h=b["h"], l=b["l"], c=b["c"], v=b.get("v", 0)) for t, b in sorted(raw.items())]
        if len(bars) >= min_bars:
            result[key.split(":")[0]] = bars
    return result


def load_daily() -> dict[str, list[Bar]]:
    return _bars_from_cache("alpaca_daily_bars_cache_berkakar.json", ":1Day", 120)


def load_intraday() -> dict[str, list[Bar]]:
    return _bars_from_cache("alpaca_intraday_bars_cache_berkakar.json", ":30Min", 400)


def regular_session(data: dict[str, list[Bar]]) -> dict[str, list[Bar]]:
    """Gün içi barlardan yalnızca normal seansı (09:30-16:00 New York) tutar -
    Alpaca stop emirleri seans dışında tetiklenmez, ince seans dışı barlar
    stop testini bozar."""
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")

    def inside(bar: Bar) -> bool:
        t = datetime.fromisoformat(bar.t.replace("Z", "+00:00")).astimezone(ny)
        minutes = t.hour * 60 + t.minute
        return 9 * 60 + 30 <= minutes < 16 * 60

    return {s: [b for b in bars if inside(b)] for s, bars in data.items()}


def load_pack(directory: str, timeframe: str, min_bars: int) -> dict[str, list[Bar]]:
    """Uygulamadaki 📦 Backtest Veri Paketi'nin (backtest_data_pack.py) yazdığı
    <directory>/<timeframe>/<HİSSE>.json dosyaları - biçim önbellekle aynı."""
    folder = os.path.join(directory, timeframe)
    result: dict[str, list[Bar]] = {}
    if not os.path.isdir(folder):
        return result
    for name in sorted(os.listdir(folder)):
        if name.endswith(".json"):
            result.update(_bars_from_cache(os.path.join(folder, name), f":{timeframe}", min_bars))
    return result


def load_yahoo(symbols: list[str], period: str) -> dict[str, list[Bar]]:
    import yfinance as yf  # opsiyonel bağımlılık
    result = {}
    for symbol in symbols:
        df = yf.download(symbol, period=period, interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty:
            print(f"  {symbol}: veri yok", file=sys.stderr)
            continue
        if hasattr(df.columns, "levels"):
            df.columns = df.columns.get_level_values(0)
        result[symbol] = [
            Bar(t=idx.strftime("%Y-%m-%dT00:00:00Z"), o=float(r["Open"]), h=float(r["High"]),
                l=float(r["Low"]), c=float(r["Close"]), v=float(r["Volume"]))
            for idx, r in df.iterrows()
        ]
    return result


# ---------------------------------------------------------------- girişler

@dataclass(frozen=True)
class Entry:
    symbol: str
    idx: int        # giriş barının indeksi (giriş bu barın içinde/kapanışında)
    price: float
    kind: str


def trend_and_random_entries(symbol: str, bars: list[Bar], step: int = 3) -> list[Entry]:
    closes = [b.c for b in bars]
    e20, e50 = ema_series(closes, 20), ema_series(closes, 50)
    entries = []
    last_trend = -step
    for i in range(WARMUP, len(bars) - 1):
        if (i - WARMUP) % step == 0:
            entries.append(Entry(symbol, i, bars[i].c, "rastgele"))
        if e20[i] is not None and e50[i] is not None and closes[i] > e20[i] > e50[i] and i - last_trend >= step:
            entries.append(Entry(symbol, i, bars[i].c, "trend"))
            last_trend = i
    return entries


def signal_entries(symbol: str, bars: list[Bar]) -> list[Entry]:
    entries = []
    last: dict[str, int] = {}
    for i in range(WARMUP, len(bars) - 1):
        window = bars[max(0, i - 59): i + 1]
        for algo_id in SIGNAL_ALGOS:
            if i - last.get(algo_id, -99) < 3:
                continue
            try:
                signal = ALGORITHMS[algo_id][1](window, None)
            except Exception:
                signal = None
            if signal is None:
                continue
            signal = reject_if_marketable(signal, bars[i].c)
            if signal is None:
                continue
            if signal.style == "breakout":
                entries.append(Entry(symbol, i, signal.price, f"sinyal:{algo_id}"))
                last[algo_id] = i
            elif bars[i + 1].l <= signal.price:
                entries.append(Entry(symbol, i + 1, min(bars[i + 1].o, signal.price), f"sinyal:{algo_id}"))
                last[algo_id] = i
    return entries


# ---------------------------------------------------------------- simülasyon

def simulate(bars: list[Bar], entry: Entry, algo_id: str, settings: dict, end_idx: int | None = None) -> dict | None:
    """Tek bir girişi seçili stop kuralıyla oynatır. end_idx verilirse o
    bardan sonrası görülmez (walk-forward eğitimi), açık işlem orada kapanır."""
    algo = STOP_ALGORITHMS[algo_id]
    shared = settings.get("shared") or {}
    algo_settings = settings.get(algo_id) or {}
    init_kwargs = resolve_kwargs(algo.initial_stop, algo_settings, shared)
    trail_kwargs = resolve_kwargs(algo.trail, algo_settings, shared)
    last = len(bars) - 1 if end_idx is None else min(end_idx, len(bars) - 1)
    i = entry.idx
    if i >= last:
        return None
    sig_bars = bars[max(0, i - 59): i + 1]
    initial = algo.initial_stop(entry.price, "long", bars=sig_bars, **init_kwargs)
    if not initial < entry.price:
        return None
    stop, reason = initial, "ilk stop"
    exit_price, exit_idx, status = None, last, "açık"
    for j in range(i + 1, last + 1):
        bar = bars[j]
        if bar.l <= stop:
            exit_price, exit_idx, status = min(bar.o, stop), j, reason
            break
        ctx = StopContext(
            side="long", entry_price=entry.price, current_stop_price=stop,
            bars=bars[max(i, j - STRUCT_WINDOW): j + 1],
            daily_closes=[b.c for b in bars[max(0, j - 120): j + 1]],
            initial_stop_price=initial, history_bars=bars[max(0, j - HISTORY): j + 1],
        )
        decision = algo.trail(ctx, **trail_kwargs)
        if decision is not None and decision.price > stop:
            stop, reason = decision.price, decision.reason
    if exit_price is None:
        exit_price = bars[last].c
    buy = entry.price * (1 + SLIPPAGE)
    sell = exit_price * (1 - SLIPPAGE)
    risk = entry.price - initial
    stop_dist_pct = risk / entry.price * 100
    position_pct = min(RISK_PCT / stop_dist_pct * 100, MAX_POSITION_PCT)
    ret_pct = (sell - buy) / buy * 100
    return {
        "r": (sell - buy) / risk, "ret_pct": ret_pct, "equity_pct": position_pct * ret_pct / 100,
        "stop_dist_pct": stop_dist_pct, "hold": exit_idx - i, "status": status,
        "exit_t": bars[exit_idx].t, "initial_hit": status == "ilk stop",
    }


def run_config(data: dict[str, list[Bar]], entries: list[Entry], algo_id: str, settings: dict,
               cut: dict[str, int] | None = None) -> list[dict | None]:
    return [simulate(data[e.symbol], e, algo_id, settings, cut.get(e.symbol) if cut else None) for e in entries]


# ---------------------------------------------------------------- ölçütler

def summarize(results: list[dict | None]) -> dict:
    res = [r for r in results if r is not None]
    if not res:
        return {"n": 0}
    rs = [r["r"] for r in res]
    eq = [r["equity_pct"] for r in res]
    wins = [x for x in eq if x > 0]
    losses = [-x for x in eq if x < 0]
    curve, peak, max_dd = 0.0, 0.0, 0.0
    for r in sorted(res, key=lambda x: x["exit_t"]):
        curve += r["equity_pct"]
        peak = max(peak, curve)
        max_dd = max(max_dd, peak - curve)
    top3 = sum(sorted(eq)[-3:])
    return {
        "n": len(res), "win": len(wins) / len(res) * 100, "avg_r": statistics.mean(rs),
        "med_r": statistics.median(rs), "avg_eq": statistics.mean(eq), "sum_eq": sum(eq),
        "sum_eq_ex3": sum(eq) - top3, "pf": (sum(wins) / sum(losses)) if losses else float("inf"),
        "max_dd": max_dd, "hold": statistics.mean(r["hold"] for r in res),
        "stop_dist": statistics.median(r["stop_dist_pct"] for r in res),
        "initial_hit": sum(r["initial_hit"] for r in res) / len(res) * 100,
    }


def paired_bootstrap(entries: list[Entry], a: list[dict | None], b: list[dict | None], data: dict[str, list[Bar]],
                     key: str = "equity_pct", n_boot: int = 3000, seed: int = 7) -> tuple[float, float, float]:
    """(a - b) ortalama farkı ve %95 GA; (hisse, ay) kümeleriyle."""
    clusters: dict[tuple, list[float]] = {}
    for e, x, y in zip(entries, a, b):
        if x is None or y is None:
            continue
        month = data[e.symbol][e.idx].t[:7]
        clusters.setdefault((e.symbol, month), []).append(x[key] - y[key])
    groups = list(clusters.values())
    if not groups:
        return 0.0, 0.0, 0.0
    flat = [d for g in groups for d in g]
    mean = statistics.mean(flat)
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        sample = [d for _ in groups for d in rng.choice(groups)]
        boots.append(statistics.mean(sample))
    boots.sort()
    return mean, boots[int(0.025 * n_boot)], boots[int(0.975 * n_boot)]


# ---------------------------------------------------------------- ayarlar

def load_saved_settings() -> dict:
    path = os.path.join(ROOT, "stop_loss_settings_berkakar.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


GRID = {
    "trail_atr_mult": [3.5, 5.0, 7.0],
    "trail_start_r": [1.0, 1.5, 2.5],
    "tighten_per_r": [0.0, 0.25, 0.5],
    "er_weight": [0.0, 0.5],
    "breakeven_r": [0.0, 1.0],          # 0 = breakeven kapalı
    "max_atr_mult": [2.0, 3.0],
}


def _grid_worker(args):
    data, entries, cut, params = args
    settings = {"adaptive_dynamic": {**params}}
    return params, summarize(run_config(data, entries, "adaptive_dynamic", settings, cut))


def grid_search(data, entries, cut, workers: int) -> list[tuple[dict, dict]]:
    keys = list(GRID)
    combos = [dict(zip(keys, values)) for values in itertools.product(*(GRID[k] for k in keys))]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_grid_worker, [(data, entries, cut, c) for c in combos], chunksize=4))
    return results


def robust_pick(results: list[tuple[dict, dict]]) -> dict:
    """Tek bir şanslı hücre yerine komşularıyla birlikte iyi olan ayarı seçer:
    skor = kendi ortalama özsermaye katkısı ile, tek parametresi farklı
    komşularının ortalamasının yarı yarıya karışımı (aşırı uyuma karşı)."""
    table = {tuple(sorted(p.items())): s.get("avg_eq", -1e9) for p, s in results if s.get("n")}
    best, best_score = None, -1e18
    for params, s in results:
        if not s.get("n"):
            continue
        neighbours = []
        for k, values in GRID.items():
            for v in values:
                if v != params[k]:
                    key = tuple(sorted({**params, k: v}.items()))
                    if key in table:
                        neighbours.append(table[key])
        score = 0.5 * s["avg_eq"] + 0.5 * (statistics.mean(neighbours) if neighbours else s["avg_eq"])
        if score > best_score:
            best, best_score = params, score
    return best


# ---------------------------------------------------------------- rapor

def _row(label: str, s: dict) -> str:
    if not s.get("n"):
        return f"| {label} | 0 | | | | | | | | |"
    pf = "∞" if math.isinf(s["pf"]) else f"{s['pf']:.2f}"
    return (f"| {label} | {s['n']} | %{s['win']:.0f} | {s['avg_r']:+.2f} | {s['avg_eq']:+.3f} | {s['sum_eq']:+.1f} | "
            f"{s['sum_eq_ex3']:+.1f} | {pf} | {s['max_dd']:.1f} | %{s['stop_dist']:.1f} | %{s['initial_hit']:.0f} | "
            f"{s['hold']:.1f} |")


HEADER = ("| Stop kuralı | İşlem | İsabet | Ort. R | Ort. özs.% | Toplam özs.% | En iyi 3 hariç | PF | Maks. DD | "
          "Med. stop mesafesi | İlk stopta çıkış | Ort. süre (bar) |\n"
          "|---|---|---|---|---|---|---|---|---|---|---|---|")


def split_entries(data, entries) -> tuple[list[Entry], list[Entry], dict[str, int], str]:
    """Her hisse kendi verisinin ortasından bölünür (veri uzunlukları farklı);
    eğitim işlemleri o bölme noktasında kesilir - test fiyatı eğitimde görülmez."""
    cut = {symbol: WARMUP + (len(bars) - WARMUP) // 2 for symbol, bars in data.items()}
    train = [e for e in entries if e.idx < cut[e.symbol]]
    test = [e for e in entries if e.idx >= cut[e.symbol]]
    dates = sorted(data[s][c].t[:10] for s, c in cut.items())
    return train, test, cut, f"hisse başına veri ortası (medyan {dates[len(dates) // 2]})"


def evaluate(name: str, data: dict[str, list[Bar]], args) -> list[str]:
    saved = load_saved_settings()
    entries: list[Entry] = []
    for symbol, bars in data.items():
        entries += trend_and_random_entries(symbol, bars, step=args.step)
        if not args.no_signals:
            entries += signal_entries(symbol, bars)
    train, test, cut, split = split_entries(data, entries)
    lines = [f"## {name}", "",
             f"{len(data)} hisse, {sum(len(b) for b in data.values())} bar; bölme: **{split}** "
             f"(eğitim {len(train)} giriş, test {len(test)} giriş).", ""]

    tuned = None
    if not args.no_grid:
        results = grid_search(data, train, cut, args.workers)
        tuned = robust_pick(results)
        ranked = sorted((r for r in results if r[1].get("n")), key=lambda r: r[1]["avg_eq"], reverse=True)
        lines += [f"**Eğitimde taranan {len(results)} ayar** - seçilen (komşularıyla birlikte sağlam): "
                  f"`{tuned}`; eğitimdeki en iyi tekil hücre: `{ranked[0][0]}` "
                  f"(ort. özs. {ranked[0][1]['avg_eq']:+.3f}%).", ""]

    configs = [
        ("Breakeven+Yapısal (kayıtlı ayar, canlı)", "breakeven_atr_structure", saved),
        ("Beklemeli ve İz Süren (kayıtlı)", "wait_then_trail", saved),
        ("Oynaklık (ATR) Stop (kayıtlı)", "atr_volatility", saved),
        ("Akıllı Dinamik - kod varsayılanı", "adaptive_dynamic", {}),
    ]
    if tuned is not None:
        configs.append(("Akıllı Dinamik - eğitimde seçilen", "adaptive_dynamic", {"adaptive_dynamic": tuned}))

    # Yıllara göre (tüm girişler, eğitim+test): sabit ayarlı kuralların farklı
    # piyasa dönemlerinde (ör. 2022 düşüşü) nasıl davrandığı. "Eğitimde seçilen"
    # eğitim yıllarında örneklem içi olduğu için burada yok.
    years = sorted({data[e.symbol][e.idx].t[:4] for e in entries})
    if len(years) > 1:
        fixed = configs[:4]
        by_year = {label: run_config(data, entries, algo_id, settings) for label, algo_id, settings in fixed}
        lines += ["**Yıllara göre ortalama özsermaye katkısı (% / işlem, tüm girişler)**", "",
                  "| Yıl | İşlem | " + " | ".join(label for label, _, _ in fixed) + " |",
                  "|---|---|" + "---|" * len(fixed)]
        for year in years:
            idx = [k for k, e in enumerate(entries) if data[e.symbol][e.idx].t[:4] == year]
            cells = []
            for label, _, _ in fixed:
                s_ = summarize([by_year[label][k] for k in idx])
                cells.append(f"{s_['avg_eq']:+.3f}" if s_.get("n") else "-")
            lines.append(f"| {year} | {len(idx)} | " + " | ".join(cells) + " |")
        lines.append("")

    for kind_label, kind_filter in (("Tüm girişler", None), ("Trend girişleri", "trend"),
                                    ("Rastgele girişler", "rastgele"), ("Canlı sinyal girişleri", "sinyal")):
        subset = [e for e in test if kind_filter is None or e.kind.startswith(kind_filter)]
        if not subset:
            continue
        lines += [f"**TEST dönemi - {kind_label}** ({len(subset)} giriş, hiç görülmemiş veri)", "", HEADER]
        outcomes = {}
        for label, algo_id, settings in configs:
            outcomes[label] = run_config(data, subset, algo_id, settings)
            lines.append(_row(label, summarize(outcomes[label])))
        lines.append("")
        base_label = configs[0][0]
        for label, _, _ in configs[3:]:
            m, lo, hi = paired_bootstrap(subset, outcomes[label], outcomes[base_label], data)
            mr, lor, hir = paired_bootstrap(subset, outcomes[label], outcomes[base_label], data, key="r")
            verdict = "anlamlı ✅" if lo > 0 else ("anlamlı ❌" if hi < 0 else "anlamlı değil")
            lines.append(f"- {label} − {base_label}: işlem başına özs. **{m:+.3f}%** (%95 GA {lo:+.3f} … {hi:+.3f}, "
                         f"{verdict}); R farkı {mr:+.2f} ({lor:+.2f} … {hir:+.2f})")
        lines.append("")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--intraday", action="store_true", help="30dk önbelleği de değerlendir")
    parser.add_argument("--yahoo", default="", help="virgülle hisse listesi - yfinance ile günlük veri")
    parser.add_argument("--period", default="5y")
    parser.add_argument("--pack", default="", help="Backtest Veri Paketi klasörü (ör. backtest_data)")
    parser.add_argument("--step", type=int, default=3, help="trend/rastgele girişler kaç barda bir")
    parser.add_argument("--only-intraday", action="store_true", help="--pack ile: günlük kısmı atla")
    parser.add_argument("--no-grid", action="store_true")
    parser.add_argument("--no-signals", action="store_true")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    args = parser.parse_args()

    started = datetime.now()
    lines = [f"# Akıllı Dinamik Stop doğrulaması ({started:%d.%m.%Y})", "",
             f"Kayma %{SLIPPAGE * 100:g}/taraf; özsermaye % = risk bazlı adet (%{RISK_PCT:g} risk, "
             f"%{MAX_POSITION_PCT:g} pozisyon tavanı) ile işlemin özsermayeye katkısı.", ""]
    if args.pack:
        pack = os.path.abspath(args.pack)
        if not args.only_intraday:
            lines += evaluate("Günlük bar (veri paketi)", load_pack(pack, "1Day", 120), args)
        if args.intraday or args.only_intraday:
            lines += evaluate("30 dakikalık bar (veri paketi, normal seans)",
                              regular_session(load_pack(pack, "30Min", 400)), args)
    elif args.yahoo:
        lines += evaluate("Yahoo günlük", load_yahoo([s.strip() for s in args.yahoo.split(",") if s.strip()],
                                                      args.period), args)
    else:
        lines += evaluate("Günlük bar (Alpaca önbelleği)", load_daily(), args)
    if args.intraday and not args.pack:
        lines += evaluate("30 dakikalık bar (Alpaca önbelleği)", load_intraday(), args)
    lines.append(f"_Süre: {(datetime.now() - started).seconds} sn_")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
