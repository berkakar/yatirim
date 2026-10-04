"""
Structure-based trailing-stop bot for Alpaca paper trading.

Steps 2-5 below (the actual stop-price decision logic, as opposed to order
management) are the "breakeven_atr_structure" algorithm in stop_algorithms.py
- one entry in a pluggable STOP_ALGORITHMS registry (mirrors buy_algorithms.py's
ALGORITHMS for premium buy points), selected via manage_position's
`stop_algorithm` argument. run_once resolves which one to use per position
via resolve_stop_algorithm: the symbol's portfolio_config_berkakar.json
symbol_settings[symbol].stop_algorithm override if set, else the
portfolio-wide config.stop_algorithm, else DEFAULT_STOP_ALGORITHM - same
config file and same per-symbol-override-over-portfolio-default precedence
alpaca_buy_points.py already uses for which buy algorithm is active. This
module handles everything ELSE: order lookup/placement/resizing,
extended-hours recovery, and calling into whichever algorithm is selected
for the actual price decision.

Manages every open position in the account. Each pass, per position:

  1. Makes sure a stop order is resting (places an initial fixed-% stop
     if none exists yet - the video trails an existing trade, it doesn't
     define entry/initial-risk sizing). In practice this is now a fallback:
     alpaca_buy_points.py submits entries as bracket orders with this same
     stop already attached, so it activates the instant the entry fills
     instead of waiting on this script's next (GitHub Actions-scheduled,
     and not always promptly delivered) run. This still covers a position
     that ends up with no stop some other way (opened outside this system).
  2. Structure is evaluated only from bars since we started managing this
     position (its first stop order's timestamp), not an arbitrary fixed
     lookback - otherwise "reference" swing points from market phases
     that happened before entry can permanently block trailing.
  3. Breakeven floor: once price has moved far enough in our favor, the
     stop is guaranteed to be at least at entry, even if structure hasn't
     validated a trail yet (this is the video's simpler first step).
  4. Structure-based trail: finds the latest break-of-structure-validated
     swing point (see structure.py - including its stale-reference reset,
     so a reference that goes unbroken for too long doesn't freeze
     trailing forever) and, gated by a higher-timeframe (daily EMA) trend
     filter, trails the stop just past it using an ATR-scaled buffer.
  5. Of whatever candidates apply, only the one that most tightens the
     stop (and stays on the correct side of the current price) is sent -
     never loosens, never sent past current price.
  6. If the position's total qty has grown or shrunk since the last pass
     (most notably: alpaca_buy_points.py's budget top-up, which buys more
     shares of a symbol that already has an open position), the resting
     stop's own qty is resized to match first, so it always covers the
     whole position. Depending on the user's "İlave Alım sonrası stop
     davranışı" choice in the Premium Buy Point module
     (portfolio_config_berkakar.json's top_up_stop_mode, loaded once per
     pass), a top-up that changed the average entry price can also add a
     same-%-as-a-fresh-entry candidate at the new average - still subject
     to the "only tightens" rule in step 5.

Separately, run_extended_hours_guard (--extended-hours-guard) covers a gap
this loop can't: a regular "stop" order only triggers 09:30-16:00 ET, so a
pre-market/after-hours move can push price through it while it just sits
there, inert, until the next regular session. Run on its own schedule during
the 04:00-09:30 and 16:00-20:00 ET windows, it replaces an already-breached
stop with a day+extended_hours limit order - the only order type Alpaca lets
execute outside regular hours - and sends a Telegram alert. If that emergency
order itself expires unfilled at the session's end - leaving the position
with nothing resting at all - the guard's own next run (its normal ~10min
cadence, not the next regular session) notices via last_trailed_stop_price
and re-establishes protection at the same level right away: a marketable
limit if price is still through it, otherwise a regular GTC stop (Alpaca
queues it outside regular hours; the guard then watches it like any resting
stop - a sell limit below market would fill immediately, not wait). Only if
that history isn't recent enough to trust either (see its own docstring)
does the gap actually widen to the next regular session, where point 1
above restores the same way.

The guard also covers the opposite gap: when the stop is still safely
below/above price (not breached) during pre-market/after-hours, it now
also runs the SAME trail() the regular session uses (see
_extended_hours_trail) - with the bar window fetched WITH extended hours
included, since otherwise a pre-market move is invisible to trail() until
the next regular-session bar closes. Without this, a stop that should
already be at breakeven (or further, via structure) stays frozen at
whatever the last regular session left it at until the market reopens and
the regular cron catches up - which can be most of a session if the move
happened right after the prior close.

Run with --once for a single pass (used by the GitHub Actions workflow,
which handles the scheduling). Without --once it loops locally, sleeping
between passes and until the market reopens. --extended-hours-guard runs the
separate mechanism described above and exits.

2026-09-28 emir analizi değişiklikleri (ayrıntı: 📒 İşlem Günlüğü > 📝 Değişiklik
Günlüğü, changelog.py; kodda "[2026-09-28 · Öneri N]" yorumları):
  - Öneri 1: Premium Buy Point hisselerinde stop, girişin kendi mum periyodunda
    izlenir (resolve_stop_timeframe_for_position, _stop_bars_for_timeframe);
    StopContext'e ilk stop (1R) ve ATR için giriş öncesi bar penceresi geçirilir.
  - Öneri 3: Açılış kalkanı - seans dışında guard stopu felaket seviyesine
    genişletir, gerçek seviye emrin etiketinde saklanır (stop_tags.py); seans
    açılışından 15 dk sonra manage_position gerçek seviyeye döner ya da
    kırılmışsa market çıkışı yapar. Seans dışı trail varsayılan olarak kapalı.
  - Öneri 6: Tüm stop emirleri çıkış sebebini gösteren bir etiketle kurulur.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from alpaca_bars_cache import DAILY_BARS_CACHE_PATH, INTRADAY_BARS_CACHE_PATH, get_cached_raw_bars
from alpaca_client import AlpacaClient, DEFAULT_TRADING_URL, DEFAULT_DATA_URL, stop_beyond_price
import storage
from stop_algorithms import DEFAULT_STOP_ALGORITHM, STOP_ALGORITHMS, StopContext, resolve_kwargs
from stop_algorithms import TREND_EMA_PERIOD as DEFAULT_TREND_EMA_PERIOD
from stop_tags import parse_shield_real_stop, reason_code, shield_exit_tag, shield_tag, stop_tag
from structure import Bar
from telegram_notify import TelegramError, send_telegram_message

load_dotenv()

TIMEFRAME = os.environ.get("TRADE_TIMEFRAME", "30Min")
POLL_SECONDS = int(os.environ.get("TRADE_POLL_SECONDS", "60"))

# Sadece bu pozisyon yönetim döngüsünün ne kadar geriye bar çekeceğini
# belirler - bir stop algoritmasının parametresi DEĞİL (bkz. LOOKBACK_DAYS'in
# kullanıldığı get_management_start). ATR/yapısal-trail/breakeven/kâr-kilidi
# gibi TÜM stop-loss algoritma parametreleri artık Stop Loss Ayarları
# sayfasında (stop_loss_settings.py) kullanıcı bazında kaydediliyor -
# kaydedilmemiş bir değer için stop_algorithms.py'deki ilgili fonksiyonun
# kod-varsayılanı geçerli olur (bkz. stop_algorithms.resolve_kwargs).
LOOKBACK_DAYS = int(os.environ.get("TRADE_LOOKBACK_DAYS", "15"))

# alpaca_buy_points.py'nin okuduğu aynı dosya (Premium Buy Point modülünde
# write_portfolio_config ile commit edilir) - tek kullanıcı (berkakar)
# varsayımı burada da geçerli.
CONFIG_PATH = "portfolio_config_berkakar.json"
TOP_UP_STOP_MODE_DEFAULT = "keep"

# Stop Loss Ayarları modülünün (stop_loss_settings.py) GitHub'a commit ettiği
# aynı dosya - tek kullanıcı (berkakar) varsayımı burada da geçerli.
STOP_LOSS_SETTINGS_PATH = "stop_loss_settings_berkakar.json"

# Extended-hours guard (bkz. run_extended_hours_guard) - normal "stop" emri
# sadece normal seansta (aşağıdaki takvimden okunan open/close arası)
# tetiklenebiliyor; Alpaca bu pencerenin dışında yalnızca limit emirlere
# (time_in_force="day" + extended_hours=True) izin veriyor. PRE_MARKET_START
# ve AFTER_HOURS_END, Alpaca'nın izin verdiği sabit 04:00-20:00 ET
# extended-hours sınırları - günden güne değişmiyor (yarım günlerde asıl
# open/close takvimden okunduğu için ayrıca hesaba katılıyor).
PRE_MARKET_START = dtime(4, 0)
AFTER_HOURS_END = dtime(20, 0)
EXTENDED_HOURS_SLIPPAGE_PCT = float(os.environ.get("TRADE_EXTENDED_HOURS_SLIPPAGE_PCT", "0.5")) / 100
# manage_position, resting stop bulamadığında (bkz. last_trailed_stop_price)
# geçmişteki son stop emrini ancak bu kadar yakın zamanlıysa güvenilir sayar
# - aksi halde sembolün GEÇMİŞTE (haftalar/aylar önce) kapanmış bambaşka bir
# pozisyonuna ait eski bir stop, şimdiki (muhtemelen bambaşka bir giriş
# fiyatındaki) pozisyona yanlışlıkla uygulanabilir. Bir hafta sonu/3 günlük
# tatili rahatça kapsayacak kadar geniş tutuldu.
EXTENDED_HOURS_RESTORE_MAX_AGE_DAYS = float(os.environ.get("TRADE_EXTENDED_HOURS_RESTORE_MAX_AGE_DAYS", "5"))

# fon_hisse_uyari.py'nin de kullandığı aynı tek-kullanıcı bildirim ayarları
# dosyası ve TELEGRAM_BOT_TOKEN secret'ı - ayrı bir konfigürasyona gerek yok.
NOTIFICATION_SETTINGS_PATH = "bildirim_ayarlari_berkakar.json"

# get_management_start'ın sembol başına cache'lediği "earliest_stop_at" -
# bkz. get_management_start'ın docstring'i.
MANAGEMENT_START_CACHE_PATH = "alpaca_position_management_cache_berkakar.json"

ET = ZoneInfo("America/New_York")

# [2026-09-28 · Öneri 3] Emir yürütme ayarları - stop_loss_settings_<kullanıcı>.json
# içindeki "execution" anahtarında (Stop Loss Ayarları sayfasından değiştirilir),
# yoksa buradaki varsayılanlar geçerli:
#   opening_shield_enabled   : Açılış kalkanı. 2026-09-28 analizinde 17 çıkışın
#                              7'si 09:30-09:35 ET'de, 3'ü seans dışında oldu -
#                              düz stop emri açılış fiyatlamasının oynaklığında
#                              piyasa fiyatından doluyordu. Kalkan açıkken seans
#                              dışında (extended-hours guard) stop, gerçek
#                              seviyenin shield_disaster_pct altına ("felaket
#                              stopu") çekilir; gerçek seviye emrin etiketinde
#                              saklanır (stop_tags.py). Seans açıldıktan
#                              opening_shield_minutes dakika sonra manage_position
#                              gerçek seviyeye döner - fiyat o seviyenin altındaysa
#                              pozisyonu market emriyle kapatır.
#   extended_hours_trail_enabled: Seans dışında (işlem hacmi düşük pre-market
#                              barlarıyla) stopu sıkılaştırma - analizde bu,
#                              stopun açılışta tetiklenmesine yol açıyordu;
#                              varsayılan olarak KAPALI.
EXECUTION_DEFAULTS = {
    "opening_shield_enabled": True,
    "opening_shield_minutes": 15,
    "shield_disaster_pct": 4.0,
    "extended_hours_trail_enabled": False,
}

# [2026-09-28 · Öneri 1] Stopun izlendiği bar periyodu artık Premium Buy
# Point hisseleri için GİRİŞ SİNYALİNİN periyodu (symbol_settings[sembol].
# timeframe) - eskiden her hisse sabit TIMEFRAME (30Min) ile yönetiliyordu, günlük
# sinyalle alınan MSFT/PAYX 30 dakikalık gürültüyle stoplanıyordu. ATR (hem
# initial stop hem trail) için giriş ÖNCESİNİ de kapsayan bir pencere gerekir:
# günlükte ATR(14) için ~3 hafta işlem günü, gün içinde birkaç gün yeterli.
STOP_HISTORY_DAYS_DAILY = 45
STOP_HISTORY_DAYS_INTRADAY = 7


def load_execution_settings(stop_settings: dict | None = None) -> dict:
    """EXECUTION_DEFAULTS'un kullanıcı override'larıyla birleşmiş hali."""
    if stop_settings is None:
        stop_settings = load_stop_loss_settings()
    saved = stop_settings.get("execution") or {}
    return {**EXECUTION_DEFAULTS, **{k: v for k, v in saved.items() if v is not None}}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def load_portfolio_config() -> dict:
    """Premium Buy Point modülünün yazdığı portföy config'i (bkz.
    github_config.py) - top_up_stop_mode, stop_algorithm ve hisse bazlı
    symbol_settings override'ları burada. Dosya yoksa boş dict döner."""
    return storage.load_json(CONFIG_PATH, {})


