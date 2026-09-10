"""
Structure-based trailing-stop bot for Alpaca paper trading.

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
and re-establishes protection at the same level right away, marketable if
price is still through it or passive (like a stand-in stop) if not. Only if
that history isn't recent enough to trust either (see its own docstring)
does the gap actually widen to the next regular session, where point 1
above restores the same way.

Run with --once for a single pass (used by the GitHub Actions workflow,
which handles the scheduling). Without --once it loops locally, sleeping
between passes and until the market reopens. --extended-hours-guard runs the
separate mechanism described above and exits.
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

from alpaca_client import AlpacaClient, DEFAULT_TRADING_URL, DEFAULT_DATA_URL
from indicators import atr, ema
from structure import Bar, validated_trailing_level
from telegram_notify import TelegramError, send_telegram_message

load_dotenv()

TIMEFRAME = os.environ.get("TRADE_TIMEFRAME", "30Min")
SWING_ORDER = int(os.environ.get("TRADE_SWING_ORDER", "2"))
INITIAL_STOP_PCT = float(os.environ.get("TRADE_INITIAL_STOP_PCT", "1.5")) / 100
POLL_SECONDS = int(os.environ.get("TRADE_POLL_SECONDS", "60"))

LOOKBACK_DAYS = int(os.environ.get("TRADE_LOOKBACK_DAYS", "15"))
ATR_PERIOD = int(os.environ.get("TRADE_ATR_PERIOD", "14"))
ATR_MULTIPLIER = float(os.environ.get("TRADE_ATR_MULTIPLIER", "0.25"))
BREAKEVEN_TRIGGER_PCT = float(os.environ.get("TRADE_BREAKEVEN_TRIGGER_PCT", "1.0")) / 100
STALE_REFERENCE_DAYS = float(os.environ.get("TRADE_STALE_REFERENCE_DAYS", "10"))
TREND_EMA_PERIOD = int(os.environ.get("TRADE_TREND_EMA_PERIOD", "50"))

FALLBACK_BUFFER_PCT = 0.001  # only used if ATR can't be computed yet (too few bars)

# alpaca_buy_points.py'nin okuduğu aynı dosya (Premium Buy Point modülünde
# write_portfolio_config ile commit edilir) - tek kullanıcı (berkakar)
# varsayımı burada da geçerli.
CONFIG_PATH = "portfolio_config_berkakar.json"
TOP_UP_STOP_MODE_DEFAULT = "keep"

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

ET = ZoneInfo("America/New_York")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def load_top_up_stop_mode() -> str:
    """Premium Buy Point modülünde kullanıcının seçtiği, ilave alım (top-up)
    sonrası resting stop davranışı - "keep" (varsayılan: sadece adet
    genişler, fiyat seviyesi değişmez) veya "tighten_to_new_entry" (yeni
    ortalama giriş fiyatına göre bir nefes payı adayı da eklenir - bkz.
    manage_position, sadece stop'u sıkılaştırırsa uygulanır)."""
    if not os.path.exists(CONFIG_PATH):
        return TOP_UP_STOP_MODE_DEFAULT
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)
    return config.get("top_up_stop_mode") or TOP_UP_STOP_MODE_DEFAULT


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
) -> list[Bar]:
    raw_bars = client.get_raw_bars(symbol, timeframe, start.isoformat())

    bars = []
    for b in raw_bars:
        ts = _parse_iso(b["t"]).astimezone(ET)
        if ts.weekday() >= 5:
            continue
        if not (9, 30) <= (ts.hour, ts.minute) < (16, 0):
            continue
        bars.append(Bar(t=b["t"], o=b["o"], h=b["h"], l=b["l"], c=b["c"], v=b["v"]))

    if exclude_forming and bars:
        last_start = _parse_iso(bars[-1].t)
        if last_start + _timeframe_duration(timeframe) > datetime.now(timezone.utc):
            bars.pop()  # still-forming bar - buy-point signals shouldn't chase intra-bar noise

    return bars


