"""Relative Strength Rotasyonu modülünün paylaşılan pipeline'ı: evren oluşturma
(otomatik_alim_satim_core.build_universe'i yeniden kullanır) → likidite
filtresi → her sembol için relative strength skoru (basit N-haftalık getiri)
→ en güçlü top_n'i seçme → mevcut RS pozisyonlarıyla karşılaştırıp
sıralamadan düşenleri satma / yeni girenleri market emriyle alma.

Bu modül Streamlit'e bağımlı DEĞİLDİR - otomatik_alim_satim_core.py ile aynı
ilke, hem Streamlit sayfası (relative_strength.py, SADECE önizleme/ayarlar
için - bkz. aşağı) hem de günlük GitHub Actions script'i
(relative_strength_runner.py, gerçek emirleri veren tek yer) tarafından
import edilir.

ÖNEMLİ - mimari kararlar:
  1. Bu sistemdeki her Streamlit sayfası (premium_buy_portfolio.py,
     otomatik_alim_satim.py) gerçek Alpaca emri YERLEŞTİRMEZ - sadece
     config/watchlist'i ayarlar, gerçek emirleri ayrı zamanlanmış GitHub
     Actions script'leri verir. Bu modül de aynı deseni izler: rebalance()
     (gerçek emir veren tek fonksiyon) SADECE relative_strength_runner.py
     tarafından çağrılır. Streamlit sayfası sadece plan_rebalance() ile
     salt-okunur bir ÖNİZLEME gösterir.
  2. Premium Buy Point'in kendi watchlist'indeki VE Açılış Aralığı Kırılımı
     (ORB) modülünün elindeki semboller bu modülün evreninden HARİÇ TUTULUR
     (bkz. rebalance()) - aksi halde aynı sembolde bağımsız sistemlerin
     (biri fiyat-stop'una, biri sıralamaya, biri kırılım/stop'a göre satmaya
     çalıştığı) birbirinden habersiz pozisyon yönetimi çakışması olurdu.
     ORB da kendi tarafında (orb_core.scan_and_buy) bu modülün elindeki
     sembolleri hariç tutuyor - karşılıklı koruma. Kullanıcı bu modülün
     elinde tuttuğu bir sembolü sonradan elle Premium Buy Point
     watchlist'ine eklerse bu koruma o an için işe yaramaz - bilinen bir
     sınır, elle eklememeniz önerilir.
  3. "Toplam nakit" tek bir referans: Alpaca hesabının GERÇEK canlı nakti
     (client.get_account()["cash"]) - alpaca_buy_points.
     compute_available_cash_for_buying'in KULLANDIĞI AYNI değer. Bu modülün
     cash_allocation_pct'i o değerin bir yüzdesini bu modüle AYIRIR;
     alpaca_buy_points.py bu payı kendi hesabından DÜŞER (bkz. o dosyadaki
     get_cash_allocation_pct kullanımı) - böylece iki modül asla aynı
     dolarları iki kez harcamaya çalışmaz, ikisi de TEK bir nakit havuzundan
     besleniyor olur.
  4. RS'nin hangi sembolleri "kendi elinde tuttuğu" Alpaca'nın pozisyon
     API'sinde YOK (pozisyonlar hangi stratejiye ait olduğunu bilmez) - bu
     yüzden ayrı bir durum dosyası (relative_strength_holdings_<kullanıcı>.
     json) tutuluyor. Kaynak-doğruluk yine de CANLI Alpaca pozisyonları:
     bu dosyada olup Alpaca'da artık pozisyonu olmayan bir sembol (ör.
     alpaca_trailing_stop.py'nin güvenlik ağı stop'u tetiklendiyse) sessizce
     durumdan düşürülür, hata sayılmaz - bkz. plan_rebalance().
  5. Yeni alınan her pozisyona ANINDA bir stop kuruluyor (seçili
     stop_algorithm ile) - rebalance haftalık olduğundan, iki rebalans
     arasında fiyat çökerse pozisyon korumasız kalmasın diye. Sıralamadan
     düşme (rank-based exit) ASIL çıkış disiplinidir, fiyat stop'u sadece
     bir güvenlik ağıdır.
"""

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from alpaca_client import AlpacaClient
from otomatik_alim_satim_core import DEFAULT_MIN_AVG_DOLLAR_VOLUME, build_universe, filter_by_liquidity
from stop_algorithms import STOP_ALGORITHMS, resolve_kwargs