def load_stop_loss_settings() -> dict:
    """Stop Loss Ayarları modülünün (stop_loss_settings.py) GitHub'a commit
    ettiği, kullanıcının her stop-loss algoritması için kaydettiği parametre
    override'ları - {"shared": {...}, "<algo_id>": {...}, ...}. Dosya yoksa
    boş dict döner (stop_algorithms.resolve_kwargs bunu "hiç override yok,
    her şey kod-varsayılanı" olarak yorumlar). SQLite açıksa (bkz.
    storage.enabled) arayüzün yazdığı kayıt oradan okunur."""
    return storage.load_json(STOP_LOSS_SETTINGS_PATH, {})


def load_top_up_stop_mode(config: dict | None = None) -> str:
    """Premium Buy Point modülünde kullanıcının seçtiği, ilave alım (top-up)
    sonrası resting stop davranışı - "keep" (varsayılan: sadece adet
    genişler, fiyat seviyesi değişmez) veya "tighten_to_new_entry" (yeni
    ortalama giriş fiyatına göre bir nefes payı adayı da eklenir - bkz.
    manage_position, sadece stop'u sıkılaştırırsa uygulanır)."""
    if config is None:
        config = load_portfolio_config()
    return config.get("top_up_stop_mode") or TOP_UP_STOP_MODE_DEFAULT


def resolve_stop_algorithm(config: dict, symbol: str) -> str:
    """Bir sembol için aktif stop-loss algoritmasını çözer: hisse bazlı
    override (symbol_settings[symbol].stop_algorithm) varsa o, yoksa
    portföy geneli varsayılan (config.stop_algorithm), o da yoksa/geçersizse
    DEFAULT_STOP_ALGORITHM - buy_algorithms için alpaca_buy_points.py'nin
    kullandığı aynı çözümleme deseni (bkz. run_once)."""
    settings = (config.get("symbol_settings") or {}).get(symbol) or {}
    algo = settings.get("stop_algorithm") or config.get("stop_algorithm") or DEFAULT_STOP_ALGORITHM
    return algo if algo in STOP_ALGORITHMS else DEFAULT_STOP_ALGORITHM


def resolve_stop_algorithm_for_position(
    pbp_config: dict, rs_holdings: dict, rs_config: dict, orb_holdings: dict, orb_config: dict, symbol: str,
    ha_holdings: dict | None = None, ha_config: dict | None = None,
) -> str:
    """Hesaptaki HER pozisyonu (run_once/run_extended_hours_guard, bkz.
    get_all_positions) yönetirken sembolün Relative Strength Rotasyonu'na,
    Açılış Aralığı Kırılımı'na (ORB) mı yoksa Premium Buy Point'e mi ait
    olduğunu ayırt eder - aksi halde RS'nin/ORB'un elindeki bir pozisyon da
    PBP'nin portföy-geneli/hisse bazlı stop_algorithm ayarıyla (muhtemelen
    o modül için SEÇİLEN algoritmadan farklı) yönetilmeye devam ederdi.
    rs_holdings/orb_holdings'te olan bir sembol o modülündür (her ikisi de
    PBP watchlist'iyle VE birbirleriyle çakışmayı zaten baştan engelliyor -
    bkz. relative_strength_core.py'nin ve orb_core.py'nin modül üstü
    notları #2), diğer her şey resolve_stop_algorithm ile PBP mantığına
    göre çözülür."""
    # Fonksiyon içi (modül seviyesinde değil) importlar bilerek: relative_strength_core/
    # orb_core -> otomatik_alim_satim_core -> alpaca_trailing_stop -> relative_strength_core/
    # orb_core döngüsünü kırmak için - modüller birbirini çağırma zamanında
    # (import zamanında değil) güvenle görebiliyor.
    if symbol in rs_holdings:
        from relative_strength_core import resolve_stop_algorithm as resolve_rs_stop_algorithm
        return resolve_rs_stop_algorithm(rs_config)
    if symbol in orb_holdings:
        from orb_core import resolve_stop_algorithm as resolve_orb_stop_algorithm
        return resolve_orb_stop_algorithm(orb_config)
    if ha_holdings and symbol in ha_holdings:
        from heikin_ashi_intraday_core import resolve_stop_algorithm as resolve_ha_stop_algorithm
        return resolve_ha_stop_algorithm(ha_config or {})
    return resolve_stop_algorithm(pbp_config, symbol)


def resolve_stop_timeframe(config: dict, symbol: str) -> str:
    """[2026-09-28 · Öneri 1] Premium Buy Point hissesi için stopun izleneceği
    bar periyodu: config["stop_timeframe_mode"] "entry" (varsayılan) ise
    hissenin giriş sinyali periyodu (symbol_settings[sembol].timeframe), yoksa
    "global" ise eskisi gibi TIMEFRAME (TRADE_TIMEFRAME, 30Min)."""
    if (config.get("stop_timeframe_mode") or "entry") != "entry":
        return TIMEFRAME
    settings = (config.get("symbol_settings") or {}).get(symbol) or {}
    return settings.get("timeframe") or TIMEFRAME


def resolve_stop_timeframe_for_position(
    pbp_config: dict, rs_holdings: dict, orb_holdings: dict, ha_holdings: dict | None, symbol: str,
) -> str:
    """RS/ORB/HA modüllerinin pozisyonları eskisi gibi TIMEFRAME ile yönetilir
    (bu modüllerin stop mantığı bu değişiklikte bilerek değiştirilmedi); sadece
    Premium Buy Point hisseleri girişin kendi periyoduna geçer."""
    if symbol in rs_holdings or symbol in orb_holdings or (ha_holdings and symbol in ha_holdings):
        return TIMEFRAME
    return resolve_stop_timeframe(pbp_config, symbol)


def _prune_holdings(path: str, load, save, live_symbols: set[str]) -> dict:
    """Bir modülün holdings'inden gerçek pozisyonu kalmamış sembolleri düşürür.
    SQLite açıksa bu, kilitli tek bir oku-değiştir-yaz işlemidir: aynı anda
    çalışan modül işi (örn. ORB taraması) arada yeni bir alım kaydettiyse o
    kayıt ezilmez."""
    if storage.db_key(path) is not None:
        return storage.update_json(
            path, lambda h: {s: info for s, info in (h or {}).items() if s in live_symbols}, {},
        )
    holdings = load("berkakar")
    pruned = {s: info for s, info in holdings.items() if s in live_symbols}
    if pruned != holdings:
        save("berkakar", pruned)
    return pruned