def get_management_start(client: AlpacaClient, symbol: str, lookback_days: int) -> datetime:
    """The earlier of `lookback_days` ago and when we first started
    managing this position's stop - whichever is more recent wins, so
    structure from before we ever held the trade can't anchor the
    reference point."""
    default_start = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    history = client.get_stop_order_history(symbol, limit=200)
    if not history:
        return default_start
    earliest = min(_parse_iso(o["created_at"]) for o in history)
    return max(default_start, earliest)


def check_trend_filter(client: AlpacaClient, symbol: str, side: str) -> bool:
    """True if the higher-timeframe (daily) trend still supports trailing
    further in this direction. Defaults to True (don't block) when there
    isn't enough daily history yet, or the filter is disabled (period<=0)."""
    if TREND_EMA_PERIOD <= 0:
        return True

    start = datetime.now(timezone.utc) - timedelta(days=TREND_EMA_PERIOD * 4)
    raw_bars = client.get_raw_bars(symbol, "1Day", start.isoformat())
    closes = [b["c"] for b in raw_bars]
    trend_ema = ema(closes, TREND_EMA_PERIOD)
    if trend_ema is None:
        return True

    last_close = closes[-1]
    return last_close > trend_ema if side == "long" else last_close < trend_ema


def seconds_until_open(clock: dict) -> float:
    next_open = _parse_iso(clock["next_open"])
    return max(0.0, (next_open - datetime.now(timezone.utc)).total_seconds())


def last_trailed_stop_price(client: AlpacaClient, symbol: str) -> float | None:
    """En son (open/replaced/canceled/expired fark etmez) stop emrinin
    fiyatı - ama sadece yakın zamanda (EXTENDED_HOURS_RESTORE_MAX_AGE_DAYS
    içinde) kurulmuşsa; aksi halde None. manage_position, resting bir stop
    bulamadığında koruma seviyesini INITIAL_STOP_PCT'e sıfırlamak yerine
    buradan trail edilmiş son seviyeyi geri yükleyebilmesi için var - en
    tipik senaryo, extended-hours guard'ın acil limit emrinin seans
    bitiminde dolmadan düşmesi.

    get_management_start'taki gibi, API'nin direction="desc" sıralamasına
    güvenmek yerine dönen kayıtlar arasından en yeni created_at'i elle
    buluyor."""
    history = client.get_stop_order_history(symbol)
    if not history:
        return None
    latest = max(history, key=lambda o: _parse_iso(o["created_at"]))
    age = datetime.now(timezone.utc) - _parse_iso(latest["created_at"])
    if age > timedelta(days=EXTENDED_HOURS_RESTORE_MAX_AGE_DAYS):
        return None
    return float(latest["stop_price"])


