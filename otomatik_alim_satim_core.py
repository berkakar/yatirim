"""Otomatik Alım/Satım modülünün paylaşılan pipeline'ı: evren oluşturma →
Alpaca verisiyle tarama → RSI/EMA momentum daraltması → backtest → kârlılık
filtresi → Premium Buy Point portföyüne birleştirme.

Bu modül Streamlit'e bağımlı DEĞİLDİR (sadece `config.py` üzerinden - o da
sadece OKUMA amaçlı, `st.secrets` bulunamazsa sessizce yerel dosyaya düşer -
piyasa/grup listelerini okur). Hem Streamlit sayfası (`otomatik_alim_satim.py`,
adım adım buton akışı için) hem de günlük GitHub Actions script'i
(`otomatik_alim_satim_runner.py`, tam otomatik akış için `run_pipeline`)
tarafından import edilir - aynı mantığın iki kere yazılmasını önler."""

import json
import os
from datetime import datetime, timedelta, timezone

from alpaca_client import AlpacaClient
from alpaca_trailing_stop import get_bars_for_timeframe
from backtest_data import new_run_id
from backtest_engine import run_backtest
from buy_algorithms import ALGORITHMS, compute_all_signals, reject_if_marketable
from config import load_group_markets, load_stock_groups, load_ticker_lists
from momentum_filter import momentum_confirmation, select_top_candidates
from structure import Bar

TIMEFRAME_LABELS = {"15Min": "15 Dakika", "30Min": "30 Dakika", "1Hour": "1 Saat", "1Day": "1 Gün"}
DAILY_LOOKBACK_DAYS = 400  # EMA200 + trend_pullback SMA200 icin yeterli pay
DEFAULT_DAYS_OF_DATA = 180
DEFAULT_DAYS_BEFORE_TRADING = 0
DEFAULT_MIN_BACKTEST_PROFIT_PCT = 10.0
DEFAULT_MAX_CANDIDATES = 10
DEFAULT_MOMENTUM_LOOKBACK_DAYS = 30
DEFAULT_ALGORITHM_ID = next(iter(ALGORITHMS))


def _fetch_bars_for_timeframe(client: AlpacaClient, symbol: str, timeframe: str, start: datetime) -> list[Bar]:
    return get_bars_for_timeframe(client, symbol, timeframe, start, exclude_forming=True)


def _daily_closes(client: AlpacaClient, symbol: str, days: int) -> list[float]:
    start = datetime.now(timezone.utc) - timedelta(days=days)
    raw = client.get_raw_bars(symbol, "1Day", start.isoformat())
    return [b["c"] for b in raw]


def _daily_pairs(client: AlpacaClient, symbol: str, days_of_data: int) -> list[tuple]:
    start = datetime.now(timezone.utc) - timedelta(days=days_of_data + DAILY_LOOKBACK_DAYS)
    raw = client.get_raw_bars(symbol, "1Day", start.isoformat())
    pairs = [(datetime.fromisoformat(b["t"].replace("Z", "+00:00")).date(), b["c"]) for b in raw]
    pairs.sort(key=lambda p: p[0])
    return pairs


def build_universe(username: str, include_nasdaq: bool, include_nyse: bool, custom_groups: list[str]) -> list[str]:
    """NASDAQ 100 ∪ NYSE ∪ (bu piyasalara bağlı, kullanıcı tarafından seçilen)
    özel hisse grupları - BIST hariç, bu modül tamamen Alpaca verisiyle çalışır
    ve Alpaca'da BIST hisseleri işlem görmez."""
    ticker_lists = load_ticker_lists(username)
    stock_groups = load_stock_groups(username)
    group_markets = load_group_markets(username)

    tickers: list[str] = []
    if include_nasdaq:
        tickers += ticker_lists.get("NASDAQ 100", [])
    if include_nyse:
        tickers += ticker_lists.get("NYSE", [])
    for group in custom_groups:
        if group_markets.get(group) in ("NASDAQ 100", "NYSE"):
            tickers += stock_groups.get(group, [])
    return list(dict.fromkeys(tickers))