def _load_cross_module_holdings_pruned(live_symbols: set[str]):
    """rs_holdings/orb_holdings/ha_holdings state'lerini yükler VE stopu
    tetiklenmiş (artık gerçek pozisyonu olmayan) sembolleri düşürüp diske geri
    yazar - aksi halde (bkz. TREX vakası, 2026-09-25: stop 15:12'de tetiklendi
    ama orb_scan_holdings_berkakar.json ORB'un bir sonraki taramasına -
    ertesi güne - kadar hâlâ "elimde var" gösterdi) ilgili modülün KENDİ bir
    sonraki taramasına kadar (ORB için ertesi güne, RS Rotasyonu için bir
    sonraki Pazartesi'ye kadar) hem önizleme/UI'da yanlış gösterilir hem de
    evrenden gereksiz yere hariç tutulurdu. run_once/run_extended_hours_guard
    zaten her çalıştığında positions'ı (dolayısıyla live_symbols'ı) çekiyor,
    o yüzden temizliği en hızlı - ve tüm modüller için TEK yerden - burada
    yapmak mantıklı."""
    from relative_strength_core import (
        holdings_path as rs_holdings_path, load_config_local, load_holdings_local,
        save_holdings_local as save_rs_holdings_local,
    )
    from orb_core import (
        holdings_path as orb_holdings_path, load_config_local as load_orb_config_local,
        load_holdings_local as load_orb_holdings_local, save_holdings_local as save_orb_holdings_local,
    )
    from heikin_ashi_intraday_core import (
        holdings_path as ha_holdings_path, load_config_local as load_ha_config_local,
        load_holdings_local as load_ha_holdings_local, save_holdings_local as save_ha_holdings_local,
    )

    rs_holdings = _prune_holdings(rs_holdings_path("berkakar"), load_holdings_local, save_rs_holdings_local, live_symbols)

    orb_holdings = _prune_holdings(orb_holdings_path("berkakar"), load_orb_holdings_local, save_orb_holdings_local, live_symbols)

    ha_holdings = _prune_holdings(ha_holdings_path("berkakar"), load_ha_holdings_local, save_ha_holdings_local, live_symbols)

    return (
        rs_holdings, load_config_local("berkakar"),
        orb_holdings, load_orb_config_local("berkakar"),
        ha_holdings, load_ha_config_local("berkakar"),
    )


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


_TIMEFRAME_UNITS = {"Min": timedelta(minutes=1), "Hour": timedelta(hours=1), "Day": timedelta(days=1)}


def _timeframe_duration(timeframe: str) -> timedelta:
    match = re.match(r"^(\d+)(Min|Hour|Day)$", timeframe)
    if not match:
        raise ValueError(f"Unrecognized timeframe: {timeframe!r}")
    n, unit = match.groups()
    return int(n) * _TIMEFRAME_UNITS[unit]


def get_regular_hours_bars(
    client: AlpacaClient, symbol: str, timeframe: str, start: datetime, exclude_forming: bool = False,
    cache_file: str | None = None, include_extended_hours: bool = False,
) -> list[Bar]:
    """`cache_file` verilirse (GitHub Actions cron script'leri), bar'lar
    alpaca_bars_cache üzerinden - sadece eksik/oluşum-halindeki kısmı Alpaca'dan
    çekilerek - alınır; aksi halde (ör. Streamlit'in interaktif önizlemesi)
    bugünkü gibi doğrudan tam pencere çekilir. cache_file'lı yol, birden fazla
    çağıranın (ör. bu script ile alpaca_buy_points.py) FARKLI `start`
    pencereleri istediği aynı sembol+timeframe için önbellekten fazlasını
    döndürebileceğinden, aşağıdaki `ts >= start` filtresi her iki yolda da
    isteneni aşan bar'ları eler. Alpaca'nın döndürdüğü ham bar'lar zaten
    pre-market/after-hours'ı da içeriyor (get_raw_bars hiçbir seans
    parametresi geçmiyor) - normal seans filtresi tamamen bu fonksiyonun
    kendi tarafında uygulanıyor, o yüzden aynı cache_file hem bu fonksiyonun
    hem include_extended_hours=True çağıran kodun (bkz. aşağısı) arasında
    güvenle paylaşılabiliyor.

    include_extended_hours: True verilirse 09:30-16:00 ET seans penceresi
    hiç uygulanmaz - sadece hafta sonu bar'ları elenir. Sadece Extended
    Hours Guard'ın pre-market/after-hours'ta stopu sıkılaştırabilmesi için
    (bkz. _extended_hours_trail) kullanılır; canlı sistemin normal trail'i
    (manage_position) ve backtest hâlâ varsayılan (False, sadece normal
    seans) davranışı kullanır."""
    if cache_file is not None:
        raw_bars = get_cached_raw_bars(client, cache_file, symbol, timeframe, start)
    else:
        raw_bars = client.get_raw_bars(symbol, timeframe, start.isoformat())

    bars = []
    for b in raw_bars:
        ts_utc = _parse_iso(b["t"])
        if ts_utc < start:
            continue
        ts = ts_utc.astimezone(ET)
        if ts.weekday() >= 5:
            continue
        if not include_extended_hours and not (9, 30) <= (ts.hour, ts.minute) < (16, 0):
            continue
        bars.append(Bar(t=b["t"], o=b["o"], h=b["h"], l=b["l"], c=b["c"], v=b["v"]))

    if exclude_forming and bars:
        last_start = _parse_iso(bars[-1].t)
        if last_start + _timeframe_duration(timeframe) > datetime.now(timezone.utc):
            bars.pop()  # still-forming bar - buy-point signals shouldn't chase intra-bar noise

    return bars


def get_bars_for_timeframe(
    client: AlpacaClient, symbol: str, timeframe: str, start: datetime, exclude_forming: bool = False,
    cache_file: str | None = None,
) -> list[Bar]:
    """get_regular_hours_bars'ın "1Day" için de güvenli hali: günlük barlar
    için get_regular_hours_bars KULLANILMAZ - "regular hours" (09:30-16:00 ET)
    penceresi gün içi barlar için anlamlı, günlük barın kendi zaman damgasına
    (Alpaca'da genelde 00:00 ET) uygulanınca bu pencereye hiçbir zaman
    girmediğinden TÜM barları yanlışlıkla eler (boş liste döner). Bir sembolün
    seçili mum periyodu "1Day" olduğunda bu, o sembol için sinyal/fiyat hiç
    hesaplanamamasına - dolayısıyla ne canlı alım emri verilmesine ne de
    Premium Buy Point karşılaştırma tablosunda satırının görünmesine - yol
    açar. `timeframe` per-sembol ayardan geldiği ve kullanıcı "1 Gün"ü
    seçebildiği için (BackTest, Otomatik Alım/Satım modüllerinde), bu ayrım
    her per-sembol bar çekiminde şart.

    `cache_file` verilirse (bkz. get_regular_hours_bars), "1Day" dalı da
    kendi verisini DAILY_BARS_CACHE_PATH üzerinden alır (get_trend_daily_closes/
    _get_daily_closes ile aynı önbellek) - çağıran taraf hangi cache_file'ı
    geçerse geçsin, günlük bar'lar her zaman günlük cache'te tutulur; bu
    parametre sadece "önbellekleme açık mı" sinyalini taşır."""
    if timeframe == "1Day":
        if cache_file is not None:
            raw = get_cached_raw_bars(client, DAILY_BARS_CACHE_PATH, symbol, "1Day", start)
            raw = [b for b in raw if _parse_iso(b["t"]) >= start]
        else:
            raw = client.get_raw_bars(symbol, "1Day", start.isoformat())
        return [Bar(t=b["t"], o=b["o"], h=b["h"], l=b["l"], c=b["c"], v=b["v"]) for b in raw]
    return get_regular_hours_bars(client, symbol, timeframe, start, exclude_forming=exclude_forming, cache_file=cache_file)


def _load_management_start_cache() -> dict:
    return storage.load_json(MANAGEMENT_START_CACHE_PATH, {})


def _save_management_start_cache(cache: dict) -> None:
    storage.save_json(MANAGEMENT_START_CACHE_PATH, cache)


def prune_management_start_cache(open_symbols: set[str]) -> None:
    """Artık açık pozisyonu olmayan sembollerin cache'lenmiş
    "earliest_stop_at"ını temizler - aksi halde bir pozisyon kapanıp
    (muhtemelen haftalar sonra) yeniden açıldığında, get_management_start
    ESKİ pozisyonun ilk stop'unun zaman damgasını yeni pozisyona
    uygulayabilir, yapı analizinin gereğinden çok daha eskiye
    dayanmasına yol açardı. run_once, her pass başında güncel açık
    pozisyon setiyle bunu çağırır."""
    cache = _load_management_start_cache()
    stale = [s for s in cache if s not in open_symbols]
    if not stale:
        return
    for s in stale:
        del cache[s]
    _save_management_start_cache(cache)


