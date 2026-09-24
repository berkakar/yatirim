"""Açılış Aralığı Kırılımı (ORB) modülünün paylaşılan pipeline'ı: evren
oluşturma (varsayılan Russell 2000 - otomatik_alim_satim_core.build_universe'i
yeniden kullanır) → likidite filtresi → buy_algorithms.orb_signal ile
kırılım adaylarını bulma → her adaya bir "alım puanı" (hacim çarpanı +
kırılım yüzdesi) verme → en yüksek puanlı top_n'i seçme → market emriyle
alıp anında (yapısal Açılış Aralığı stop'uyla) koruma kurma.

Bu modül Streamlit'e bağımlı DEĞİLDİR - relative_strength_core.py /
otomatik_alim_satim_core.py ile aynı ilke.

ÖNEMLİ - mimari kararlar:
  1. Bu sistemdeki her Streamlit sayfası gerçek Alpaca emri YERLEŞTİRMEZ
     (bkz. relative_strength_core.py'nin modül üstü notu #1, aynı ilke) -
     scan_and_buy() (gerçek emir veren tek fonksiyon) SADECE
     orb_scan_runner.py tarafından çağrılır. Streamlit sayfası sadece
     scan_candidates() ile salt-okunur bir ÖNİZLEME gösterir.
  2. Premium Buy Point'in watchlist'i VE Relative Strength Rotasyonu'nun
     elindeki semboller bu modülün evreninden HARİÇ TUTULUR - üç bağımsız
     sistemin aynı sembolde çakışmaması için (relative_strength_core.py'nin
     modül üstü notu #2 ile aynı gerekçe, artık üç yönlü). Relative Strength
     Rotasyonu da kendi tarafında bu modülün elindeki sembolleri hariç
     tutuyor (bkz. o modüldeki karşılıklı koruma).
  3. "Toplam nakit" YİNE TEK bir referans (relative_strength_core.py'nin
     modül üstü notu #3 ile birebir aynı ilke): Alpaca hesabının gerçek
     canlı nakti. alpaca_buy_points.compute_available_cash_for_buying artık
     HEM Relative Strength Rotasyonu'nun HEM bu modülün payını kendi
     hesabından düşüyor - üç modül de TEK bir nakit havuzundan besleniyor.
  4. ORB, Relative Strength Rotasyonu'nun aksine bir ROTASYON stratejisi
     DEĞİL: her gün yeni adaylar aranıp alınır, ama var olan bir pozisyon
     "artık en yüksek puanlı değil" diye SATILMAZ - tek çıkış mekanizması
     alım anında kurulan stop-loss (alpaca_trailing_stop.py'nin 5 dakikada
     bir çalışan botu tarafından sürekli sıkılaştırılıyor). Zaten pozisyonu
     olan bir sembol aday listesinden HARİÇ TUTULUR - taze bir kırılımı
     yakalamak bu modülün işi, mevcut bir pozisyonu büyütmek değil.
  5. stop_algorithms.opening_range_initial_stop tam olarak bu modül için
     tasarlandı (bkz. o fonksiyonun docstring'i) - Relative Strength
     Rotasyonu'nun aksine burada `bars` GERÇEKTEN initial_stop()'a
     geçiriliyor, o yüzden yapısal (seansın açılış barının ters ucu)
     davranışı GERÇEKTEN devreye giriyor, sabit yüzdeye düşmüyor.
"""

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from alpaca_client import AlpacaClient
from alpaca_trailing_stop import get_bars_for_timeframe
from buy_algorithms import orb_signal
from otomatik_alim_satim_core import DEFAULT_MIN_AVG_DOLLAR_VOLUME, build_universe, filter_by_liquidity
from stop_algorithms import STOP_ALGORITHMS, resolve_kwargs

DEFAULT_TOP_N = 5
DEFAULT_TIMEFRAME = "15Min"
DEFAULT_VOLUME_MULT = 1.5
DEFAULT_MAX_BARS_AFTER_OPEN = 4
DEFAULT_CASH_ALLOCATION_PCT = 0.0  # opt-in: kullanıcı elle bir pay ayırana kadar 0 - diğer modüller tüm nakti kullanmaya devam eder
ORDER_TAG_PREFIX = "orb"