def scan_universe(
    client: AlpacaClient, tickers: list[str], algorithm_ids: list[str], timeframe_codes: list[str],
    lookback_days: int = 60,
) -> list[dict]:
    """Alım Bölgesi Tarama'yla aynı şekil sinyal satırları üretir, ama
    verisi tamamen Alpaca'dan gelir (yfinance yerine)."""
    rows = []
    for ticker in tickers:
        try:
            daily_closes = _daily_closes(client, ticker, DAILY_LOOKBACK_DAYS)
        except Exception:
            daily_closes = []

        for tf in timeframe_codes:
            start = datetime.now(timezone.utc) - timedelta(days=lookback_days)
            try:
                bars = _fetch_bars_for_timeframe(client, ticker, tf, start)
            except Exception:
                continue
            if not bars:
                continue

            try:
                current_price = client.get_latest_trade_price(ticker) or bars[-1].c
            except Exception:
                current_price = bars[-1].c

            signals = compute_all_signals(bars, daily_closes)
            for algo_id in algorithm_ids:
                signal = reject_if_marketable(signals.get(algo_id), current_price)
                if signal is None:
                    continue
                rows.append({
                    "Hisse": ticker,
                    "Tarayıcı Türü": ALGORITHMS[algo_id][0],
                    "Mum Periyodu": TIMEFRAME_LABELS.get(tf, tf),
                    "Mum Seviyesi": round(signal.price, 2),
                    "_algo_id": algo_id,
                    "_tf_code": tf,
                })
    return rows


def narrow_by_momentum(
    client: AlpacaClient, signal_rows: list[dict], max_candidates: int = DEFAULT_MAX_CANDIDATES,
    lookback_days: int = DEFAULT_MOMENTUM_LOOKBACK_DAYS,
) -> list[dict]:
    """Son `lookback_days` gün içinde RSI14/RSI21 VE EMA50/EMA200 kesişimini
    BİRLİKTE gösteren en fazla `max_candidates` hisseyi (satırını) döner -
    bkz. momentum_filter.py."""
    scored = []
    checked_tickers: dict[str, list[float]] = {}
    for row in signal_rows:
        ticker = row["Hisse"]
        if ticker not in checked_tickers:
            try:
                checked_tickers[ticker] = _daily_closes(client, ticker, DAILY_LOOKBACK_DAYS)
            except Exception:
                checked_tickers[ticker] = []
        signal = momentum_confirmation(checked_tickers[ticker], lookback_days)
        if signal is not None:
            scored.append((row, signal))
    return select_top_candidates(scored, max_candidates)


def run_backtests(
    client: AlpacaClient, candidate_rows: list[dict], cash_allocation: float, username: str,
    days_of_data: int = DEFAULT_DAYS_OF_DATA, days_before_trading: int = DEFAULT_DAYS_BEFORE_TRADING,
) -> list[dict]:
    """Her adayın KENDİ tarama sinyalini ürettiği (algoritma, mum periyodu)
    çiftiyle backtest çalıştırır - BackTest modülündeki gibi tüm
    algoritma×periyot kombinasyonlarını değil, çünkü adaylar zaten belirli
    bir sinyalle taramadan/momentum filtresinden geçmiş durumda. Sonuçları
    HİÇBİR YERE YAZMAZ (persist etmez) - bunu çağıran taraf (Streamlit sayfası
    ya da günlük script) kendi bağlamına uygun şekilde yapar."""
    budget_per_candidate = cash_allocation / len(candidate_rows) if candidate_rows else 0.0
    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results = []
    for row in candidate_rows:
        symbol, algo_id, tf = row["Hisse"], row["_algo_id"], row["_tf_code"]
        start = datetime.now(timezone.utc) - timedelta(days=days_of_data)
        try:
            bars = _fetch_bars_for_timeframe(client, symbol, tf, start)
        except Exception:
            bars = []
        try:
            daily_pairs = _daily_pairs(client, symbol, days_of_data)
        except Exception:
            daily_pairs = []

        result = run_backtest(
            symbol=symbol, algorithm=algo_id, timeframe=tf, bars=bars, daily_pairs=daily_pairs,
            days_of_data=days_of_data, days_before_trading=days_before_trading,
            starting_budget=budget_per_candidate,
        )
        results.append({
            "run_id": new_run_id(symbol, algo_id, tf),
            "run_at": run_at,
            "symbol": symbol,
            "algorithm": algo_id,
            "timeframe": tf,
            "days_of_data": days_of_data,
            "days_before_trading": days_before_trading,
            "starting_budget": budget_per_candidate,
            "final_value": result.final_value,
            "pnl": result.pnl,
            "pnl_pct": result.pnl_pct,
            "stop_loss_enabled": False,
            "max_loss_pct": None,
            "stop_loss_triggered": result.stop_loss_triggered,
            "stop_loss_triggered_at": result.stop_loss_triggered_at,
            "trades": [vars(t) for t in result.trades],
            "source": "Alpaca",
        })
    return results