# [2026-10-03] Bir stop emrinin MEVCUT pozisyona ait olup olmadığı, pozisyonun
# açılış zamanına göre ayrılır (position_opened_at). Önceden "sembolün en eski /
# en son stop emri" kullanılıyordu ve bu, pozisyondan ÖNCEKİ emirleri de
# kapsıyordu. Gözlemlenen gerçek örnek MDB (2026-09-28): 24 Eylül'de konan
# bracket alış limiti (396.03) açılışta %24 boşlukla 308.82'den doldu, Alpaca
# piyasanın üstünde kalan 390.09'luk stop bacağını AYNI ANDA iptal etti;
# last_trailed_stop_price bu hiç devreye girmemiş bacağı "son trail seviyesi"
# sayıp 390.09'u geri yüklemeye çalıştı, Alpaca reddetti ve pozisyon ~29 saat
# korumasız kaldı. initial_stop_price da 17 Eylül'deki eski bir pozisyondan
# 368.41 okunmuştu (giriş 308.82 - 1R negatif).
POSITION_FILLS_LOOKBACK_DAYS = 60
# Bracket bacağının iptali dolumla aynı anda (ms farkla) gelir - bu pay içinde
# biten bir emir pozisyona ait sayılmaz.
STOP_LEG_GRACE = timedelta(seconds=5)


def position_opened_at(client: AlpacaClient, symbol: str, signed_qty: float) -> datetime | None:
    """Mevcut pozisyonun açıldığı an: güncel adetten geriye doğru dolumlar
    üzerinden yürüyerek pozisyonun en son SIFIRDAN açıldığı dolum bulunur
    (ilave alımlar açılış sayılmaz). POSITION_FILLS_LOOKBACK_DAYS içinde
    bulunamazsa (pozisyon daha eski) None - bu durumda filtre uygulanmaz.

    Sonuç pozisyon yönetim önbelleğinde (storage: SQLite ya da JSON) sembol
    başına bir kez tutulur; kayıt pozisyon kapanınca
    prune_management_start_cache ile silinir. İlk hesaplandığında aynı
    kayıttaki earliest_stop_at / initial_stop_price da silinir - bu alanlar
    eski kuralla (pozisyon öncesi emirleri de sayarak) hesaplanmış olabilir."""
    cache = _load_management_start_cache()
    cached = cache.get(symbol) or {}
    if "opened_at" in cached:
        return _parse_iso(cached["opened_at"]) if cached["opened_at"] else None
    try:
        fills = client.get_symbol_fills(symbol, POSITION_FILLS_LOOKBACK_DAYS)
    except Exception as e:
        log(f"{symbol}: pozisyon açılış zamanı için dolumlar alınamadı, filtre uygulanmıyor: {e}")
        return None
    fills = sorted((f for f in fills if f.get("filled_at")), key=lambda f: _parse_iso(f["filled_at"]))
    opened = None
    qty = signed_qty
    for fill in reversed(fills):
        delta = float(fill["filled_qty"]) * (1 if fill["side"] == "buy" else -1)
        before = qty - delta
        if abs(before) < 1e-9:
            opened = _parse_iso(fill["filled_at"])
            break
        qty = before
    cache[symbol] = {"opened_at": opened.isoformat() if opened else None}
    _save_management_start_cache(cache)
    return opened


def _belongs_to_position(order: dict, opened_at: datetime | None) -> bool:
    """Pozisyon açıldıktan sonra kurulmuş ya da açılıştan sonra da yaşamış
    (bracket bacağı gibi önceden kurulup dolumla devreye giren) emirler
    pozisyona aittir; açılıştan önce ya da açılışla aynı anda biten emirler
    (eski pozisyonların stopları, dolumda iptal edilen bracket bacağı) değil."""
    if opened_at is None:
        return True
    if _parse_iso(order["created_at"]) >= opened_at:
        return True
    ended = (order.get("canceled_at") or order.get("expired_at") or order.get("replaced_at")
             or order.get("filled_at"))
    return ended is None or _parse_iso(ended) > opened_at + STOP_LEG_GRACE


def _position_stop_history(
    client: AlpacaClient, symbol: str, signed_qty: float | None, limit: int = 50,
) -> tuple[list[dict], datetime | None]:
    history = client.get_stop_order_history(symbol, limit=limit)
    if signed_qty is None:
        return history, None
    opened_at = position_opened_at(client, symbol, signed_qty)
    return [o for o in history if _belongs_to_position(o, opened_at)], opened_at


def get_management_start(
    client: AlpacaClient, symbol: str, lookback_days: int, signed_qty: float | None = None,
) -> datetime:
    """The earlier of `lookback_days` ago and when we first started
    managing this position's stop - whichever is more recent wins, so
    structure from before we ever held the trade can't anchor the
    reference point.

    Pozisyon açık kaldığı sürece bu pozisyonun "earliest_stop_at"ı hiç
    değişmez (bir pozisyonun ilk stop'u sadece bir kez, açılışında
    kurulur; sonraki trail'ler zaman içinde geriye gitmez) - bu yüzden
    sadece o kısım cache'lenir ve get_stop_order_history'nin tekrar tekrar
    (her pass'te, her açık pozisyon için) çağrılması önlenir.
    `default_start` ise `lookback_days`'e göre kayan (canlı) bir pencere
    olduğu için HER ÇAĞRIDA taze hesaplanır - aksi halde lookback_days'ten
    uzun süredir açık bir pozisyon için pencere donmuş kalırdı.

    signed_qty verilirse yalnızca mevcut pozisyona ait stoplar sayılır ve
    pozisyonun açılış anı biliniyorsa doğrudan o kullanılır (bkz.
    position_opened_at) - bracket bacağı dolumdan önce kurulduğu için en
    eski stopun zamanı açılıştan önce olabilir."""
    default_start = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    opened_at = position_opened_at(client, symbol, signed_qty) if signed_qty is not None else None
    cache = _load_management_start_cache()
    cached = cache.get(symbol)
    # "earliest_stop_at" anahtarının VARLIĞI kontrol edilir (kaydın varlığı
    # değil): aynı kayıtta artık get_initial_stop_price'ın alanı da tutuluyor.
    if cached is not None and "earliest_stop_at" in cached:
        earliest = _parse_iso(cached["earliest_stop_at"]) if cached.get("earliest_stop_at") else None
    else:
        if opened_at is not None:
            earliest = opened_at
        else:
            history, _ = _position_stop_history(client, symbol, signed_qty, limit=200)
            earliest = min((_parse_iso(o["created_at"]) for o in history), default=None)
        cache[symbol] = {**(cached or {}), "earliest_stop_at": earliest.isoformat() if earliest else None}
        _save_management_start_cache(cache)

    if earliest is None:
        return default_start
    return max(default_start, earliest)


def get_initial_stop_price(client: AlpacaClient, symbol: str, signed_qty: float | None = None) -> float | None:
    """[2026-09-28 · Öneri 1-2] Pozisyonun İLK stop seviyesi (en eski stop
    emrinin fiyatı) - 1R = giriş - ilk stop hesabı için (StopContext.
    initial_stop_price). get_management_start ile aynı cache dosyasında,
    sembol başına bir kez hesaplanır; eski cache kayıtlarında alan yoksa
    geçmiş bir kez daha çekilip eklenir. signed_qty verilirse yalnızca
    mevcut pozisyona ait stoplar sayılır (bkz. _belongs_to_position)."""
    if signed_qty is not None:
        position_opened_at(client, symbol, signed_qty)  # gerekirse eski alanları geçersiz kılar
    cache = _load_management_start_cache()
    cached = cache.get(symbol) or {}
    if "initial_stop_price" in cached:
        return cached["initial_stop_price"]
    history, _ = _position_stop_history(client, symbol, signed_qty, limit=200)
    earliest = min(history, key=lambda o: _parse_iso(o["created_at"]), default=None)
    price = None
    if earliest is not None:
        # Kalkanlı bir emir en eskisiyse (ör. pozisyon seans dışında açıldıysa)
        # etiketteki gerçek seviye esas alınır, felaket seviyesi değil.
        price = parse_shield_real_stop(earliest.get("client_order_id")) or float(earliest["stop_price"])
    cached["initial_stop_price"] = price
    cache[symbol] = {**(cache.get(symbol) or {}), **cached}
    _save_management_start_cache(cache)
    return price


def minutes_since_regular_open(client: AlpacaClient) -> float | None:
    """[2026-09-28 · Öneri 3] Bugünkü normal seans açılışından bu yana geçen
    dakika - seans kapalıysa/tatilse None. Yarım günler için takvimden okunur."""
    now_et = datetime.now(timezone.utc).astimezone(ET)
    today = now_et.date().isoformat()
    try:
        days = client.get_calendar(today, today)
    except Exception:
        return None
    if not days:
        return None
    open_t = datetime.strptime(days[0]["open"], "%H:%M").time()
    close_t = datetime.strptime(days[0]["close"], "%H:%M").time()
    if not open_t <= now_et.time() < close_t:
        return None
    open_dt = now_et.replace(hour=open_t.hour, minute=open_t.minute, second=0, microsecond=0)
    return (now_et - open_dt).total_seconds() / 60


def shield_disaster_price(real_stop_price: float, side: str, execution: dict) -> float:
    pct = float(execution.get("shield_disaster_pct") or 0) / 100
    return real_stop_price * (1 - pct) if side == "long" else real_stop_price * (1 + pct)


def apply_opening_shield(
    client: AlpacaClient, symbol: str, side: str, stop_order: dict, execution: dict,
) -> dict | None:
    """[2026-09-28 · Öneri 3] Resting stopu felaket seviyesine genişletir,
    gerçek seviyeyi yeni emrin etiketine yazar. Etiket yazılamazsa stop
    genişletilmez (gerçek seviye kaybolmasın) - None döner."""
    real = float(stop_order["stop_price"])
    disaster = round(shield_disaster_price(real, side, execution), 2)
    try:
        new_order = client.replace_stop_price(
            stop_order["id"], disaster, client_order_id=shield_tag(symbol, real), allow_untagged_fallback=False,
        )
    except requests.HTTPError as e:
        log(f"{symbol}: açılış kalkanı kurulamadı, stop {real:.2f}'de bırakıldı: {e}")
        return None
    log(f"{symbol}: açılış kalkanı - stop {real:.2f} -> felaket seviyesi {disaster:.2f} "
        f"(gerçek seviye seans açılışından {execution['opening_shield_minutes']} dk sonra geri kurulacak).")
    return new_order