# Bu modülün KENDİ varsayılanı - RS Rotasyonu'nun ROTATION_DEFAULT_STOP_ALGORITHM'ı
# ile AYNI gerekçeyle ayrı bir sabit (bkz. relative_strength_core.py), ama
# DEĞERİ farklı: burada "Açılış Aralığı (ORB) Stop" GERÇEKTEN anlamlı (bkz.
# modül üstü not #5) - RS Rotasyonu'nda olduğu gibi sessizce sabit yüzdeye
# düşmüyor, çünkü scan_and_buy() initial_stop()'a `bars` geçiriyor.
ORB_DEFAULT_STOP_ALGORITHM = "opening_range"


def config_path(username: str) -> str:
    return f"orb_scan_config_{username}.json"


def holdings_path(username: str) -> str:
    return f"orb_scan_holdings_{username}.json"


def load_config_local(username: str) -> dict:
    """GitHub Actions script'lerinin (orb_scan_runner.py, alpaca_buy_points.py,
    alpaca_trailing_stop.py) kullandığı yerel okuma - bkz. relative_strength_core.
    load_config_local ile aynı gerekçe (GH Actions her koşuda repoyu
    sıfırdan checkout eder, dosya zaten güncel)."""
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
    """alpaca_buy_points.compute_available_cash_for_buying'in Premium Buy
    Point'e ayırdığı nakti hesaplarken bu modülün payını düşebilmesi için -
    relative_strength_core.get_cash_allocation_pct ile aynı desen/gerekçe."""
    cfg = load_config_local(username)
    if not cfg.get("enabled"):
        return 0.0
    return float(cfg.get("cash_allocation_pct") or 0.0)


def resolve_stop_algorithm(cfg: dict) -> str:
    algo = cfg.get("stop_algorithm") or ORB_DEFAULT_STOP_ALGORITHM
    return algo if algo in STOP_ALGORITHMS else ORB_DEFAULT_STOP_ALGORITHM


def compute_orb_score(bars: list, max_bars_after_open: int = DEFAULT_MAX_BARS_AFTER_OPEN) -> float | None:
    """SADECE orb_signal zaten geçerli bir kırılım sinyali ürettiğinde
    (bu fonksiyon çağrılmadan önce None dönmediğinde) anlamlıdır - adaylar
    arasında sıralamak için bir "alım puanı" hesaplar: hacim çarpanı
    (kırılım barının hacmi / açılış barının hacmi - literatürdeki RVOL
    vurgusu) + kırılım yüzdesi (kapanışın açılış aralığı üstünü ne kadar
    aştığı) toplamı. orb_signal'ın kendi iç mantığını (session_bars/
    opening_bar bulma) KASITLI olarak küçük ölçekte tekrar eder - orb_signal'ın
    imzasını/davranışını değiştirmemek için ayrı, bağımsız bir fonksiyon."""
    if not bars:
        return None
    last = bars[-1]
    last_date = last.t[:10]
    session_bars = [b for b in bars if b.t[:10] == last_date]
    if not session_bars:
        return None
    opening_bar = session_bars[0]
    if opening_bar.v <= 0 or opening_bar.h <= 0:
        return None
    volume_ratio = last.v / opening_bar.v
    breakout_pct = (last.c - opening_bar.h) / opening_bar.h * 100
    return round(volume_ratio + breakout_pct, 4)


@dataclass(frozen=True)
class OrbCandidate:
    symbol: str
    price: float
    score: float
    reason: str