DEFAULT_TOP_N = 10
DEFAULT_LOOKBACK_WEEKS = 8
DEFAULT_MIN_SCORE_PCT = 0.0  # mutlak momentum filtresi (yüzde) - bu eşiğin altındaki skor asla seçilmez
DEFAULT_CASH_ALLOCATION_PCT = 0.0  # opt-in: kullanıcı elle bir pay ayırana kadar 0 - PBP tüm nakti kullanmaya devam eder
ORDER_TAG_PREFIX = "rs"

# Bu modülün KENDİ varsayılanı - stop_algorithms.DEFAULT_STOP_ALGORITHM'dan
# BİLEREK ayrı bir sabit: o sabit ileride (PBP/BackTest tarafında) değişirse
# bu strateji sessizce farklı bir algoritmaya kaymasın diye. "Açılış Aralığı
# (ORB) Stop" bu strateji için ANLAMSIZ - rebalance() giriş fiyatını
# initial_stop()'a `bars` OLMADAN geçirir (RS haftanın herhangi bir günü/
# saatinde alım yapabildiğinden "seansın açılış barı" kavramı yok), o yüzden
# seçilse bile sessizce sabit yüzdelik yedek stop'a düşer - yapısal avantajı
# hiç kullanılmaz. "Beklemeli ve İz Süren Stop" de teknik olarak çalışır ama
# kâr eşiğine kadar trail'i geciktirdiğinden, haftalık rebalans arasındaki
# güvenlik ağı rolü için "Breakeven + Yapısal Trail" (ATR-tamponlu trail
# hemen devrede) daha uygun - bu yüzden varsayılan bu.
ROTATION_DEFAULT_STOP_ALGORITHM = "breakeven_atr_structure"


def config_path(username: str) -> str:
    return f"relative_strength_config_{username}.json"


def holdings_path(username: str) -> str:
    return f"relative_strength_holdings_{username}.json"