def restore_from_shield(
    client: AlpacaClient, symbol: str, side: str, qty: float, stop_order: dict, real: float,
) -> dict | None:
    """[2026-09-28 · Öneri 3] Kalkan süresi dolunca: fiyat gerçek stopun
    doğru tarafındaysa stop gerçek seviyeye geri çekilir ve güncel emir
    döner; kırılmışsa stop iptal edilip pozisyon market emriyle kapatılır
    (None döner). Market çıkışı başarısız olursa stop felaket seviyesinde
    yeniden kurulur - pozisyon hiçbir adımda korumasız kalmaz."""
    last_price = client.get_latest_trade_price(symbol)
    breached = last_price is not None and (last_price <= real if side == "long" else last_price >= real)
    if not breached:
        new_order = client.replace_stop_price(stop_order["id"], real, client_order_id=stop_tag("restore", symbol))
        log(f"{symbol}: açılış kalkanı bitti - stop gerçek seviyeye ({real:.2f}) geri çekildi "
            f"(son fiyat {last_price}).")
        return new_order
    if side != "long":
        # Sistem short açmıyor; yine de güvenli taraf: stopu gerçek seviyeye
        # çekmek piyasanın yanlış tarafında kalacağı için felakette bırak.
        log(f"{symbol}: short pozisyonda kalkan kırıldı, stop felaket seviyesinde bırakıldı.")
        return None
    disaster = float(stop_order["stop_price"])
    client.cancel_order(stop_order["id"])
    time.sleep(1)  # iptalin hisseleri serbest bırakması için
    try:
        client.place_market_exit(symbol, qty, client_order_id=shield_exit_tag(symbol))
        log(f"{symbol}: açılış kalkanı bitti, fiyat ({last_price:.2f}) gerçek stopun ({real:.2f}) altında - "
            f"pozisyon market emriyle kapatıldı.")
    except Exception as e:
        place_protective_stop(client, symbol, qty, side, disaster, client_order_id=shield_tag(symbol, real),
                              context="kalkan sonrası market çıkışı başarısız")
        log(f"{symbol}: kalkan sonrası market çıkışı başarısız ({e}), felaket stopu {disaster:.2f} yeniden kuruldu.")
    return None


def get_trend_daily_closes(client: AlpacaClient, symbol: str, trend_ema_period: int) -> list[float]:
    """Seçili stop algoritmasının trend filtresi (bkz. stop_algorithms._trend_ok)
    için günlük kapanışlar - trend_ema_period*4 günlük pencere (Stop Loss
    Ayarları'nda kullanıcının kaydettiği değer, yoksa stop_algorithms.
    TREND_EMA_PERIOD), DAILY_BARS_CACHE_PATH önbelleği üzerinden. Filtre
    kapalıysa (period<=0) boş liste döner (algoritma bunu "engelleme" olarak
    yorumlar)."""
    if trend_ema_period <= 0:
        return []
    start = datetime.now(timezone.utc) - timedelta(days=trend_ema_period * 4)
    raw_bars = get_cached_raw_bars(client, DAILY_BARS_CACHE_PATH, symbol, "1Day", start)
    return [b["c"] for b in raw_bars if _parse_iso(b["t"]) >= start]


def seconds_until_open(clock: dict) -> float:
    next_open = _parse_iso(clock["next_open"])
    return max(0.0, (next_open - datetime.now(timezone.utc)).total_seconds())


def last_trailed_stop_price(client: AlpacaClient, symbol: str, signed_qty: float | None = None) -> float | None:
    """En son (open/replaced/canceled/expired fark etmez) stop emrinin
    fiyatı - ama sadece yakın zamanda (EXTENDED_HOURS_RESTORE_MAX_AGE_DAYS
    içinde) kurulmuşsa; aksi halde None. manage_position, resting bir stop
    bulamadığında koruma seviyesini seçili algoritmanın naif ilk stop'una
    sıfırlamak yerine buradan trail edilmiş son seviyeyi geri yükleyebilmesi için var - en
    tipik senaryo, extended-hours guard'ın acil limit emrinin seans
    bitiminde dolmadan düşmesi.

    get_management_start'taki gibi, API'nin direction="desc" sıralamasına
    güvenmek yerine dönen kayıtlar arasından en yeni created_at'i elle
    buluyor. signed_qty verilirse yalnızca mevcut pozisyona ait stoplar
    sayılır (bkz. _belongs_to_position) - pozisyon öncesi emirler ve
    dolumla aynı anda iptal edilen bracket bacağı "son trail seviyesi"
    sayılmaz."""
    history, _ = _position_stop_history(client, symbol, signed_qty)
    if not history:
        return None
    latest = max(history, key=lambda o: _parse_iso(o["created_at"]))
    age = datetime.now(timezone.utc) - _parse_iso(latest["created_at"])
    if age > timedelta(days=EXTENDED_HOURS_RESTORE_MAX_AGE_DAYS):
        return None
    # [2026-09-28 · Öneri 3] Son emir açılış kalkanıysa, felaket seviyesi değil
    # etiketteki gerçek seviye "son trail edilmiş seviye"dir.
    real = parse_shield_real_stop(latest.get("client_order_id"))
    return real if real is not None else float(latest["stop_price"])


def _stop_bars_for_timeframe(
    client: AlpacaClient, symbol: str, timeframe: str, management_start: datetime,
) -> tuple[list[Bar], list[Bar]]:
    """[2026-09-28 · Öneri 1] (bars, history_bars): bars = yönetim
    başlangıcından bu yana, history_bars = ATR için giriş öncesini de kapsayan
    pencere - ikisi de stopun izlendiği periyotta (get_bars_for_timeframe,
    "1Day" için günlük cache). Günlükte giriş gününün barı da dahil olsun
    diye başlangıç o günün 00:00 UTC'sine çekilir."""
    if timeframe == "1Day":
        start = management_start.replace(hour=0, minute=0, second=0, microsecond=0)
        history_days = STOP_HISTORY_DAYS_DAILY
    else:
        start = management_start
        history_days = STOP_HISTORY_DAYS_INTRADAY
    history_start = start - timedelta(days=history_days)
    history_bars = get_bars_for_timeframe(client, symbol, timeframe, history_start, cache_file=INTRADAY_BARS_CACHE_PATH)
    bars = [b for b in history_bars if _parse_iso(b.t) >= start]
    return bars, history_bars


def notify_once_per_day(symbol: str, kind: str, msg: str) -> None:
    """Telegram uyarısı - aynı sembol ve tür için günde en fazla bir kez
    (stop botu birkaç dakikada bir çalıştığından aynı hata her geçişte
    tekrar ederdi). Son gönderim günü pozisyon yönetim önbelleğindeki
    sembol kaydında tutulur (storage: SQLite ya da JSON - yeni bir kayıt
    adı gerekmez) ve pozisyon kapanınca kayıtla birlikte silinir."""
    log(msg)
    today = datetime.now(ET).date().isoformat()
    cache = _load_management_start_cache()
    entry = cache.get(symbol) or {}
    alerts = entry.get("alerts") or {}
    if alerts.get(kind) == today:
        return
    bot_token, chat_id = load_telegram_settings()
    if not (bot_token and chat_id):
        return
    try:
        send_telegram_message(bot_token, chat_id, msg)
    except TelegramError as e:
        log(f"Telegram bildirimi gönderilemedi: {e}")
        return
    cache = _load_management_start_cache()  # gönderim sırasında başka bir alan değişmiş olabilir
    entry = cache.get(symbol) or {}
    entry["alerts"] = {**(entry.get("alerts") or {}), kind: today}
    cache[symbol] = entry
    _save_management_start_cache(cache)


def _wrong_side(level: float, last_price: float | None, side: str) -> bool:
    """Satış stopu piyasanın altında (alış stopu üstünde) olmalı - değilse
    Alpaca 422 ile reddeder."""
    if last_price is None:
        return False
    return level >= last_price if side == "long" else level <= last_price


def protective_stop(stop_price: float, entry_price: float, last_price: float | None, side: str) -> tuple[float, bool]:
    """[2026-10-03] Kurulacak stop güncel fiyatın yanlış tarafındaysa (Alpaca
    422 ile reddederdi) pozisyonu satmak yerine, stopun girişe olan mesafesi
    kadar GÜNCEL fiyatın ötesine taşınmış seviyeyi döner. (seviye, taşındı mı).
    manage_position ve modüllerin alış sonrası stop kurulumu (orb_core) aynı
    kuralı kullanır."""
    if not _wrong_side(stop_price, last_price, side):
        return stop_price, False
    return stop_beyond_price(last_price, stop_price, entry_price, side), True


