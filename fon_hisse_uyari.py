"""Fonlarım modülünde takip edilen fonların en büyük 10 hissesinden biri
günlük bazda kullanıcının belirlediği eşiğin altına düşerse Telegram
bildirimi gönderir.

GitHub Actions tarafından BIST işlem saatlerinde periyodik çalıştırılır
(bkz. .github/workflows/fon_hisse_uyari.yml) - günde 2 kez tetiklenip her
seferinde ~4 saat boyunca bu scripti 5 dakikada bir --once ile çağıran bir
döngü şeklinde, çünkü GitHub'ın kendi */5 schedule event'i pratikte
güvenilir çalışmıyor (saatler süren boşluklar bırakabiliyor). Her kullanıcının takip
listesi (takip_fonlari_<kullanıcı>.json), bildirim ayarları
(bildirim_ayarlari_<kullanıcı>.json) ve fonların KAP'tan çekilmiş en
büyük 10 hissesi (kap_portfoy_cache.json) doğrudan repo checkout'undan
okunur - Streamlit tarafı bunları zaten GitHub'a commit'liyor (bkz.
turk_fonlari_takip_data.py, bildirim_data.py).

Aynı durumun tekrar tekrar bildirilmemesi için (ör. 5 dakikada bir aynı
düşüşler için spam atılmasın diye) her kullanıcının o an eşiği aşan TÜM
hisselerinin anlık görüntüsü (bildirim_durumu.json) tutulur - bu görüntü
bir önceki gönderilen bildirimle birebir AYNI ise (hiçbir hisse eklenmedi/
çıkmadı, hiçbir yüzde değişmedi) yeni bildirim gönderilmez; en ufak bir
fark olursa (yeni bir hisse eşiği aştı, biri toparlandı, ya da mevcut
birinin yüzdesi değişti) o anki TÜM eşiği aşan hisseleri içeren tam bir
bildirim gönderilir - böylece kullanıcı her bildirimde fonun o anki
tüm düşüş tablosunu görür, sadece son değişen tek hisseyi değil.

Run with --once (GitHub Actions workflow'u tarafından kullanılır).
"""
import argparse
import glob
import json
import os
import re
from datetime import datetime

import yfinance as yf

from tefas_client import fetch_fund_daily_change_pct
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
    döner - piyasa açıksa şu anki fiyatı, kapalıysa son kapanışı kullanır.

    KAP'taki "en büyük yatırım aracı" listesinde bazen bir BIST hissesi
    değil, aynı portföy yönetim şirketinin başka bir TEFAS fonu (fon
    içinde fon pozisyonu) çıkabilir - bu ticker'lar Yahoo Finance'te
    bulunamaz, bu yüzden yfinance başarısız olursa TEFAS fon fiyatı
    üzerinden aynı hesap yedek olarak denenir."""
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

    try:
        return fetch_fund_daily_change_pct(ticker)
    except Exception:
        return None


def _load_state() -> dict:
    """{"kullanıcı": {"HİSSE": son_gönderilen_yüzde, ...}, ...}"""
    return _load_json(STATE_FILE, {})


def run_once() -> None:
    users = _discover_users()
    if not users:
        log("Takip edilen fon bulunan kullanıcı yok, çıkılıyor.")
        return

    portfolio_cache = _load_json("kap_portfoy_cache.json", {})
    state = _load_state()

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

    new_state: dict[str, dict[str, float]] = {}

    for username, ticker_funds in user_ticker_funds.items():
        settings = user_settings[username]
        threshold = settings.get("loss_threshold_pct", DEFAULT_LOSS_THRESHOLD_PCT)
        prev_notified = state.get(username, {})

        # Şu an eşiği aşan TÜM hisseler (yüzdesiyle) - hisse toparlanıp
        # eşiğin üzerine çıktığında burada yer almaz, böylece durumdan da
        # düşer (bir dahaki düşüşte "yeni" olay sayılır). Bildirim, bu
        # bütün listeyi (sadece değişen hisseyi değil) içerir - "değişiklik"
        # bütün olarak önceki bildirimle aynı olup olmamasına bakılarak
        # belirlenir, yoksa kullanıcı aynı anda düşen başka hisseleri
        # göremiyordu.
        currently_breaching: dict[str, float] = {}
        for ticker, funds in ticker_funds.items():
            change = changes.get(ticker)
            if change is None or change > threshold:
                continue
            currently_breaching[ticker] = change

        if currently_breaching == prev_notified:
            new_state[username] = currently_breaching
            continue

        lines = [f"⚠️ Fonlarım Uyarısı - Günlük Kayıp Eşiği (%{threshold}) Aşıldı\n"]
        for ticker, change in sorted(currently_breaching.items(), key=lambda c: c[1]):
            fund_desc = ", ".join(f"{code} (%{weight})" for code, weight in ticker_funds[ticker])
            lines.append(f"• {ticker}: %{change} - {fund_desc}")
        message = "\n".join(lines)

        sent_ok = False
        if not bot_token:
            log(f"{username} için {len(currently_breaching)} hisse eşik altında ama bot token yok, atlanıyor.")
        else:
            try:
                send_telegram_message(bot_token, settings["telegram_chat_id"], message)
                sent_ok = True
            except TelegramError as e:
                log(f"{username} için Telegram bildirimi gönderilemedi: {e}")

        if sent_ok:
            new_state[username] = currently_breaching
            log(f"{username} için {len(currently_breaching)} hisse bildirimi gönderildi: {list(currently_breaching)}")
        else:
            # Gönderim başarısız oldu - durumu ilerletme ki bir sonraki
            # çalıştırmada tekrar "değişmiş" sayılıp yeniden denensin.
            new_state[username] = prev_notified

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(new_state, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Tek seferlik çalıştırma (GitHub Actions).")
    args = parser.parse_args()

    run_once()
