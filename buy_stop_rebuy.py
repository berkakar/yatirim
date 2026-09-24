"""
Alım-Stop-Alım Ek Yeteneği: Premium Buy Point portföyünde bir pozisyon kendi
stop'u tetiklenerek (Zarar Kes değil - stop-loss emri) kapandığında, fiyatın
gelen barlarda tekrar önceki alım (giriş) fiyatına ulaşıp ulaşmadığını izler
ve ulaşırsa aynı sembolü yeniden satın alır. Bu, portfolio_config_berkakar.json
içindeki "buy_stop_rebuy_enabled" (varsayılan kapalı) ve
"buy_stop_rebuy_window_hours" (varsayılan 2 saat, premium_buy_portfolio.py'de
bir metin kutusunda ayarlanır) ile kontrol edilir.

Sadece gün-içi mum periyotlarında (15Min/30Min/1Hour) çalışır - "1 günlük
barlarda uygulanamasın" kuralı ELIGIBLE_TIMEFRAME_MINUTES'te uygulanır; bir
sembolün seçili periyodu "1Day" ise hiç izlenmez. Zaman penceresi
(window_hours), o sembolün periyoduna göre bir bar sayısına çevrilir (bkz.
window_bar_count) - ör. varsayılan 2 saat, 1 saatlik barda 2 bar, 15 dakikalık
barda 8 bar demektir.

İki aşamalı bir durum makinesi (buy_stop_rebuy_state_berkakar.json'da
saklanır):
  - "tracked": şu an açık, izlenebilir (gün-içi periyotlu, resting stopu olan)
    her pozisyon için giriş fiyatı + güncel stop emri id'si - HER pass'te
    tazelenir (trailing-stop bir stop'u "replace" ettiğinde Alpaca yeni bir
    emir id'si üretir - bkz. alpaca_client.get_stop_order_history'nin
    docstring'i - bu yüzden en güncel id'yi bilmek için her pass'te yeniden
    okunur).
  - "pending": stop'u tetiklenerek kapanmış (tracked'teki son bilinen stop
    emrinin durumu "filled" olan), fiyatın giriş seviyesine dönüşü izlenen
    semboller - giriş fiyatı + periyot + stop anının zamanı tutulur, her
    pass'te SIFIRDAN (kalıcı bir "kaç bar geçti" sayacı TUTULMADAN) o andan
    beri kapanmış barlar yeniden çekilip değerlendirilir; bu da mantığı basit
    ve kendi kendini onaran tutar.

Otomatik akışta (GitHub Actions, --once) alpaca_buy_points.py ve
alpaca_trailing_stop.py'den SONRA çalışır - böylece aynı pass içinde önce
tazelenen pozisyon/stop durumunu kullanır. Yeniden alım, tıpkı ilave alımda
(alpaca_buy_points.check_symbol) olduğu gibi anında bir market emriyle
doldurulur (fiyat zaten giriş seviyesine ulaştığı an tespit edildiği için,
bekleyen bir limit emri onu kaçırabilir) ve hemen ardından sembolün seçili
stop-loss algoritmasının naif ilk stop'uyla korunur. Yeniden alım emirleri
"rebuy-<algoritma>-<periyot>-<sembol>-<epoch>" client_order_id'siyle
etiketlenir - alpaca_dashboard.py'nin İşlem Geçmişi tablosunda bu emirleri
ayırt edip turuncu yazıyla ve bir "Açıklama" notuyla göstermek için kullanılır
(bkz. _parse_order_tag).
"""

import json
import math
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from alpaca_bars_cache import INTRADAY_BARS_CACHE_PATH, parse_iso
from alpaca_buy_points import compute_available_cash_for_buying, load_local_config
from alpaca_client import AlpacaClient, DEFAULT_DATA_URL, DEFAULT_TRADING_URL
from alpaca_trailing_stop import (
    get_bars_for_timeframe, load_stop_loss_settings, load_telegram_settings, log,
    resolve_stop_algorithm, TIMEFRAME as DEFAULT_TIMEFRAME,
)
from buy_algorithms import ALGORITHMS, DEFAULT_ALGORITHM
from stop_algorithms import resolve_kwargs, STOP_ALGORITHMS
from telegram_notify import send_telegram_message, TelegramError

load_dotenv()

STATE_PATH = "buy_stop_rebuy_state_berkakar.json"

# "1 günlük barlarda uygulanamasın" - bu sözlükte olmayan bir periyot (yani
# "1Day") hiç izlenmez, bkz. _update_tracked.
ELIGIBLE_TIMEFRAME_MINUTES = {"15Min": 15, "30Min": 30, "1Hour": 60}
DEFAULT_WINDOW_HOURS = 2.0