def place_protective_stop(
    client: AlpacaClient, symbol: str, qty: float, side: str, stop_price: float,
    entry_price: float | None = None, client_order_id: str | None = None, context: str = "",
) -> dict:
    """[2026-10-03] Sistemdeki TÜM stop kurulumlarının ortak yolu - pozisyon
    korumasız kalmamalı. Önce güncel fiyata göre protective_stop uygulanır,
    sonra AlpacaClient.place_stop_order (Alpaca'nın kendi referans fiyatıyla
    yeniden deneme, geçici hatalarda tekrar) çağrılır. Seviye değiştiyse
    Telegram'dan haber verilir (günde bir). Yine de kurulamazsa KORUMASIZ
    uyarısı gönderilip hata fırlatılır - çağıranın kendi geri dönüş mantığı
    (ör. ilave alımda eski stopu geri kurma) çalışabilsin; trailing stop
    botu da bir sonraki geçişinde (Droplet'te 5 dk) yeniden dener."""
    try:
        last_price = client.get_latest_trade_price(symbol)
    except Exception:
        last_price = None
    requested = stop_price
    if entry_price is not None:
        stop_price, _ = protective_stop(stop_price, entry_price, last_price, side)
    try:
        order = client.place_stop_order(
            symbol, qty, side, stop_price, client_order_id=client_order_id, reference_price=entry_price,
        )
    except Exception as e:
        notify_once_per_day(
            symbol, "stop_failed",
            f"🚨 {symbol}: stop kurulamadı{f' ({context})' if context else ''}, pozisyon KORUMASIZ olabilir - "
            f"stop botu bir sonraki geçişte yeniden deneyecek: {e}",
        )
        raise
    placed = float(order.get("stop_price") or round(stop_price, 2))
    if round(placed, 2) != round(requested, 2):
        notify_once_per_day(
            symbol, "stop_moved",
            f"⚠️ {symbol}: istenen stop {requested:.2f} güncel fiyatın yanlış tarafındaydı"
            f"{f' ({context})' if context else ''}; stop {placed:.2f} seviyesine kuruldu.",
        )
    return order


def manage_position(
    client: AlpacaClient, pos: dict, top_up_stop_mode: str = TOP_UP_STOP_MODE_DEFAULT,
    stop_algorithm: str = DEFAULT_STOP_ALGORITHM, stop_settings: dict | None = None,
    timeframe: str | None = None, minutes_since_open: float | None = None,
) -> None:
    """timeframe: [2026-09-28 · Öneri 1] stopun izlendiği bar periyodu
    (resolve_stop_timeframe_for_position), verilmezse TIMEFRAME.
    minutes_since_open: [2026-09-28 · Öneri 3] seans açılışından beri geçen
    dakika (minutes_since_regular_open) - açılış kalkanı kararı için."""
    symbol = pos["symbol"]
    signed_qty = float(pos["qty"])
    qty = abs(signed_qty)
    side = "long" if signed_qty > 0 else "short"
    entry_price = float(pos["avg_entry_price"])
    algo = STOP_ALGORITHMS[stop_algorithm]
    timeframe = timeframe or TIMEFRAME
    # Stop Loss Ayarları sayfasında kaydedilmiş override'lar (bkz.
    # stop_loss_settings.py) - "shared" ATR/yapısal-trail ayarları + seçili
    # algoritmaya özgü ayarlar. Kaydedilmemiş bir alan için ilgili
    # fonksiyonun kod-varsayılanı geçerli olur (bkz. resolve_kwargs).
    stop_settings = stop_settings or {}
    shared_settings = stop_settings.get("shared") or {}
    algo_settings = stop_settings.get(stop_algorithm) or {}
    execution = load_execution_settings(stop_settings)
    shield_window = (
        bool(execution["opening_shield_enabled"]) and minutes_since_open is not None
        and minutes_since_open < float(execution["opening_shield_minutes"])
    )

    management_start = get_management_start(client, symbol, LOOKBACK_DAYS, signed_qty)
    bars, history_bars = _stop_bars_for_timeframe(client, symbol, timeframe, management_start)

    topped_up = False
    stop_order = client.get_open_stop_order(symbol)
    if stop_order is None:
        if client.has_open_exit_order(symbol, side):
            # Extended-hours guard'ın (run_extended_hours_guard) bıraktığı
            # day+extended_hours limit emri hâlâ resting olabilir - bu, normal
            # seansta da geçerliliğini koruyor (aynı takvim günü boyunca).
            # Üstüne ikinci bir stop denemek Alpaca'dan "insufficient qty
            # available" hatası alır, çünkü hisseler zaten o emir tarafından
            # tutuluyor. Koruma zaten var, dokunma.
            log(f"{symbol}: resting stop yok ama başka bir çıkış emri açık "
                f"(muhtemelen extended-hours guard), fallback stop atlanıyor.")
            return

        # bars=history_bars: ATR tabanlı ilk stop (atr_volatility) girişin
        # periyodundaki ATR'yi görebilsin - diğer algoritmalar bars'ı ya
        # yoksayar ya da (ORB/HA) kendi mantığıyla kullanır.
        naive_stop = algo.initial_stop(
            entry_price, side, bars=history_bars or None,
            **resolve_kwargs(algo.initial_stop, algo_settings, shared_settings),
        )
        restored = last_trailed_stop_price(client, symbol, signed_qty)
        try:
            last_price = client.get_latest_trade_price(symbol)
        except Exception:
            last_price = None
        if restored is not None and _wrong_side(restored, last_price, side):
            # [2026-10-03] Fiyat geri yüklenecek seviyenin zaten ötesinde - o
            # seviyeden stop Alpaca'ca reddedilir (MDB vakası). Naif stopa düşülür.
            log(f"{symbol}: son trail seviyesi {restored:.2f} güncel fiyatın ({last_price:.2f}) yanlış "
                f"tarafında, kullanılmıyor.")
            restored = None
        if restored is not None:
            # Extended-hours guard'ın bıraktığı acil limit emri seans
            # bitiminde dolmadan düşmüş olabilir - bu durumda structure
            # trail'in kazandırdığı ilerlemeyi seçili algoritmanın naif ilk
            # stop'una sıfırlamak yerine, son bilinen trail seviyesini geri
            # yüklüyoruz. İki
            # adaydan (restored, naive) gerçekten sıkı olanı seçiliyor -
            # aşağıdaki candidate seçimindeki "en çok sıkılaştıran" kuralıyla
            # aynı mantık.
            initial_stop = max(restored, naive_stop) if side == "long" else min(restored, naive_stop)
            reason = f"son trail edilmiş seviye ({restored:.2f}) geri yüklendi"
        else:
            initial_stop = naive_stop
            reason = "geçmişte yakın zamanlı bir stop yok, naif ilk stop"

        if _wrong_side(initial_stop, last_price, side):
            # [2026-10-03] Fiyat girişe göre hesaplanan naif stopun da ötesine
            # geçmiş (ör. boşluklu açılışta dolan limit emir). Pozisyonu
            # piyasa emriyle satmak yerine, naif stopun girişe olan mesafesi
            # kadar GÜNCEL fiyatın ötesine koruyucu bir stop kurulur ve haber
            # verilir - karar kullanıcıda kalır, pozisyon korumasız kalmaz.
            fallback, _ = protective_stop(naive_stop, entry_price, last_price, side)
            notify_once_per_day(
                symbol, "stop_breached",
                f"⚠️ {symbol}: koruma seviyesi ({initial_stop:.2f}) güncel fiyatın ({last_price:.2f}) yanlış "
                f"tarafında kaldı (giriş {entry_price:.2f}). Stop, güncel fiyattan aynı mesafede "
                f"{fallback:.2f} seviyesine kuruldu - pozisyonu gözden geçirin.",
            )
            initial_stop = fallback
            reason = "seviye kırılmıştı, güncel fiyata göre koruyucu stop"

        stop_order = place_protective_stop(
            client, symbol, qty, side, initial_stop, entry_price=entry_price,
            client_order_id=stop_tag("initial", symbol), context="stopsuz pozisyon",
        )
        initial_stop = float(stop_order.get("stop_price") or initial_stop)
        log(f"{symbol}: no resting stop found (not opened as a bracket order here, or "
            f"extended-hours guard emri seans bitiminde dolmadan düştü) - {reason}: "
            f"{initial_stop:.2f} (entry {entry_price:.2f}).")
    else:
        # [2026-09-28 · Öneri 3] Açılış kalkanı - adet yeniden boyutlandırmadan
        # ÖNCE bakılır: replace_stop_qty yeni emri etiketsiz oluşturur ve
        # etikette saklanan gerçek seviye kaybolurdu.
        real = parse_shield_real_stop(stop_order.get("client_order_id"))
        if real is not None:
            if shield_window:
                log(f"{symbol}: açılış kalkanı aktif (felaket stopu {float(stop_order['stop_price']):.2f}, "
                    f"gerçek {real:.2f}) - seansın ilk {execution['opening_shield_minutes']} dakikası bekleniyor.")
                return
            stop_order = restore_from_shield(client, symbol, side, qty, stop_order, real)
            if stop_order is None:
                return
        elif shield_window:
            # Guard seans dışında çalışamadıysa (GitHub Actions gecikmesi) kalkan
            # ilk 15 dakikada burada kurulur.
            if apply_opening_shield(client, symbol, side, stop_order, execution) is not None:
                return

        stop_qty = float(stop_order["qty"])
        if abs(stop_qty - qty) > 1e-9:
            # alpaca_buy_points.py's budget top-up (or a manual trade) changed
            # the position's total size - the resting stop must keep covering
            # the whole position, not just however many shares it was
            # originally sized for.
            stop_order = client.replace_stop_qty(stop_order["id"], qty)
            topped_up = True
            log(f"{symbol}: resized resting stop qty {stop_qty:g} -> {qty:g} (position size changed).")

    current_stop_price = float(stop_order["stop_price"])
    stop_order_id = stop_order["id"]

    if not bars:
        log(f"{symbol}: {timeframe} periyodunda bar yok, trail atlanıyor.")
        return

    # trend_ema_period, ctx için gereken günlük kapanış penceresini boyutlamak
    # için trail() çağrısından ÖNCE de gerekiyor (bkz. get_trend_daily_closes) -
    # bu yüzden resolve_kwargs'ın trail() için kuracağı kwargs'tan bağımsız,
    # doğrudan shared_settings'ten (kaydedilmemişse kod-varsayılanından) okunur.
    effective_trend_ema_period = int(shared_settings.get("trend_ema_period", DEFAULT_TREND_EMA_PERIOD))
    daily_closes = get_trend_daily_closes(client, symbol, effective_trend_ema_period)
    ctx = StopContext(
        side=side, entry_price=entry_price, current_stop_price=current_stop_price,
        bars=bars, daily_closes=daily_closes, topped_up=topped_up, top_up_stop_mode=top_up_stop_mode,
        initial_stop_price=get_initial_stop_price(client, symbol, signed_qty), history_bars=history_bars,
    )
    decision = algo.trail(ctx, **resolve_kwargs(algo.trail, algo_settings, shared_settings))
    if decision is None:
        return

    try:
        client.replace_stop_price(stop_order_id, decision.price, client_order_id=stop_tag(reason_code(decision.reason), symbol))
        log(f"{symbol}: trailed stop {current_stop_price:.2f} -> {decision.price:.2f} ({decision.reason}, {timeframe}).")
    except requests.HTTPError as e:
        log(f"{symbol}: failed to replace stop order: {e}")