def load_config_local(username: str) -> dict:
    """GitHub Actions script'lerinin (relative_strength_runner.py,
    alpaca_buy_points.py, alpaca_trailing_stop.py) kullandığı yerel okuma -
    GH Actions her koşuda repoyu sıfırdan checkout ettiğinden dosya zaten
    güncel, GitHub API'sine gerek yok (bkz. alpaca_trailing_stop.
    load_portfolio_config ile aynı desen)."""
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
    Point'e ayırdığı nakti hesaplarken bu modülün payını düşebilmesi için.
    Modül hiç yapılandırılmamışsa YA DA "enabled" değilse 0 döner - bu
    durumda gerçek emri asla bu modül vermeyeceğinden (bkz. modül üstü not
    #1) PBP'nin payını kısıtlamaya gerek yok, davranış RS Rotasyonu'nu hiç
    açmamış bir kullanıcı için ESKİSİYLE TAMAMEN AYNI kalır."""
    cfg = load_config_local(username)
    if not cfg.get("enabled"):
        return 0.0
    return float(cfg.get("cash_allocation_pct") or 0.0)


def resolve_stop_algorithm(cfg: dict) -> str:
    """Kaydedilmiş bir seçim yoksa ya da geçersizse ROTATION_DEFAULT_STOP_ALGORITHM'a
    (breakeven_atr_structure) düşer - bkz. o sabitin üstündeki not."""
    algo = cfg.get("stop_algorithm") or ROTATION_DEFAULT_STOP_ALGORITHM
    return algo if algo in STOP_ALGORITHMS else ROTATION_DEFAULT_STOP_ALGORITHM


def compute_relative_strength_scores(
    client: AlpacaClient, tickers: list[str], lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
) -> dict[str, float]:
    """Her sembol için basit relative strength skoru: son `lookback_weeks`
    haftalık pencerenin İLK ve SON günlük kapanışı arasındaki yüzdesel
    getiri. Veri çekilemeyen ya da yetersiz (2'den az bar) sembol skora
    dahil edilmez (elenmiş sayılır, hata değil)."""
    start = datetime.now(timezone.utc) - timedelta(weeks=lookback_weeks, days=5)  # hafta sonu/tatil payı
    scores: dict[str, float] = {}
    for ticker in tickers:
        try:
            raw = client.get_raw_bars(ticker, "1Day", start.isoformat())
        except Exception:
            continue
        if len(raw) < 2:
            continue
        first_close, last_close = raw[0]["c"], raw[-1]["c"]
        if first_close <= 0:
            continue
        scores[ticker] = (last_close - first_close) / first_close
    return scores


def rank_and_select(
    scores: dict[str, float], top_n: int = DEFAULT_TOP_N, min_score_pct: float = DEFAULT_MIN_SCORE_PCT,
) -> list[str]:
    """En yüksek skordan başlayarak en fazla top_n sembol - min_score_pct/100
    altında kalan skorlar (mutlak momentum negatifse) hiç aday sayılmaz,
    top_n'e ulaşılamasa bile eklenmez (dual momentum: göreceli GÜÇLÜ olmak
    yetmez, mutlak olarak da pozitif olmalı)."""
    threshold = min_score_pct / 100
    eligible = [(t, s) for t, s in scores.items() if s >= threshold]
    eligible.sort(key=lambda pair: pair[1], reverse=True)
    return [t for t, _ in eligible[:top_n]]


@dataclass(frozen=True)
class RebalancePlan:
    universe_size: int
    target_symbols: list[str]  # yeni top_n (sıralı, en güçlüden başlayarak)
    to_sell: list[str]
    to_buy: list[str]
    to_hold: list[str]
    scores: dict[str, float] = field(default_factory=dict)


def plan_rebalance(
    client: AlpacaClient, username: str, universe: list[str],
    top_n: int = DEFAULT_TOP_N, lookback_weeks: int = DEFAULT_LOOKBACK_WEEKS,
    min_score_pct: float = DEFAULT_MIN_SCORE_PCT,
) -> RebalancePlan:
    """Salt-okunur: hiçbir emir vermez, hiçbir dosya yazmaz - Streamlit
    sayfasının önizlemesi VE rebalance()'ın ilk adımı olarak kullanılır.

    "Şu an gerçekten elimde ne var" sorusunun kaynağı, durum dosyasındaki
    kayıt DEĞİL canlı Alpaca pozisyonlarıdır: durum dosyasında bir sembol
    olup Alpaca'da artık pozisyonu yoksa (ör. alpaca_trailing_stop.py'nin
    güvenlik ağı stop'u tetiklenmiş olabilir), o sembol "elimde değil"
    sayılır - hâlâ hedef sıralamadaysa yeniden alınır, değilse durumdan
    sessizce düşer (rebalance() içinde)."""
    scores = compute_relative_strength_scores(client, universe, lookback_weeks)
    target = rank_and_select(scores, top_n, min_score_pct)
    target_set = set(target)

    tracked_symbols = set(load_holdings_local(username).keys())
    actual_held: set[str] = set()
    if tracked_symbols:
        try:
            actual_held = tracked_symbols & {p["symbol"] for p in client.get_all_positions()}
        except Exception:
            actual_held = set()

    return RebalancePlan(
        universe_size=len(universe),
        target_symbols=target,
        to_sell=sorted(actual_held - target_set),
        to_buy=sorted(target_set - actual_held),
        to_hold=sorted(actual_held & target_set),
        scores=scores,
    )


def compute_available_cash_for_rotation(client: AlpacaClient, cash_allocation_pct: float) -> float:
    """alpaca_buy_points.compute_available_cash_for_buying ile aynı desen -
    bu modülün payına (toplam canlı nakdin cash_allocation_pct'i) düşen
    tutardan, bu modülün hâlâ açık/bekleyen ("rs-" etiketli) buy-limit
    emirlerinin tutarını düşer. Bu modül şu an sadece market emriyle
    alıyor (bkz. rebalance()), o yüzden pratikte resting bir emir olması
    beklenmez - yine de aynı çift-sayım korumasını tutarlılık için
    uyguluyor."""
    cash = float(client.get_account()["cash"]) * (cash_allocation_pct / 100)
    reserved = sum(
        float(o["qty"]) * float(o["limit_price"])
        for o in client.get_open_orders()
        if o["type"] == "limit" and o["side"] == "buy"
        and (o.get("client_order_id") or "").startswith(f"{ORDER_TAG_PREFIX}-")
    )
    return max(0.0, cash - reserved)


def rebalance(client: AlpacaClient, username: str, cfg: dict, stop_settings: dict | None = None) -> dict:
    """Gerçek emir veren TEK fonksiyon - SADECE relative_strength_runner.py
    tarafından çağrılır (bkz. modül üstü not #1). Akış: evren oluştur
    (Premium Buy Point watchlist'i hariç) → likidite filtrele → planla →
    sıralamadan düşenleri sat (stop varsa önce iptal et, market emriyle
    kapat) → kalan nakitle yeni girenleri market emriyle al, anında stop
    kur → durumu kaydet."""
    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cash_allocation_pct = float(cfg.get("cash_allocation_pct") or DEFAULT_CASH_ALLOCATION_PCT)
    if not cfg.get("enabled") or cash_allocation_pct <= 0:
        return {"run_at": run_at, "skipped": True, "reason": "devre dışı ya da nakit payı 0"}

    top_n = int(cfg.get("top_n") or DEFAULT_TOP_N)
    lookback_weeks = int(cfg.get("lookback_weeks") or DEFAULT_LOOKBACK_WEEKS)
    min_score_pct = float(cfg.get("min_score_pct") if cfg.get("min_score_pct") is not None else DEFAULT_MIN_SCORE_PCT)
    stop_algorithm = resolve_stop_algorithm(cfg)
    stop_algo = STOP_ALGORITHMS[stop_algorithm]
    stop_settings = stop_settings or {}
    stop_shared_settings = stop_settings.get("shared") or {}
    stop_algo_settings = stop_settings.get(stop_algorithm) or {}

    universe = build_universe(
        username, cfg.get("include_nasdaq", True), cfg.get("include_nyse", False),
        cfg.get("custom_groups") or [], cfg.get("include_russell", False),
    )
    try:
        pbp_watchlist = client.get_watchlist_by_name(f"premium-buy-portfolio-{username}")
        pbp_symbols = {a["symbol"] for a in pbp_watchlist["assets"]} if pbp_watchlist else set()
    except Exception:
        pbp_symbols = set()
    # Fonksiyon içi import: orb_core -> otomatik_alim_satim_core ->
    # alpaca_trailing_stop -> relative_strength_core/orb_core döngüsünü
    # kırmak için (bkz. alpaca_trailing_stop.py'deki aynı notlar).
    from orb_core import load_holdings_local as load_orb_holdings
    orb_symbols = set(load_orb_holdings(username).keys())
    from heikin_ashi_intraday_core import load_holdings_local as load_ha_holdings
    orb_symbols |= set(load_ha_holdings(username).keys())  # Heikin Ashi Gün İçi'nin elindekiler de
    universe = [t for t in universe if t not in pbp_symbols and t not in orb_symbols]  # bkz. modül üstü not #2
    universe = filter_by_liquidity(client, universe, cfg.get("min_avg_dollar_volume", DEFAULT_MIN_AVG_DOLLAR_VOLUME))

    plan = plan_rebalance(client, username, universe, top_n, lookback_weeks, min_score_pct)
    holdings = load_holdings_local(username)

    sold: list[str] = []
    sell_errors: list[str] = []
    for symbol in plan.to_sell:
        try:
            position = client.get_position(symbol)
            if position is None:
                holdings.pop(symbol, None)  # zaten kapanmış (ör. safety-net stop) - sadece durumdan düş
                continue
            qty = float(position["qty"])
            stop_order = client.get_open_stop_order(symbol)
            if stop_order is not None:
                client.cancel_order(stop_order["id"])  # wash-trade koruması - bkz. top-up dalındaki aynı not
            tag = f"{ORDER_TAG_PREFIX}-exit-{symbol}-{int(datetime.now(timezone.utc).timestamp())}"
            client.place_market_exit(symbol, qty, client_order_id=tag)
            holdings.pop(symbol, None)
            sold.append(symbol)
        except Exception as e:
            sell_errors.append(f"{symbol}: {e}")

    # Satışlardan SONRA hesapla - o an serbest kalan nakit bu pass'in
    # alımlarında kullanılabilsin diye.
    total_rs_cash = float(client.get_account()["cash"]) * (cash_allocation_pct / 100)
    target_per_position = total_rs_cash / top_n if top_n > 0 else 0.0
    available_cash = compute_available_cash_for_rotation(client, cash_allocation_pct)

    bought: list[str] = []
    buy_errors: list[str] = []
    for symbol in plan.to_buy:
        try:
            dollar_amount = min(target_per_position, available_cash)
            if dollar_amount <= 0:
                buy_errors.append(f"{symbol}: kullanılabilir nakit yetersiz")
                continue
            try:
                live_price = client.get_latest_trade_price(symbol)
            except Exception:
                live_price = None
            if not live_price or live_price <= 0:
                buy_errors.append(f"{symbol}: güncel fiyat alınamadı")
                continue
            qty = math.floor(dollar_amount / live_price)
            if qty <= 0:
                buy_errors.append(f"{symbol}: hedef tutar (${dollar_amount:.2f}) 1 adet için yetersiz")
                continue

            tag = f"{ORDER_TAG_PREFIX}-buy-{symbol}-{int(datetime.now(timezone.utc).timestamp())}"
            buy_order = client.place_market_entry(symbol, qty, "long", client_order_id=tag)
            filled = client.wait_for_fill(buy_order["id"], timeout=30)
            fill_price = float(filled["filled_avg_price"])
            filled_qty = float(filled["filled_qty"])
            available_cash -= filled_qty * fill_price

            stop_price = round(stop_algo.initial_stop(
                fill_price, "long", **resolve_kwargs(stop_algo.initial_stop, stop_algo_settings, stop_shared_settings),
            ), 2)
            try:
                client.place_stop_order(symbol, filled_qty, "long", stop_price)
            except Exception as e:
                buy_errors.append(f"{symbol}: alındı (@ {fill_price:.2f}) ama stop kurulamadı, KORUMASIZ: {e}")

            holdings[symbol] = {
                "qty": filled_qty, "entry_price": fill_price,
                "entered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "score_pct": round(plan.scores.get(symbol, 0.0) * 100, 2),
            }
            bought.append(symbol)
        except Exception as e:
            buy_errors.append(f"{symbol}: {e}")

    save_holdings_local(username, holdings)

    return {
        "run_at": run_at,
        "skipped": False,
        "universe_size": plan.universe_size,
        "target_symbols": plan.target_symbols,
        "held": plan.to_hold,
        "sold": sold,
        "bought": bought,
        "sell_errors": sell_errors,
        "buy_errors": buy_errors,
        "cash_allocation_pct": cash_allocation_pct,
        "top_n": top_n,
    }
