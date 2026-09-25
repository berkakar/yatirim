"""Heikin Ashi Gün İçi modülünün paylaşılan pipeline'ı: evren oluşturma
(varsayılan NASDAQ 100 - otomatik_alim_satim_core.build_universe'i yeniden
kullanır) → likidite filtresi → buy_algorithms.heikin_ashi_stoch_signal ile
30 dakikalık barlarda alım adaylarını bulma → puanlama → boş pozisyon
slotları kadar en yüksek puanlıyı market emriyle alıp anında "Heikin Ashi
Çıkışı" stop'uyla (stop_algorithms.heikin_ashi_exit) koruma kurma.

Her yarım saatte bir (bkz. heikin_ashi_intraday_runner.py ve
.github/workflows/heikin_ashi_intraday.yml) şu sırayla çalışır:
  1. Seans kapanışına EOD_FLATTEN_MINUTES dakikadan az kaldıysa: modülün
     elindeki TÜM pozisyonlar market emriyle kapatılır (gün içi strateji -
     pozisyon geceye taşınmaz). Başka hiçbir şey yapılmaz.
  2. Elde tutulan her pozisyon için strateji çıkış kuralı KAPANMIŞ son 30
     dakikalık bara göre kontrol edilir (heikin_ashi.long_exit_reason: ilk
     kırmızı HA mumu ya da Stokastik %K > 80 iken %D'nin altına kesişim) -
     sinyal varsa resting stop iptal edilip market emriyle satılır.
  3. Kapanışa NO_NEW_ENTRY_MINUTES dakikadan az kalmadıysa, boş slotlar
     (max_positions - elde tutulan) yeni alımlarla doldurulur.

Bu modül Streamlit'e bağımlı DEĞİLDİR - orb_core.py ile aynı ilke.

Mimari kararlar orb_core.py'nin modül üstü notlarıyla birebir aynı:
  1. Streamlit sayfası (heikin_ashi_intraday.py) gerçek emir VERMEZ - sadece
     ayarları kaydeder ve scan_candidates() ile salt-okunur önizleme
     gösterir. Gerçek emirleri SADECE heikin_ashi_intraday_runner.py verir.
  2. Premium Buy Point watchlist'i, Relative Strength Rotasyonu'nun ve
     ORB'un elindeki semboller bu modülün evreninden HARİÇ TUTULUR; RS ve
     ORB de kendi taraflarında bu modülün elindekileri hariç tutar.
  3. Nakit TEK havuzdan: toplam canlı nakdin cash_allocation_pct'i bu
     modülündür - alpaca_buy_points.compute_available_cash_for_buying bu
     payı Premium Buy Point'in hesabından düşer. Pay max_positions slota
     eşit bölünür.
  4. Koruyucu stop'u alpaca_trailing_stop.py'nin 5 dakikalık botu yönetir
     (resolve_stop_algorithm_for_position bu modülün holdings'ini tanır) -
     "Heikin Ashi Çıkışı" stop'u çıkış sinyalinde stopu kapanışın hemen
     altına çeker. Adım 2'deki market çıkışı bunun üstüne, stratejinin çıkış
     kuralını bar kapanışında birebir uygular.
"""

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from alpaca_client import AlpacaClient
from alpaca_trailing_stop import get_bars_for_timeframe
from buy_algorithms import heikin_ashi_stoch_signal
from heikin_ashi import SMA_PERIOD, long_exit_reason, stochastic_series
from otomatik_alim_satim_core import DEFAULT_MIN_AVG_DOLLAR_VOLUME, build_universe, filter_by_liquidity
from stop_algorithms import STOP_ALGORITHMS, resolve_kwargs