def window_bar_count(timeframe: str, window_hours: float) -> int:
    """window_hours'ı (ör. varsayılan 2 saat) o periyoda göre bir bar sayısına
    çevirir - 1 saatlik bar için 2, 15 dakikalık bar için 8 (2*60/15)."""
    minutes = ELIGIBLE_TIMEFRAME_MINUTES[timeframe]
    return max(1, math.floor(window_hours * 60 / minutes))


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {"tracked": {}, "pending": {}}
    with open(STATE_PATH, encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("tracked", {})
    state.setdefault("pending", {})
    return state


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _update_tracked(
    client: AlpacaClient, state: dict, positions: list[dict], symbol_settings: dict, default_algorithm: str,
) -> None:
    tracked = state["tracked"]
    for pos in positions:
        symbol = pos["symbol"]
        if float(pos["qty"]) <= 0:
            continue  # sadece long pozisyonlar kapsamda - Premium Buy Point zaten yalnızca long alım yapıyor

        settings = symbol_settings.get(symbol) or {}
        timeframe = settings.get("timeframe") or DEFAULT_TIMEFRAME
        if timeframe not in ELIGIBLE_TIMEFRAME_MINUTES:
            tracked.pop(symbol, None)  # "1Day" (ya da tanınmayan bir periyot) - izlenmez
            continue

        stop_order = client.get_open_stop_order(symbol)
        if stop_order is None:
            continue  # henüz resting bir stop yok (bkz. alpaca_trailing_stop.manage_position) - bir sonraki pass

        algorithm = settings.get("algorithm") or default_algorithm
        if algorithm not in ALGORITHMS:
            algorithm = default_algorithm

        tracked[symbol] = {
            "entry_price": float(pos["avg_entry_price"]),
            "stop_order_id": stop_order["id"],
            "timeframe": timeframe,
            "algorithm": algorithm,
        }


def _detect_stop_outs(client: AlpacaClient, state: dict, open_symbols: set[str]) -> None:
    tracked = state["tracked"]
    pending = state["pending"]
    for symbol in [s for s in tracked if s not in open_symbols]:
        info = tracked.pop(symbol)
        try:
            order = client.get_order(info["stop_order_id"])
        except Exception as e:
            log(f"{symbol}: Alım-Stop-Alım - kapanan pozisyonun stop emri sorgulanamadı, atlanıyor: {e}")
            continue
        if order["status"] != "filled":
            # Pozisyon stop DIŞINDA bir sebeple kapanmış (elle satış, extended-hours
            # guard'ın acil limit emri, vb.) - "hisse değeri stop'a ulaşıp satım
            # tetiklenirse" şartı sağlanmadı, kapsam dışı.
            continue
        pending[symbol] = {
            "entry_price": info["entry_price"],
            "timeframe": info["timeframe"],
            "algorithm": info["algorithm"],
            "stopped_out_at": order.get("filled_at") or order["updated_at"],
        }
        log(f"{symbol}: stop tetiklendi (giriş {info['entry_price']:.2f}) - Alım-Stop-Alım izlemeye aldı.")


def _process_pending(
    client: AlpacaClient, state: dict, symbol: str, window_hours: float, budget: float, weights: dict,
    config: dict, stop_settings: dict, available_cash: float, bot_token: str | None, chat_id: str | None,
) -> float:
    """Bekleyen (pending) bir sembolü bir pass ilerletir: pencere içinde fiyat
    giriş seviyesine ulaştıysa yeniden alır, pencere dolduysa vazgeçer,
    aksi halde bekletmeye devam eder. Dönüş: bu çağrıda harcanan tutar ($)."""
    pending = state["pending"]
    info = pending[symbol]
    timeframe = info["timeframe"]
    entry_price = info["entry_price"]
    algorithm = info["algorithm"]
    stopped_out_at = parse_iso(info["stopped_out_at"])

    if client.get_position(symbol) is not None:
        pending.pop(symbol, None)  # pozisyon başka bir yolla (ör. elle) zaten yeniden açılmış - izleme biter
        return 0.0

    n = window_bar_count(timeframe, window_hours)
    bars = get_bars_for_timeframe(
        client, symbol, timeframe, stopped_out_at, exclude_forming=True, cache_file=INTRADAY_BARS_CACHE_PATH,
    )
    window_bars = bars[:n]
    qualifying = next((b for b in window_bars if b.h >= entry_price), None)

    if qualifying is None:
        if len(bars) < n:
            return 0.0  # pencere için yeterli bar henüz oluşmadı, beklemeye devam
        pending.pop(symbol, None)
        log(f"{symbol}: Alım-Stop-Alım penceresi ({window_hours:g} saat / {n} bar) doldu, fiyat giriş "
            f"seviyesine ({entry_price:.2f}) dönmedi - yeniden alım yapılmadı.")
        if bot_token and chat_id:
            try:
                send_telegram_message(
                    bot_token, chat_id,
                    f"ℹ️ {symbol}: Alım-Stop-Alım penceresi doldu, fiyat giriş seviyesine "
                    f"({entry_price:.2f}) dönmedi - yeniden alım yapılmadı.",
                )
            except TelegramError:
                pass
        return 0.0

    weight_pct = float(weights.get(symbol, 0))
    dollar_amount = budget * (weight_pct / 100)
    if dollar_amount <= 0:
        pending.pop(symbol, None)
        return 0.0

    try:
        live_price = client.get_latest_trade_price(symbol)
    except Exception:
        live_price = None
    if live_price is None:
        live_price = bars[-1].c

    qty = math.floor(min(dollar_amount, available_cash) / live_price)
    if qty <= 0:
        log(f"{symbol}: Alım-Stop-Alım tetiklendi (fiyat {entry_price:.2f} seviyesine döndü) ama "
            "nakit/bütçe yetersiz - bu pass'te atlanıyor, pencere içindeyse tekrar denenecek.")
        return 0.0  # pending'de kalır - bir sonraki pass'te (hâlâ pencere içindeyse) tekrar denenir

    stop_algorithm = resolve_stop_algorithm(config, symbol)
    stop_algo = STOP_ALGORITHMS[stop_algorithm]
    stop_settings = stop_settings or {}
    stop_shared_settings = stop_settings.get("shared") or {}
    stop_algo_settings = stop_settings.get(stop_algorithm) or {}

    client_order_id = f"rebuy-{algorithm}-{timeframe}-{symbol}-{int(datetime.now(timezone.utc).timestamp())}"
    try:
        buy_order = client.place_market_entry(symbol, qty, "long", client_order_id=client_order_id)
        filled = client.wait_for_fill(buy_order["id"], timeout=30)
    except Exception as e:
        log(f"{symbol}: Alım-Stop-Alım market emri başarısız/zaman aşımı, bu pass'te atlanıyor: {e}")
        return 0.0  # pending'de kalır, bir sonraki pass'te (hâlâ pencere içindeyse) tekrar denenir

    fill_price = float(filled["filled_avg_price"])
    filled_qty = float(filled["filled_qty"])
    stop_price = round(stop_algo.initial_stop(
        fill_price, "long", bars=bars,
        **resolve_kwargs(stop_algo.initial_stop, stop_algo_settings, stop_shared_settings),
    ), 2)

    stop_msg_suffix = f", stop {stop_price:.2f} seviyesinden kuruldu."
    try:
        client.place_stop_order(symbol, filled_qty, "long", stop_price)
    except Exception as e:
        stop_msg_suffix = f" ama koruma stopu KURULAMADI, pozisyon KORUMASIZ: {e}"
        log(f"{symbol}: Alım-Stop-Alım sonrası stop kurulamadı: {e}")

    pending.pop(symbol, None)
    msg = (
        f"🔁 {symbol}: Alım-Stop-Alım tetiklendi - stop sonrası fiyat giriş seviyesine "
        f"({entry_price:.2f}) döndü, {filled_qty:g} adet @ {fill_price:.2f} fiyatından yeniden alındı "
        f"(order {buy_order['id']}){stop_msg_suffix}"
    )
    log(msg)
    if bot_token and chat_id:
        try:
            send_telegram_message(bot_token, chat_id, msg)
        except TelegramError:
            pass
    return filled_qty * fill_price


def run_once(client: AlpacaClient) -> None:
    config = load_local_config()
    state = load_state()

    if not config.get("buy_stop_rebuy_enabled"):
        if state["tracked"] or state["pending"]:
            log("Alım-Stop-Alım devre dışı - izlenen/bekleyen kayıtlar temizleniyor.")
            save_state({"tracked": {}, "pending": {}})
        return

    window_hours = float(config.get("buy_stop_rebuy_window_hours") or DEFAULT_WINDOW_HOURS)
    symbol_settings = config.get("symbol_settings") or {}
    default_algorithm = config.get("algorithm") or DEFAULT_ALGORITHM
    if default_algorithm not in ALGORITHMS:
        default_algorithm = DEFAULT_ALGORITHM

    positions = [p for p in client.get_all_positions() if p.get("asset_class") == "us_equity"]
    open_symbols = {p["symbol"] for p in positions}

    _update_tracked(client, state, positions, symbol_settings, default_algorithm)
    _detect_stop_outs(client, state, open_symbols)

    if not state["pending"]:
        save_state(state)
        return

    budget = float(config.get("budget") or 0)
    weights = config.get("weights") or {}
    stop_settings = load_stop_loss_settings()

    try:
        available_cash = compute_available_cash_for_buying(client)
    except Exception as e:
        log(f"Alım-Stop-Alım: hesap nakti alınamadı, bu pass atlanıyor: {e}")
        save_state(state)
        return

    bot_token, chat_id = load_telegram_settings()
    for symbol in list(state["pending"].keys()):
        try:
            spent = _process_pending(
                client, state, symbol, window_hours, budget, weights, config, stop_settings,
                available_cash, bot_token, chat_id,
            )
            available_cash -= spent
        except Exception as e:
            log(f"{symbol}: Alım-Stop-Alım işlenirken hata, bu pass atlanıyor: {e}")

    save_state(state)


def build_client() -> AlpacaClient:
    key_id = os.environ["APCA_API_KEY_ID"]
    secret_key = os.environ["APCA_API_SECRET_KEY"]
    trading_url = os.environ.get("APCA_API_BASE_URL", DEFAULT_TRADING_URL)
    data_url = os.environ.get("APCA_API_DATA_URL", DEFAULT_DATA_URL)
    return AlpacaClient(key_id, secret_key, trading_url, data_url)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit (used by GitHub Actions).")
    parser.parse_args()

    run_once(build_client())