def extended_hours_session(client: AlpacaClient) -> str | None:
    """'pre-market', 'after-hours' ya da None (normal seans, hafta sonu,
    resmi tatil, ya da 04:00-20:00 ET penceresinin bile dışında). O günün
    gerçek open/close saatini takvimden okur, böylece yarım günler (bayram
    arifeleri vb.) de doğru ele alınır - sadece pre-market/after-hours
    sınırları PRE_MARKET_START/AFTER_HOURS_END'e sabit (Alpaca'nın izin
    verdiği extended-hours penceresinin kendisi, günden güne değişmiyor)."""
    now_et = datetime.now(timezone.utc).astimezone(ET)
    if now_et.weekday() >= 5:
        return None

    today = now_et.date().isoformat()
    days = client.get_calendar(today, today)
    if not days:
        return None  # resmi tatil

    open_t = datetime.strptime(days[0]["open"], "%H:%M").time()
    close_t = datetime.strptime(days[0]["close"], "%H:%M").time()
    now_t = now_et.time()

    if PRE_MARKET_START <= now_t < open_t:
        return "pre-market"
    if close_t <= now_t < AFTER_HOURS_END:
        return "after-hours"
    return None


def load_telegram_settings() -> tuple[str | None, str | None]:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    settings = storage.load_json(NOTIFICATION_SETTINGS_PATH, {})
    chat_id = (settings.get("telegram_chat_id") or "").strip() or None
    return bot_token, chat_id


def _extended_hours_trail(
    client: AlpacaClient, symbol: str, side: str, entry_price: float, current_stop_price: float,
    stop_order_id: str, stop_algorithm: str, stop_settings: dict | None, signed_qty: float | None = None,
) -> None:
    """guard_position, stop hâlâ korumadaysa (kırılmamışsa) normalde hiçbir
    şey yapmıyordu - ama bu, pre-market/after-hours'ta fiyat LEHE hareket
    etse bile (ör. breakeven eşiği aşılsa da) stopun bir sonraki normal
    seansa kadar hiç sıkılaştırılamaması demekti (manage_position'ın trail()
    çağrısı sadece normal seans cron'unda, 13:00-21:00 UTC'de çalışıyor).
    Bu fonksiyon aynı boşluğu kapatıyor: manage_position'ın normal seansta
    yaptığı BİREBİR AYNI trail() çağrısını burada da yapıyor, tek fark bar
    penceresinin include_extended_hours=True ile çekilmesi - aksi halde
    "son bar"ın kapanışı (ctx.bars[-1].c) hep dünkü kapanışta donuk kalır ve
    pre-market'teki fiyat hareketi trail() tarafından hiç görülmez.
    Sadece replace_stop_price çağırır - breach/emergency-limit mantığına
    (guard_position'ın geri kalanı) hiç dokunmaz, resting emrin TİPİNİ
    (stop) değiştirmez; trail() zaten "sadece sıkılaştır" kuralını kendi
    içinde uyguladığı için burada ayrı bir improve kontrolüne gerek yok."""
    algo = STOP_ALGORITHMS[stop_algorithm]
    stop_settings = stop_settings or {}
    shared_settings = stop_settings.get("shared") or {}
    algo_settings = stop_settings.get(stop_algorithm) or {}

    management_start = get_management_start(client, symbol, LOOKBACK_DAYS, signed_qty)
    bars = get_regular_hours_bars(
        client, symbol, TIMEFRAME, management_start,
        cache_file=INTRADAY_BARS_CACHE_PATH, include_extended_hours=True,
    )
    if not bars:
        return

    effective_trend_ema_period = int(shared_settings.get("trend_ema_period", DEFAULT_TREND_EMA_PERIOD))
    daily_closes = get_trend_daily_closes(client, symbol, effective_trend_ema_period)
    ctx = StopContext(
        side=side, entry_price=entry_price, current_stop_price=current_stop_price,
        bars=bars, daily_closes=daily_closes,
    )
    decision = algo.trail(ctx, **resolve_kwargs(algo.trail, algo_settings, shared_settings))
    if decision is None:
        return

    try:
        client.replace_stop_price(stop_order_id, decision.price)
        log(f"{symbol}: extended-hours trail {current_stop_price:.2f} -> {decision.price:.2f} ({decision.reason}).")
    except requests.HTTPError as e:
        log(f"{symbol}: extended-hours'ta stop güncellenemedi: {e}")