TIMEFRAME = "30Min"
# SMA(50) 30 dakikalık barda ~4 işlem günü (günde 13 bar) - hafta sonu/tatil
# payıyla 10 takvim günü. HA_Open özyinelemeli olduğundan fazladan bar,
# son barların HA renklerini de stabilize eder.
BARS_LOOKBACK_DAYS = 10
DEFAULT_MAX_POSITIONS = 5
DEFAULT_CASH_ALLOCATION_PCT = 0.0  # opt-in - bkz. orb_core.DEFAULT_CASH_ALLOCATION_PCT
EOD_FLATTEN_MINUTES = 20
NO_NEW_ENTRY_MINUTES = 60
ORDER_TAG_PREFIX = "hai"
HA_INTRADAY_DEFAULT_STOP_ALGORITHM = "heikin_ashi_exit"


def config_path(username: str) -> str:
    return f"ha_intraday_config_{username}.json"


def holdings_path(username: str) -> str:
    return f"ha_intraday_holdings_{username}.json"


def load_config_local(username: str) -> dict:
    path = config_path(username)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_holdings_local(username: str) -> dict:
    path = holdings_path(username)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_holdings_local(username: str, holdings: dict) -> None:
    with open(holdings_path(username), "w", encoding="utf-8") as f:
        json.dump(holdings, f, ensure_ascii=False, indent=2)


def get_cash_allocation_pct(username: str) -> float:
    """alpaca_buy_points.compute_available_cash_for_buying için - bkz.
    orb_core.get_cash_allocation_pct ile aynı desen."""
    cfg = load_config_local(username)
    if not cfg.get("enabled"):
        return 0.0
    return float(cfg.get("cash_allocation_pct") or 0.0)


def resolve_stop_algorithm(cfg: dict) -> str:
    algo = cfg.get("stop_algorithm") or HA_INTRADAY_DEFAULT_STOP_ALGORITHM
    return algo if algo in STOP_ALGORITHMS else HA_INTRADAY_DEFAULT_STOP_ALGORITHM


def compute_score(bars: list) -> float | None:
    """Adayları sıralamak için alım puanı: Stokastik %K-%D farkı (dönüşün
    gücü) + kapanışın SMA50'nin yüzde kaç üstünde olduğu (trendin gücü).
    Sadece heikin_ashi_stoch_signal zaten sinyal ürettiğinde anlamlıdır."""
    if len(bars) < SMA_PERIOD:
        return None
    k, d = stochastic_series(bars[-16:])
    if k[-1] is None or d[-1] is None:
        return None
    sma = sum(b.c for b in bars[-SMA_PERIOD:]) / SMA_PERIOD
    trend_pct = (bars[-1].c - sma) / sma * 100
    return round((k[-1] - d[-1]) + trend_pct, 4)


@dataclass(frozen=True)
class HaCandidate:
    symbol: str
    price: float
    score: float
    reason: str


def _fetch_bars(client: AlpacaClient, symbol: str) -> list:
    start = datetime.now(timezone.utc) - timedelta(days=BARS_LOOKBACK_DAYS)
    return get_bars_for_timeframe(client, symbol, TIMEFRAME, start, exclude_forming=True)


def scan_candidates(client: AlpacaClient, universe: list[str]) -> list[HaCandidate]:
    """Salt-okunur: emir vermez, dosya yazmaz. Evrendeki her sembol için
    kapanmış 30 dakikalık barlarda alım sinyali var mı bakar, puana göre
    büyükten küçüğe sıralı döner."""
    candidates = []
    for symbol in universe:
        try:
            bars = _fetch_bars(client, symbol)
        except Exception:
            continue
        if not bars:
            continue
        signal = heikin_ashi_stoch_signal(bars)
        if signal is None:
            continue
        score = compute_score(bars)
        if score is None:
            continue
        candidates.append(HaCandidate(symbol=symbol, price=signal.price, score=score, reason=signal.reason))
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def minutes_to_close(clock: dict) -> float:
    next_close = datetime.fromisoformat(clock["next_close"].replace("Z", "+00:00"))
    return (next_close - datetime.now(timezone.utc)).total_seconds() / 60


