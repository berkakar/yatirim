"""Fonlarım modülünde takip edilen fonların en büyük 6 hissesinden biri
günlük bazda kullanıcının belirlediği eşiğin altına düşerse Telegram
bildirimi gönderir.

GitHub Actions tarafından BIST işlem saatlerinde periyodik çalıştırılır
(bkz. .github/workflows/fon_hisse_uyari.yml). Her kullanıcının takip
listesi (takip_fonlari_<kullanıcı>.json), bildirim ayarları
(bildirim_ayarlari_<kullanıcı>.json) ve fonların KAP'tan çekilmiş en
büyük 6 hissesi (kap_portfoy_cache.json) doğrudan repo checkout'undan
okunur - Streamlit tarafı bunları zaten GitHub'a commit'liyor (bkz.
turk_fonlari_takip_data.py, bildirim_data.py).

Aynı gün içinde aynı kullanıcı+hisse için tekrar tekrar bildirim
gönderilmemesi için günlük bir "gönderildi" durumu (bildirim_durumu.json)
tutulur.

Run with --once (GitHub Actions workflow'u tarafından kullanılır).
"""
import argparse
import glob
import json
import os
import re
from datetime import date, datetime

import yfinance as yf

from telegram_notify import TelegramError, send_telegram_message

STATE_FILE = "bildirim_durumu.json"
DEFAULT_LOSS_THRESHOLD_PCT = -3.0

_USER_RE = re.compile(r"^takip_fonlari_(.+)\.json$")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _discover_users() -> list[str]:
    users = []
    for path in glob.glob("takip_fonlari_*.json"):
        m = _USER_RE.match(os.path.basename(path))
        if m:
            users.append(m.group(1))
    return users


def _latest_holdings(portfolio_cache: dict, fund_code: str) -> list[tuple[str, float]]:
    reports = (portfolio_cache.get(fund_code) or {}).get("reports") or []
    if not reports:
        return []
    latest = max(reports, key=lambda r: r["report_date_sort"])
    return [(h[0], h[1]) for h in latest.get("holdings", [])]


def _daily_change_pct(ticker: str) -> float | None:
    """BIST hissesinin günlük (bir önceki kapanışa göre) değişim yüzdesini
    döner - piyasa açıksa şu anki fiyatı, kapalıysa son kapanışı kullanır."""
    for symbol in (f"{ticker}.IS", ticker):
        try:
            hist = yf.Ticker(symbol).history(period="5d", interval="1d")
        except Exception:
            continue
        if hist is None or hist.empty or len(hist) < 2:
            continue
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            continue
        prev_close, last_close = float(closes.iloc[-2]), float(closes.iloc[-1])
        if prev_close == 0:
            continue
        return round((last_close - prev_close) / prev_close * 100, 2)
    return None


def _todays_state() -> dict:
    state = _load_json(STATE_FILE, {})
    today = date.today().isoformat()
    if state.get("date") != today:
        return {"date": today, "notified": {}}
    state.setdefault("notified", {})
    return state


def run_once() -> None:
    users = _discover_users()
    if not users:
        log("Takip edilen fon bulunan kullanıcı yok, çıkılıyor.")
        return

    portfolio_cache = _load_json("kap_portfoy_cache.json", {})
    state = _todays_state()

    # username -> {ticker: [(fund_code, weight_pct), ...]}
    user_ticker_funds: dict[str, dict[str, list[tuple[str, float]]]] = {}
    user_settings: dict[str, dict] = {}

    for username in users:
        settings = _load_json(f"bildirim_ayarlari_{username}.json", {})
        chat_id = (settings.get("telegram_chat_id") or "").strip()
        if not chat_id:
            continue
        user_settings[username] = settings

        tracked = _load_json(f"takip_fonlari_{username}.json", [])
        ticker_funds: dict[str, list[tuple[str, float]]] = {}
        for fund in tracked:
            for ticker, weight in _latest_holdings(portfolio_cache, fund["code"]):
                ticker_funds.setdefault(ticker, []).append((fund["code"], weight))
        if ticker_funds:
            user_ticker_funds[username] = ticker_funds

    if not user_ticker_funds:
        log("Bildirimi açık ve takip listesinde hisse bulunan kullanıcı yok, çıkılıyor.")
        return

    all_tickers = {t for funds in user_ticker_funds.values() for t in funds}
    log(f"{len(all_tickers)} benzersiz hisse için günlük değişim kontrol ediliyor...")
    changes = {}
    for ticker in sorted(all_tickers):
        changes[ticker] = _daily_change_pct(ticker)

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        log("TELEGRAM_BOT_TOKEN tanımlı değil, bildirim gönderilemeyecek.")

    for username, ticker_funds in user_ticker_funds.items():
        settings = user_settings[username]
        threshold = settings.get("loss_threshold_pct", DEFAULT_LOSS_THRESHOLD_PCT)
        already_notified = set(state["notified"].get(username, []))

        breaches = []
        for ticker, funds in ticker_funds.items():
            change = changes.get(ticker)
            if change is None or change > threshold or ticker in already_notified:
                continue
            breaches.append((ticker, change, funds))

        if not breaches:
            continue

        lines = [f"⚠️ Fonlarım Uyarısı - Günlük Kayıp Eşiği (%{threshold}) Aşıldı\n"]
        for ticker, change, funds in sorted(breaches, key=lambda b: b[1]):
            fund_desc = ", ".join(f"{code} (%{weight})" for code, weight in funds)
            lines.append(f"• {ticker}: %{change} - {fund_desc}")
        message = "\n".join(lines)

        if not bot_token:
            log(f"{username} için {len(breaches)} eşik aşımı var ama bot token yok, atlanıyor.")
            continue

        try:
            send_telegram_message(bot_token, settings["telegram_chat_id"], message)
        except TelegramError as e:
            log(f"{username} için Telegram bildirimi gönderilemedi: {e}")
            continue

        log(f"{username} için {len(breaches)} hisse bildirimi gönderildi: {[b[0] for b in breaches]}")
        state["notified"].setdefault(username, [])
        state["notified"][username].extend(ticker for ticker, _, _ in breaches)

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Tek seferlik çalıştırma (GitHub Actions).")
    args = parser.parse_args()

    run_once()