def manage_position(client: AlpacaClient, pos: dict, top_up_stop_mode: str = TOP_UP_STOP_MODE_DEFAULT) -> None:
    symbol = pos["symbol"]
    signed_qty = float(pos["qty"])
    qty = abs(signed_qty)
    side = "long" if signed_qty > 0 else "short"
    entry_price = float(pos["avg_entry_price"])

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

        naive_stop = entry_price * (1 - INITIAL_STOP_PCT) if side == "long" else entry_price * (1 + INITIAL_STOP_PCT)
        restored = last_trailed_stop_price(client, symbol)
        if restored is not None:
            # Extended-hours guard'ın bıraktığı acil limit emri seans
            # bitiminde dolmadan düşmüş olabilir - bu durumda structure
            # trail'in kazandırdığı ilerlemeyi INITIAL_STOP_PCT'e sıfırlamak
            # yerine, son bilinen trail seviyesini geri yüklüyoruz. İki
            # adaydan (restored, naive) gerçekten sıkı olanı seçiliyor -
            # aşağıdaki candidate seçimindeki "en çok sıkılaştıran" kuralıyla
            # aynı mantık.
            initial_stop = max(restored, naive_stop) if side == "long" else min(restored, naive_stop)
            reason = f"son trail edilmiş seviye ({restored:.2f}) geri yüklendi"
        else:
            initial_stop = naive_stop
            reason = "geçmişte yakın zamanlı bir stop yok, naif ilk stop"

        stop_order = client.place_stop_order(symbol, qty, side, initial_stop)
        log(f"{symbol}: no resting stop found (not opened as a bracket order here, or "
            f"extended-hours guard emri seans bitiminde dolmadan düştü) - {reason}: "
            f"{initial_stop:.2f} (entry {entry_price:.2f}).")
    else:
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

    management_start = get_management_start(client, symbol, LOOKBACK_DAYS)
    bars = get_regular_hours_bars(client, symbol, TIMEFRAME, management_start)
    if not bars:
        log(f"{symbol}: no regular-hours bars available, skipping.")
        return

    last_price = bars[-1].c
    candidates = []  # (price, reason) - the caller picks whichever tightens the stop most

    if side == "long":
        gain_pct = (last_price - entry_price) / entry_price
    else:
        gain_pct = (entry_price - last_price) / entry_price

    if gain_pct >= BREAKEVEN_TRIGGER_PCT:
        if side == "long" and entry_price > current_stop_price and entry_price < last_price:
            candidates.append((entry_price, "breakeven"))
        elif side == "short" and entry_price < current_stop_price and entry_price > last_price:
            candidates.append((entry_price, "breakeven"))

    if topped_up and top_up_stop_mode == "tighten_to_new_entry":
        # Premium Buy Point modülünde kullanıcının seçtiği tercih: top-up,
        # pozisyonun ortalama giriş fiyatını değiştirmiş olabilir - yeni
        # ortalamaya göre bir INITIAL_STOP_PCT nefes payı adayı da eklenir.
        # Aşağıdaki "en çok sıkılaştıran"+improves seçimi sayesinde bu aday
        # mevcut korumayı asla gevşetmez, sadece sıkılaştırabilir.
        if side == "long":
            top_up_candidate = entry_price * (1 - INITIAL_STOP_PCT)
            if top_up_candidate < last_price:
                candidates.append((top_up_candidate, "top-up nefes payı"))
        else:
            top_up_candidate = entry_price * (1 + INITIAL_STOP_PCT)
            if top_up_candidate > last_price:
                candidates.append((top_up_candidate, "top-up nefes payı"))

    if check_trend_filter(client, symbol, side):
        pivot = validated_trailing_level(bars, side, SWING_ORDER, STALE_REFERENCE_DAYS)
        if pivot is not None:
            atr_value = atr(bars, ATR_PERIOD)
            buffer_amount = atr_value * ATR_MULTIPLIER if atr_value is not None else pivot.price * FALLBACK_BUFFER_PCT

            if side == "long":
                candidate = pivot.price - buffer_amount
                if candidate < last_price:
                    candidates.append((candidate, f"structure@{pivot.price:.2f}"))
            else:
                candidate = pivot.price + buffer_amount
                if candidate > last_price:
                    candidates.append((candidate, f"structure@{pivot.price:.2f}"))

    if not candidates:
        return

    if side == "long":
        best_price, reason = max(candidates, key=lambda c: c[0])
        improves = best_price > current_stop_price
    else:
        best_price, reason = min(candidates, key=lambda c: c[0])
        improves = best_price < current_stop_price

    if not improves:
        return

    try:
        client.replace_stop_price(stop_order_id, best_price)
        log(f"{symbol}: trailed stop {current_stop_price:.2f} -> {best_price:.2f} ({reason}).")
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
    chat_id = None
    if os.path.exists(NOTIFICATION_SETTINGS_PATH):
        with open(NOTIFICATION_SETTINGS_PATH, encoding="utf-8") as f:
            chat_id = (json.load(f).get("telegram_chat_id") or "").strip() or None
    return bot_token, chat_id