def guard_position(
    client: AlpacaClient, pos: dict, bot_token: str | None, chat_id: str | None,
    stop_algorithm: str = DEFAULT_STOP_ALGORITHM, stop_settings: dict | None = None,
) -> None:
    # [2026-09-28 · Öneri 3] Açılış kalkanı açıkken (varsayılan) guard, resting
    # stopu önce felaket seviyesine genişletir (apply_opening_shield) ve
    # kırılma kontrolünü O seviyeye göre yapar: gerçek stopun seans dışında
    # düşük hacimle kırılması artık tek başına satış sebebi değil - karar
    # seans açılışından 15 dk sonra manage_position/restore_from_shield'de
    # verilir. Seans dışı trail (_extended_hours_trail) varsayılan olarak kapalı.
    """Normal bir "stop" emri şu an (extended hours) tetiklenemeyeceği için,
    fiyat zaten stop seviyesini kırmışsa onun yerine geçebilecek tek şeyi -
    day+extended_hours bir limit emri - gönderir.

    İki durumu ayrı ayrı ele alır:

    1. Resting bir stop var ve kırılmış: onu iptal edip yerine marketable
       (küçük bir kayma payıyla) bir limit-sell gönderir - tıpkı önceki
       davranış.
    2. Ne resting bir stop ne de bu guard'ın bıraktığı başka bir çıkış emri
       var (has_open_exit_order) - yani önceki acil limit emri seans
       bitiminde dolmadan düşmüş ve pozisyon şu an TAMAMEN korumasız. Bunu
       bir sonraki normal seans açılışına (manage_position'ın
       last_trailed_stop_price ile geri yüklemesine) bırakmak yerine,
       last_trailed_stop_price'tan (yeterince yakın zamanlıysa) aynı
       seviyeyi hemen geri kurar: fiyat o seviyeyi henüz kırmamışsa normal
       bir GTC stop emri (Alpaca seans dışında kabul edip sıraya alır; bu
       guard onu bir sonraki çalışmasında resting stop olarak izler -
       piyasanın altındaki bir limit-sell ise beklemez, anında dolardı);
       zaten kırmışsa aynı 1. durumdaki gibi marketable bir limit. Böylece kör
       bölge, bir sonraki iş gününü değil, bir sonraki guard çalışmasını
       (mevcut cron ile ~10 dk) bekliyor.

    Stop hâlâ korumadaysa (henüz kırılmamışsa) VE resting bir stop varsa,
    breach/emergency-limit mantığına hiç girmeden _extended_hours_trail'i
    dener - fiyat pre-market/after-hours'ta lehe hareket ettiyse (breakeven,
    yapısal trail) stopu sıkılaştırır; hiçbir aday uygulanmıyorsa (henüz
    eşik aşılmadıysa) o da hiçbir şey yapmaz.

    Kalan sınır: last_trailed_stop_price hiçbir güvenilir geçmiş bulamazsa
    (gerçekten hiç stop'u olmamış bir pozisyon, ya da geçmiş
    EXTENDED_HOURS_RESTORE_MAX_AGE_DAYS'ten daha eski) burada da yapacak bir
    şey yok - bu durumda kör bölge yine bir sonraki normal seansı bekler."""
    symbol = pos["symbol"]
    signed_qty = float(pos["qty"])
    qty = abs(signed_qty)
    side = "long" if signed_qty > 0 else "short"
    entry_price = float(pos["avg_entry_price"])

    execution = load_execution_settings(stop_settings or {})
    shield_enabled = bool(execution["opening_shield_enabled"])
    real_stop = None  # kalkan açıkken etiketteki gerçek seviye

    stop_order = client.get_open_stop_order(symbol)
    if stop_order is not None:
        real_stop = parse_shield_real_stop(stop_order.get("client_order_id"))
        if shield_enabled and real_stop is None:
            shielded = apply_opening_shield(client, symbol, side, stop_order, execution)
            if shielded is not None:
                real_stop = float(stop_order["stop_price"])
                stop_order = shielded
        reference_price = float(stop_order["stop_price"])
        resting_order_id = stop_order["id"]
    elif client.has_open_exit_order(symbol, side):
        return  # guard'ın önceki acil emri hâlâ resting - dokunma
    else:
        reference_price = last_trailed_stop_price(client, symbol, signed_qty)
        if reference_price is None:
            return  # ne resting emir ne güvenilir geçmiş var - yapacak bir şey yok
        if shield_enabled:
            real_stop = reference_price
            reference_price = round(shield_disaster_price(real_stop, side, execution), 2)
        resting_order_id = None

    last_price = client.get_latest_trade_price(symbol)
    if last_price is None:
        return

    breached = last_price <= reference_price if side == "long" else last_price >= reference_price
    if not breached:
        if resting_order_id is not None:
            if execution["extended_hours_trail_enabled"] and real_stop is None:
                _extended_hours_trail(
                    client, symbol, side, entry_price, reference_price, resting_order_id, stop_algorithm, stop_settings,
                    signed_qty,
                )
            return
        # Kör bölgeyi kapatan proaktif adım: henüz kırılmamış, seviyeyi normal
        # bir GTC stop emri olarak geri kuruyoruz. Burada BİLEREK referans
        # seviyesinde bir limit-sell kullanılmıyor: piyasanın altındaki bir
        # limit-sell beklemez, anında en iyi alış fiyatından dolar - pozisyonu
        # korumak yerine hemen kapatırdı (bkz. AMAT, 2026-09-24,
        # alpaca_buy_points.run_extended_hours_entry_scan'daki aynı not).
        # Alpaca seans dışında gönderilen stop'u kabul edip sıraya alıyor;
        # bir sonraki guard çalışması onu resting stop olarak görür (kırılırsa
        # marketable limite çevirir), normal seans açılınca da kendisi çalışır.
        try:
            tag = shield_tag(symbol, real_stop) if real_stop is not None else stop_tag("restore", symbol)
            client.place_stop_order(symbol, qty, side, reference_price, client_order_id=tag, reference_price=entry_price)
        except Exception as e:
            msg = (f"🚨 {symbol}: önceki acil koruma emri dolmadan düşmüştü, stop {reference_price:.2f} "
                   f"seviyesinden yeniden kurulamadı, pozisyon şu an KORUMASIZ olabilir: {e}")
        else:
            msg = (f"⚠️ {symbol}: önceki acil koruma emri dolmadan düşmüştü, pozisyon KORUMASIZ kalmıştı. "
                   f"Son fiyat {last_price:.2f} son bilinen seviyeyi ({reference_price:.2f}) henüz aşmamış - "
                   f"aynı seviyeden GTC stop yeniden kuruldu (seans dışında bu guard izliyor).")
        log(msg)
        if bot_token and chat_id:
            try:
                send_telegram_message(bot_token, chat_id, msg)
            except TelegramError as e:
                log(f"Telegram bildirimi gönderilemedi: {e}")
        return
    elif side == "long":
        limit_price = round(min(reference_price, last_price) * (1 - EXTENDED_HOURS_SLIPPAGE_PCT), 2)
    else:
        limit_price = round(max(reference_price, last_price) * (1 + EXTENDED_HOURS_SLIPPAGE_PCT), 2)

    try:
        if resting_order_id is not None:
            client.cancel_order(resting_order_id)
            time.sleep(1)  # cancel'ın hisseleri serbest bırakması için kısa bir pay
        client.place_extended_hours_limit(symbol, qty, side, limit_price)
    except requests.HTTPError as e:
        # Varsa resting emir muhtemelen zaten iptal oldu ama yerine emir
        # konamadı - pozisyon şu an gerçekten korumasız. Bunu sessizce
        # geçmek yerine açıkça bildiriyoruz; sıradaki guard çalışması
        # (birkaç dakika içinde) tekrar dener.
        msg = (
            f"🚨 {symbol}: stop kırıldı ({last_price:.2f} vs {reference_price:.2f}) ama extended-hours "
            f"limit emri gönderilirken hata alındı, pozisyon şu an KORUMASIZ olabilir: {e}"
        )
        log(msg)
        if bot_token and chat_id:
            try:
                send_telegram_message(bot_token, chat_id, msg)
            except TelegramError:
                pass
        return

    if resting_order_id is not None:
        msg = (
            f"🚨 {symbol}: extended hours'ta son fiyat {last_price:.2f}, stopu ({reference_price:.2f}) kırdı. "
            f"Normal stop bu seansta çalışamayacağı için iptal edildi; yerine day+extended-hours "
            f"limit emri {limit_price:.2f} seviyesinden gönderildi. Dolarsa pozisyon kapanır; "
            f"dolmadan seans biterse emir düşer ve pozisyon tekrar korumasız kalır (bir sonraki guard "
            f"çalışması bunu last_trailed_stop_price ile yeniden kurmayı dener)."
        )
    else:
        msg = (
            f"🚨 {symbol}: önceki acil koruma emri dolmadan düşmüştü, pozisyon KORUMASIZ kalmıştı. "
            f"Son fiyat {last_price:.2f}, son bilinen seviyeyi ({reference_price:.2f}) hâlâ aşmış "
            f"durumda - yerine day+extended-hours limit emri {limit_price:.2f} seviyesinden gönderildi."
        )
    log(msg)
    if bot_token and chat_id:
        try:
            send_telegram_message(bot_token, chat_id, msg)
        except TelegramError as e:
            log(f"Telegram bildirimi gönderilemedi: {e}")


def run_extended_hours_guard(client: AlpacaClient) -> None:
    session = extended_hours_session(client)
    if session is None:
        log("Extended-hours penceresi dışında (normal seans/hafta sonu/tatil), çıkılıyor.")
        return

    positions = [p for p in client.get_all_positions() if p.get("asset_class") == "us_equity"]
    if not positions:
        log(f"{session}: açık pozisyon yok.")
        return

    bot_token, chat_id = load_telegram_settings()
    # run_once ile aynı - _extended_hours_trail'in her pozisyon için doğru
    # (sembole özel override varsa onu, yoksa portföy-geneli varsayılanı -
    # ya da pozisyon Relative Strength Rotasyonu'na/ORB'a aitse O modülün
    # seçtiği algoritmayı, bkz. resolve_stop_algorithm_for_position) stop
    # algoritmasıyla çalışmasını sağlar.
    config = load_portfolio_config()
    stop_settings = load_stop_loss_settings()
    live_symbols = {p["symbol"] for p in positions}
    rs_holdings, rs_config, orb_holdings, orb_config, ha_holdings, ha_config = (
        _load_cross_module_holdings_pruned(live_symbols)
    )
    for pos in positions:
        stop_algorithm = resolve_stop_algorithm_for_position(
            config, rs_holdings, rs_config, orb_holdings, orb_config, pos["symbol"], ha_holdings, ha_config,
        )
        guard_position(client, pos, bot_token, chat_id, stop_algorithm, stop_settings)


def run_once(client: AlpacaClient) -> None:
    clock = client.get_clock()
    if not clock["is_open"]:
        log("Market closed, nothing to do.")
        return

    positions = [p for p in client.get_all_positions() if p.get("asset_class") == "us_equity"]
    prune_management_start_cache({p["symbol"] for p in positions})
    if not positions:
        log("No open equity positions.")
        return

    config = load_portfolio_config()
    top_up_stop_mode = load_top_up_stop_mode(config)
    stop_settings = load_stop_loss_settings()
    live_symbols = {p["symbol"] for p in positions}
    rs_holdings, rs_config, orb_holdings, orb_config, ha_holdings, ha_config = (
        _load_cross_module_holdings_pruned(live_symbols)
    )
    minutes_since_open = minutes_since_regular_open(client)
    for pos in positions:
        stop_algorithm = resolve_stop_algorithm_for_position(
            config, rs_holdings, rs_config, orb_holdings, orb_config, pos["symbol"], ha_holdings, ha_config,
        )
        timeframe = resolve_stop_timeframe_for_position(config, rs_holdings, orb_holdings, ha_holdings, pos["symbol"])
        try:
            manage_position(
                client, pos, top_up_stop_mode, stop_algorithm, stop_settings,
                timeframe=timeframe, minutes_since_open=minutes_since_open,
            )
        except Exception as e:
            # Bir pozisyondaki hata diğerlerinin stop yönetimini durdurmasın.
            # [2026-10-03] Önceden sadece log'a yazılıyordu - MDB stopu bu yüzden
            # ~29 saat sessizce kurulamadı. Artık günde bir Telegram uyarısı.
            notify_once_per_day(
                pos["symbol"], "manage_failed",
                f"🚨 {pos['symbol']}: stop yönetimi başarısız, pozisyon KORUMASIZ olabilir: {e}",
            )


def run_loop(client: AlpacaClient) -> None:
    log(f"Starting trailing-stop loop (timeframe={TIMEFRAME}).")
    while True:
        clock = client.get_clock()
        if not clock["is_open"]:
            wait_s = min(seconds_until_open(clock), 900)
            log(f"Market closed. Sleeping {int(wait_s)}s.")
            time.sleep(wait_s)
            continue

        run_once(client)
        time.sleep(POLL_SECONDS)


def build_client() -> AlpacaClient:
    key_id = os.environ["APCA_API_KEY_ID"]
    secret_key = os.environ["APCA_API_SECRET_KEY"]
    trading_url = os.environ.get("APCA_API_BASE_URL", DEFAULT_TRADING_URL)
    data_url = os.environ.get("APCA_API_DATA_URL", DEFAULT_DATA_URL)
    return AlpacaClient(key_id, secret_key, trading_url, data_url)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit (used by GitHub Actions).")
    parser.add_argument(
        "--extended-hours-guard", action="store_true",
        help="Pre-market/after-hours'ta stopu kırılmış pozisyonlar için day+extended_hours "
             "limit emri gönder ve çık (ayrı bir GitHub Actions workflow'u tarafından kullanılır).",
    )
    args = parser.parse_args()

    client = build_client()
    try:
        if args.extended_hours_guard:
            run_extended_hours_guard(client)
        elif args.once:
            run_once(client)
        else:
            run_loop(client)
    except KeyboardInterrupt:
        sys.exit(0)