def filter_profitable(results: list[dict], min_pct: float = DEFAULT_MIN_BACKTEST_PROFIT_PCT) -> list[dict]:
    return [r for r in results if (r.get("pnl_pct") or 0) > min_pct]


def merge_into_portfolio(username: str, selected_rows: list[dict], client: AlpacaClient) -> dict:
    """SADECE günlük otomatik koşu (`run_pipeline`) tarafından kullanılır -
    portfolio_config_<username>.json'ı yerel dosyadan okur/yazar (GitHub
    Actions kendi checkout'undan okur, workflow'un commit adımı geri
    gönderir - github_config.py'nin belgelediği kural budur) ve
    premium-buy-portfolio-<username> watchlist'ini var olan sembollerle
    birleştirir. Etkileşimli (Streamlit) akış bunu ÇAĞIRMAZ - o, kullanıcının
    "Portföyü Kaydet" butonuna kadar hiçbir şeyi kalıcı yapmaz (Alım Bölgesi
    Tarama'nın Aktar akışıyla aynı davranış).

    Yeni sembollerin ağırlığı: toplam bütçenin, DAHA ÖNCE yüzdesi belirlenmiş
    (bu turda yeniden atanan semboller hariç) hisselere ayrılan kısmı
    düşüldükten sonra kalan payı, yeni sembol sayısına eşit bölerek (toplam
    bütçeye göre yüzde olarak) hesaplanır - premium_buy_portfolio.py'deki
    manuel Aktar akışıyla aynı mantık."""
    config_path = f"portfolio_config_{username}.json"
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {"budget": 0, "weights": {}}

    symbols = list(dict.fromkeys(r["symbol"] for r in selected_rows))
    n = len(symbols)

    budget = float(config.get("budget") or 0)
    if not budget:
        try:
            budget = float(client.get_account()["cash"])
        except Exception:
            budget = 0.0

    weights = dict(config.get("weights") or {})
    symbol_settings = dict(config.get("symbol_settings") or {})

    already_allocated_pct = sum(pct for sym, pct in weights.items() if sym not in symbols)
    remaining_pct = max(0.0, 100.0 - already_allocated_pct)
    new_weight_pct = round(remaining_pct / n, 2) if n else 0.0

    for row in selected_rows:
        symbol = row["symbol"]
        weights[symbol] = new_weight_pct
        symbol_settings[symbol] = {"algorithm": row["algorithm"], "timeframe": row["timeframe"]}

    config["budget"] = budget
    config["weights"] = weights
    config["symbol_settings"] = symbol_settings
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    watchlist_name = f"premium-buy-portfolio-{username}"
    watchlist = client.get_or_create_watchlist(watchlist_name)
    current_symbols = [a["symbol"] for a in watchlist.get("assets", [])]
    merged_symbols = list(dict.fromkeys(current_symbols + symbols))
    client.set_watchlist_symbols(watchlist["id"], merged_symbols)

    return config


def run_pipeline(username: str, client: AlpacaClient, cfg: dict) -> dict:
    """Günlük otomatik koşunun tam akışı: tara → momentum ile daralt →
    backtest → %10 üzeri kârlılığı filtrele → portföye birleştir. Özet bir
    dict döner (günlük script bunu config'e `last_run_summary` olarak yazar)."""
    universe = build_universe(
        username, cfg.get("include_nasdaq", True), cfg.get("include_nyse", True), cfg.get("custom_groups") or [],
    )
    signal_rows = scan_universe(
        client, universe, cfg.get("algorithms") or [DEFAULT_ALGORITHM_ID], cfg.get("timeframes") or ["1Day"],
    )
    candidates = narrow_by_momentum(
        client, signal_rows, cfg.get("max_candidates", DEFAULT_MAX_CANDIDATES),
        cfg.get("momentum_lookback_days", DEFAULT_MOMENTUM_LOOKBACK_DAYS),
    )
    cash_allocation = float(cfg.get("cash_allocation") or 0)

    backtest_results = run_backtests(client, candidates, cash_allocation, username)
    profitable = filter_profitable(backtest_results, cfg.get("min_backtest_profit_pct", DEFAULT_MIN_BACKTEST_PROFIT_PCT))

    if profitable:
        merge_into_portfolio(username, profitable, client)

    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "universe_size": len(universe),
        "scan_signal_count": len(signal_rows),
        "candidate_count": len(candidates),
        "backtest_results": backtest_results,
        "selected_symbols": [r["symbol"] for r in profitable],
    }