def scan_candidates(
    client: AlpacaClient, universe: list[str], timeframe: str = DEFAULT_TIMEFRAME,
    volume_mult: float = DEFAULT_VOLUME_MULT, max_bars_after_open: int = DEFAULT_MAX_BARS_AFTER_OPEN,
    lookback_days: int = 2,
) -> list[OrbCandidate]:
    """Salt-okunur: hiçbir emir vermez, hiçbir dosya yazmaz - Streamlit
    sayfasının önizlemesi VE scan_and_buy()'ın ilk adımı olarak kullanılır.
    Evrendeki her sembol için son `lookback_days` günün barlarını çeker,
    orb_signal ile geçerli bir kırılım var mı bakar, varsa compute_orb_score
    ile puanlar. Puana göre BÜYÜKTEN KÜÇÜĞE sıralı liste döner (henüz
    top_n'e kesilmemiş - bkz. select_top_candidates)."""
    start = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    candidates = []
    for symbol in universe:
        try:
            bars = get_bars_for_timeframe(client, symbol, timeframe, start, exclude_forming=True)
        except Exception:
            continue
        if not bars:
            continue
        signal = orb_signal(bars, volume_mult=volume_mult, max_bars_after_open=max_bars_after_open)
        if signal is None:
            continue
        score = compute_orb_score(bars, max_bars_after_open)
        if score is None:
            continue
        candidates.append(OrbCandidate(symbol=symbol, price=signal.price, score=score, reason=signal.reason))
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def select_top_candidates(candidates: list[OrbCandidate], top_n: int = DEFAULT_TOP_N) -> list[OrbCandidate]:
    return candidates[:top_n]


def compute_available_cash_for_scan(client: AlpacaClient, cash_allocation_pct: float) -> float:
    """alpaca_buy_points.compute_available_cash_for_buying /
    relative_strength_core.compute_available_cash_for_rotation ile aynı
    desen - bu modülün payına (toplam canlı nakdin cash_allocation_pct'i)
    düşen tutardan, bu modülün hâlâ açık/bekleyen ("orb-" etiketli)
    buy-limit emirlerinin tutarını düşer."""
    cash = float(client.get_account()["cash"]) * (cash_allocation_pct / 100)
    reserved = sum(
        float(o["qty"]) * float(o["limit_price"])
        for o in client.get_open_orders()
        if o["type"] == "limit" and o["side"] == "buy"
        and (o.get("client_order_id") or "").startswith(f"{ORDER_TAG_PREFIX}-")
    )
    return max(0.0, cash - reserved)