def _sell(client: AlpacaClient, symbol: str, kind: str) -> float | None:
    """Pozisyonu market emriyle kapatır (önce resting stop iptal edilir -
    wash-trade koruması, bkz. relative_strength_core.rebalance). Pozisyon
    zaten yoksa (ör. stop tetiklendi) None döner."""
    position = client.get_position(symbol)
    if position is None:
        return None
    qty = float(position["qty"])
    stop_order = client.get_open_stop_order(symbol)
    if stop_order is not None:
        client.cancel_order(stop_order["id"])
    tag = f"{ORDER_TAG_PREFIX}-{kind}-{symbol}-{int(datetime.now(timezone.utc).timestamp())}"
    client.place_market_exit(symbol, qty, client_order_id=tag)
    return qty


def _prune_closed(client: AlpacaClient, holdings: dict) -> dict:
    """Stopu tetiklenmiş (artık gerçek pozisyonu olmayan) sembolleri düşürür."""
    if not holdings:
        return holdings
    try:
        live_symbols = {p["symbol"] for p in client.get_all_positions()}
    except Exception:
        return holdings
    return {s: info for s, info in holdings.items() if s in live_symbols}


def run_pass(client: AlpacaClient, username: str, cfg: dict, stop_settings: dict | None = None) -> dict:
    """Gerçek emir veren TEK fonksiyon - SADECE heikin_ashi_intraday_runner.py
    çağırır. Adımlar modül üstü notta."""
    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cash_allocation_pct = float(cfg.get("cash_allocation_pct") or DEFAULT_CASH_ALLOCATION_PCT)
    holdings = _prune_closed(client, load_holdings_local(username))
    summary = {"run_at": run_at, "skipped": False, "sold": [], "bought": [], "errors": []}

    clock = client.get_clock()
    remaining = minutes_to_close(clock)

    # 1. Gün sonu: her şeyi kapat. Modül devre dışı bırakılmış olsa bile elde
    # kalan pozisyonlar geceye taşınmasın diye enabled kontrolünden ÖNCE.
    if remaining <= EOD_FLATTEN_MINUTES:
        for symbol in list(holdings):
            try:
                _sell(client, symbol, "eod")
                summary["sold"].append(f"{symbol} (gün sonu)")
            except Exception as e:
                summary["errors"].append(f"{symbol}: gün sonu satışı başarısız: {e}")
                continue
            holdings.pop(symbol, None)
        save_holdings_local(username, holdings)
        summary["phase"] = "eod_flatten"
        return summary

    if not cfg.get("enabled") or cash_allocation_pct <= 0:
        save_holdings_local(username, holdings)
        return {**summary, "skipped": True, "reason": "devre dışı ya da nakit payı 0"}

    # 2. Strateji çıkış kuralı (kapanmış son bar).
    for symbol in list(holdings):
        try:
            reason = long_exit_reason(_fetch_bars(client, symbol))
        except Exception as e:
            summary["errors"].append(f"{symbol}: çıkış kontrolü başarısız: {e}")
            continue
        if reason is None:
            continue
        try:
            _sell(client, symbol, "exit")
            summary["sold"].append(f"{symbol} ({reason})")
            holdings.pop(symbol, None)
        except Exception as e:
            summary["errors"].append(f"{symbol}: çıkış satışı başarısız: {e}")

    # 3. Yeni alımlar.
    max_positions = int(cfg.get("max_positions") or DEFAULT_MAX_POSITIONS)
    free_slots = max_positions - len(holdings)
    if remaining <= NO_NEW_ENTRY_MINUTES or free_slots <= 0:
        save_holdings_local(username, holdings)
        summary["phase"] = "exits_only"
        return summary

    stop_algorithm = resolve_stop_algorithm(cfg)
    stop_algo = STOP_ALGORITHMS[stop_algorithm]
    stop_settings = stop_settings or {}
    stop_shared_settings = stop_settings.get("shared") or {}
    stop_algo_settings = stop_settings.get(stop_algorithm) or {}

    universe = build_universe(
        username, cfg.get("include_nasdaq", True), cfg.get("include_nyse", False),
        cfg.get("custom_groups") or [], cfg.get("include_russell", False),
    )
    universe = [t for t in universe if t not in excluded_symbols(client, username) and t not in holdings]
    universe = filter_by_liquidity(client, universe, cfg.get("min_avg_dollar_volume", DEFAULT_MIN_AVG_DOLLAR_VOLUME))
    candidates = scan_candidates(client, universe)
    selected = candidates[:free_slots]

    account_cash = float(client.get_account()["cash"])
    target_per_position = account_cash * (cash_allocation_pct / 100) / max_positions
    # Elde tutulanların giriş maliyeti bu modülün payından düşülür.
    used = sum(float(i.get("qty", 0)) * float(i.get("entry_price", 0)) for i in holdings.values())
    available_cash = max(0.0, min(account_cash, account_cash * cash_allocation_pct / 100 - used))

    for cand in selected:
        try:
            dollar_amount = min(target_per_position, available_cash)
            qty = math.floor(dollar_amount / cand.price) if cand.price > 0 else 0
            if qty <= 0:
                summary["errors"].append(f"{cand.symbol}: hedef tutar (${dollar_amount:.2f}) 1 adet için yetersiz")
                continue
            tag = f"{ORDER_TAG_PREFIX}-buy-{cand.symbol}-{int(datetime.now(timezone.utc).timestamp())}"
            order = client.place_market_entry(cand.symbol, qty, "long", client_order_id=tag)
            filled = client.wait_for_fill(order["id"], timeout=30)
            fill_price = float(filled["filled_avg_price"])
            filled_qty = float(filled["filled_qty"])
            available_cash -= filled_qty * fill_price

            try:
                stop_bars = _fetch_bars(client, cand.symbol)
            except Exception:
                stop_bars = None
            stop_price = round(stop_algo.initial_stop(
                fill_price, "long", bars=stop_bars,
                **resolve_kwargs(stop_algo.initial_stop, stop_algo_settings, stop_shared_settings),
            ), 2)
            try:
                client.place_stop_order(cand.symbol, filled_qty, "long", stop_price)
            except Exception as e:
                summary["errors"].append(f"{cand.symbol}: alındı (@ {fill_price:.2f}) ama stop kurulamadı, KORUMASIZ: {e}")

            holdings[cand.symbol] = {
                "qty": filled_qty, "entry_price": fill_price, "stop_price": stop_price,
                "entered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "score": cand.score,
            }
            summary["bought"].append(cand.symbol)
        except Exception as e:
            summary["errors"].append(f"{cand.symbol}: {e}")

    save_holdings_local(username, holdings)
    summary.update({
        "phase": "full",
        "universe_size": len(universe),
        "candidate_count": len(candidates),
        "selected_symbols": [c.symbol for c in selected],
    })
    return summary


def excluded_symbols(client: AlpacaClient, username: str) -> set[str]:
    """Premium Buy Point watchlist'i + Relative Strength Rotasyonu'nun ve
    ORB'un elindekiler (bkz. modül üstü not #2)."""
    try:
        pbp_watchlist = client.get_watchlist_by_name(f"premium-buy-portfolio-{username}")
        pbp_symbols = {a["symbol"] for a in pbp_watchlist["assets"]} if pbp_watchlist else set()
    except Exception:
        pbp_symbols = set()
    # Fonksiyon içi import: döngüsel import notu için bkz. orb_core.scan_and_buy.
    from orb_core import load_holdings_local as load_orb_holdings
    from relative_strength_core import load_holdings_local as load_rs_holdings
    return pbp_symbols | set(load_rs_holdings(username)) | set(load_orb_holdings(username))