def guard_position(client: AlpacaClient, pos: dict, bot_token: str | None, chat_id: str | None) -> None:
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
       seviyeyi hemen geri kurar: fiyat o seviyeyi henüz kırmamışsa sadece
       normal bir stop'un yerini tutacak pasif bir limit (tam o seviyeden,
       kayma payı gerekmiyor - zaten tetiklenmiş bir kırılma yok); zaten
       kırmışsa aynı 1. durumdaki gibi marketable bir limit. Böylece kör
       bölge, bir sonraki iş gününü değil, bir sonraki guard çalışmasını
       (mevcut cron ile ~10 dk) bekliyor.

    Stop hâlâ korumadaysa (henüz kırılmamışsa) ya da guard'ın önceki emri
    hâlâ resting'se hiçbir şey yapmaz.

    Kalan sınır: last_trailed_stop_price hiçbir güvenilir geçmiş bulamazsa
    (gerçekten hiç stop'u olmamış bir pozisyon, ya da geçmiş
    EXTENDED_HOURS_RESTORE_MAX_AGE_DAYS'ten daha eski) burada da yapacak bir
    şey yok - bu durumda kör bölge yine bir sonraki normal seansı bekler."""
    symbol = pos["symbol"]
    signed_qty = float(pos["qty"])
    qty = abs(signed_qty)
    side = "long" if signed_qty > 0 else "short"

    stop_order = client.get_open_stop_order(symbol)
    if stop_order is not None:
        reference_price = float(stop_order["stop_price"])
        resting_order_id = stop_order["id"]
    elif client.has_open_exit_order(symbol, side):
        return  # guard'ın önceki acil emri hâlâ resting - dokunma
    else:
        reference_price = last_trailed_stop_price(client, symbol)
        if reference_price is None:
            return  # ne resting emir ne güvenilir geçmiş var - yapacak bir şey yok
        resting_order_id = None

    last_price = client.get_latest_trade_price(symbol)
    if last_price is None:
        return

    breached = last_price <= reference_price if side == "long" else last_price >= reference_price
    if not breached:
        if resting_order_id is not None:
            return  # resting stop hâlâ korumada ve kırılmamış - yapacak bir şey yok
        # Kör bölgeyi kapatan proaktif adım: henüz kırılmamış, sadece normal
        # stop'un yerini tutacak pasif bir limit koyuyoruz - tam referans
        # seviyesinden (bir stop da zaten tam o fiyattan tetiklenirdi).
        limit_price = round(reference_price, 2)
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
    elif breached:
        msg = (
            f"🚨 {symbol}: önceki acil koruma emri dolmadan düşmüştü, pozisyon KORUMASIZ kalmıştı. "
            f"Son fiyat {last_price:.2f}, son bilinen seviyeyi ({reference_price:.2f}) hâlâ aşmış "
            f"durumda - yerine day+extended-hours limit emri {limit_price:.2f} seviyesinden gönderildi."
        )
    else:
        msg = (
            f"⚠️ {symbol}: önceki acil koruma emri dolmadan düşmüştü, pozisyon KORUMASIZ kalmıştı. "
            f"Son fiyat {last_price:.2f} son bilinen seviyeyi ({reference_price:.2f}) henüz aşmamış - "
            f"yerine (stop'un yerini tutacak) day+extended-hours limit emri aynı seviyeden yeniden kuruldu."
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
    for pos in positions:
        guard_position(client, pos, bot_token, chat_id)


def run_once(client: AlpacaClient) -> None:
    clock = client.get_clock()
    if not clock["is_open"]:
        log("Market closed, nothing to do.")
        return

    positions = [p for p in client.get_all_positions() if p.get("asset_class") == "us_equity"]
    if not positions:
        log("No open equity positions.")
        return

    top_up_stop_mode = load_top_up_stop_mode()
    for pos in positions:
        manage_position(client, pos, top_up_stop_mode)


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