def scan_and_buy(client: AlpacaClient, username: str, cfg: dict, stop_settings: dict | None = None) -> dict:
    """Gerçek emir veren TEK fonksiyon - SADECE orb_scan_runner.py tarafından
    çağrılır (bkz. modül üstü not #1). Akış: evren oluştur (Premium Buy
    Point watchlist'i + Relative Strength Rotasyonu'nun elindekiler + bu
    modülün zaten sahip olduğu semboller hariç) → likidite filtrele →
    kırılım adaylarını bul+puanla → en yüksek top_n'i seç → market emriyle
    al, anında (bars geçirilerek gerçekten yapısal) stop kur."""
    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cash_allocation_pct = float(cfg.get("cash_allocation_pct") or DEFAULT_CASH_ALLOCATION_PCT)
    if not cfg.get("enabled") or cash_allocation_pct <= 0:
        return {"run_at": run_at, "skipped": True, "reason": "devre dışı ya da nakit payı 0"}

    top_n = int(cfg.get("top_n") or DEFAULT_TOP_N)
    timeframe = cfg.get("timeframe") or DEFAULT_TIMEFRAME
    volume_mult = float(cfg.get("volume_mult") or DEFAULT_VOLUME_MULT)
    max_bars_after_open = int(cfg.get("max_bars_after_open") or DEFAULT_MAX_BARS_AFTER_OPEN)
    stop_algorithm = resolve_stop_algorithm(cfg)
    stop_algo = STOP_ALGORITHMS[stop_algorithm]
    stop_settings = stop_settings or {}
    stop_shared_settings = stop_settings.get("shared") or {}
    stop_algo_settings = stop_settings.get(stop_algorithm) or {}

    # Stopu tetiklenmiş (artık gerçek pozisyonu olmayan) semboller durumdan
    # düşürülür - aksi halde o sembol kalıcı olarak "elimde var" sayılıp bir
    # daha asla yeniden taranamazdı (bkz. modül üstü not #4).
    own_holdings = load_holdings_local(username)
    if own_holdings:
        try:
            live_symbols = {p["symbol"] for p in client.get_all_positions()}
        except Exception:
            live_symbols = set(own_holdings.keys())  # emin olamıyorsak elimizdekine güven, yanlışlıkla tekrar taramaya açma
        own_holdings = {s: info for s, info in own_holdings.items() if s in live_symbols}

    universe = build_universe(
        username, cfg.get("include_nasdaq", False), cfg.get("include_nyse", False),
        cfg.get("custom_groups") or [], cfg.get("include_russell", True),
    )
    try:
        pbp_watchlist = client.get_watchlist_by_name(f"premium-buy-portfolio-{username}")
        pbp_symbols = {a["symbol"] for a in pbp_watchlist["assets"]} if pbp_watchlist else set()
    except Exception:
        pbp_symbols = set()
    # Fonksiyon içi import: relative_strength_core -> otomatik_alim_satim_core
    # -> alpaca_trailing_stop -> orb_core/relative_strength_core döngüsünü
    # kırmak için (bkz. alpaca_trailing_stop.py'deki aynı notlar).
    from relative_strength_core import load_holdings_local as load_rs_holdings
    rs_symbols = set(load_rs_holdings(username).keys())
    already_held = set(own_holdings.keys())
    universe = [t for t in universe if t not in pbp_symbols and t not in rs_symbols and t not in already_held]
    universe = filter_by_liquidity(client, universe, cfg.get("min_avg_dollar_volume", DEFAULT_MIN_AVG_DOLLAR_VOLUME))

    candidates = scan_candidates(client, universe, timeframe, volume_mult, max_bars_after_open)
    selected = select_top_candidates(candidates, top_n)

    total_orb_cash = float(client.get_account()["cash"]) * (cash_allocation_pct / 100)
    target_per_position = total_orb_cash / top_n if top_n > 0 else 0.0
    available_cash = compute_available_cash_for_scan(client, cash_allocation_pct)

    bought: list[str] = []
    buy_errors: list[str] = []
    holdings = own_holdings
    start = datetime.now(timezone.utc) - timedelta(days=2)
    for cand in selected:
        try:
            dollar_amount = min(target_per_position, available_cash)
            if dollar_amount <= 0:
                buy_errors.append(f"{cand.symbol}: kullanılabilir nakit yetersiz")
                continue
            qty = math.floor(dollar_amount / cand.price) if cand.price > 0 else 0
            if qty <= 0:
                buy_errors.append(f"{cand.symbol}: hedef tutar (${dollar_amount:.2f}) 1 adet için yetersiz")
                continue

            tag = f"{ORDER_TAG_PREFIX}-buy-{cand.symbol}-{int(datetime.now(timezone.utc).timestamp())}"
            buy_order = client.place_market_entry(cand.symbol, qty, "long", client_order_id=tag)
            filled = client.wait_for_fill(buy_order["id"], timeout=30)
            fill_price = float(filled["filled_avg_price"])
            filled_qty = float(filled["filled_qty"])
            available_cash -= filled_qty * fill_price

            # Stop için bars TEKRAR çekiliyor (scan_candidates zaten çekmişti
            # ama saklamadı - sadelik için; en fazla top_n=birkaç sembol için
            # ekstra bir API çağrısı, önemsiz maliyet).
            try:
                stop_bars = get_bars_for_timeframe(client, cand.symbol, timeframe, start, exclude_forming=True)
            except Exception:
                stop_bars = None

            stop_price = round(stop_algo.initial_stop(
                fill_price, "long", bars=stop_bars,
                **resolve_kwargs(stop_algo.initial_stop, stop_algo_settings, stop_shared_settings),
            ), 2)
            try:
                client.place_stop_order(cand.symbol, filled_qty, "long", stop_price)
            except Exception as e:
                buy_errors.append(f"{cand.symbol}: alındı (@ {fill_price:.2f}) ama stop kurulamadı, KORUMASIZ: {e}")

            holdings[cand.symbol] = {
                "qty": filled_qty, "entry_price": fill_price,
                "entered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "score": cand.score,
            }
            bought.append(cand.symbol)
        except Exception as e:
            buy_errors.append(f"{cand.symbol}: {e}")

    save_holdings_local(username, holdings)

    return {
        "run_at": run_at,
        "skipped": False,
        "universe_size": len(universe),
        "candidate_count": len(candidates),
        "selected_symbols": [c.symbol for c in selected],
        "bought": bought,
        "buy_errors": buy_errors,
        "cash_allocation_pct": cash_allocation_pct,
        "top_n": top_n,
    }
