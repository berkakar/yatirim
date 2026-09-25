"""Açılış Aralığı Kırılımı (ORB) modülünün her gün piyasa açılışında çalışan
otomatik adımı.

`orb_scan.py` (Streamlit sayfası) kullanıcıya SADECE ayarları (evren, mum
periyodu, top_n, hacim/kırılım eşikleri, nakit payı, stop algoritması)
düzenleme ve salt-okunur bir önizleme imkânı verir - GERÇEK emirleri asla o
sayfa vermez (bkz. orb_core.py'nin modül üstü notu #1, bu sistemdeki her
Streamlit sayfasıyla aynı ilke). Kullanıcı sayfadaki "otomatik çalıştır"
kutucuğunu işaretlerse, bu script GitHub Actions üzerinden her gün piyasa
açılışından bir süre sonra gerçek taramayı+alımı (orb_core.scan_and_buy)
çalıştırır.

Diğer tek-kullanıcı script'leri (alpaca_buy_points.py, relative_strength_runner.py)
gibi Streamlit'e bağımlı değildir ve kullanıcı adı sabittir - bkz. o
dosyalardaki aynı gerekçe.

Cron, ABD piyasa açılışını (DST'ye göre 13:30/14:30 UTC) hem yaz hem kış
saatinde makul bir gecikmeyle yakalayabilmek için ~1 saatlik bir pencerede
her 15 dakikada bir tetiklenir (bkz. .github/workflows/orb_scan.yml) - bu
script kendi içinde "piyasa açık mı" ve "bugün zaten çalıştı mı" kontrolleriyle
aynı günde birden fazla gerçek tarama/alım yapılmasını engeller."""

import argparse
import json
import os
from datetime import datetime, timezone

from alpaca_client import AlpacaClient, DEFAULT_TRADING_URL, DEFAULT_DATA_URL
from alpaca_trailing_stop import log
from orb_core import config_path, holdings_path, load_holdings_local, save_holdings_local, scan_and_buy

USERNAME = "berkakar"
CONFIG_PATH = config_path(USERNAME)
STOP_LOSS_SETTINGS_PATH = f"stop_loss_settings_{USERNAME}.json"


def load_local_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {"enabled": False}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_local_config(config: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def load_local_stop_settings() -> dict:
    if not os.path.exists(STOP_LOSS_SETTINGS_PATH):
        return {}
    with open(STOP_LOSS_SETTINGS_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_client() -> AlpacaClient:
    key_id = os.environ["APCA_API_KEY_ID"]
    secret_key = os.environ["APCA_API_SECRET_KEY"]
    trading_url = os.environ.get("APCA_API_BASE_URL", DEFAULT_TRADING_URL)
    data_url = os.environ.get("APCA_API_DATA_URL", DEFAULT_DATA_URL)
    return AlpacaClient(key_id, secret_key, trading_url, data_url)


def _already_ran_today(config: dict) -> bool:
    """Cron ~1 saatlik pencerede 15 dakikada bir tetiklendiğinden (bkz. modül
    üstü not), aynı gün içinde birden fazla gerçek tarama/alım geçişini
    engeller - last_run_at'ın tarihi bugünle (UTC) aynıysa True döner."""
    last_run_at = config.get("last_run_at")
    if not last_run_at:
        return False
    try:
        last_run_date = datetime.fromisoformat(last_run_at).date()
    except ValueError:
        return False
    return last_run_date == datetime.now(timezone.utc).date()


def run_once() -> None:
    config = load_local_config()
    if not config.get("enabled"):
        log("Açılış Aralığı Kırılımı (ORB) devre dışı, atlanıyor.")
        return
    if _already_ran_today(config):
        log("Açılış Aralığı Kırılımı (ORB) bugün zaten çalıştı, atlanıyor.")
        return

    client = build_client()
    clock = client.get_clock()
    if not clock["is_open"]:
        log("Piyasa kapalı, tarama atlanıyor.")
        return

    stop_settings = load_local_stop_settings()
    try:
        summary = scan_and_buy(client, USERNAME, config, stop_settings)
    except Exception as e:
        log(f"Açılış Aralığı Kırılımı (ORB) pipeline hatası, bu koşu atlanıyor: {e}")
        return

    config["last_run_at"] = summary["run_at"]
    config["last_run_summary"] = summary
    save_local_config(config)

    if summary.get("skipped"):
        log(f"Açılış Aralığı Kırılımı (ORB) atlandı: {summary.get('reason')}.")
        return

    log(
        f"Açılış Aralığı Kırılımı (ORB) tamamlandı: evren={summary['universe_size']}, "
        f"aday={summary['candidate_count']}, seçilen={summary['selected_symbols']}, "
        f"alınan={summary['bought']}."
    )
    if summary.get("buy_errors"):
        log(f"Alım hataları: {summary['buy_errors']}")


def reconcile_symbols(symbols: list[str]) -> None:
    """scan_and_buy() bir sembolü GERÇEKTEN Alpaca'da satın aldı ama (ör.
    eşzamanlı bir git push çakışması yüzünden - bkz. bu dosyanın git
    geçmişi) orb_scan_holdings_berkakar.json hiç commit edilemediyse
    kullanılır. HİÇBİR alım/satım YAPMAZ - sadece Alpaca'daki GERÇEK
    pozisyonu okuyup yerel holdings state'ine ekler ki bir sonraki tarama bu
    sembolü tekrar almaya çalışmasın (bkz. orb_core.py'nin modül üstü
    notu #4 - zaten pozisyonu olan bir sembol aday listesinden hariç
    tutulur)."""
    client = build_client()
    holdings = load_holdings_local(USERNAME)
    for symbol in symbols:
        position = client.get_position(symbol)
        if position is None:
            log(f"{symbol}: Alpaca'da canlı pozisyon bulunamadı, atlanıyor.")
            continue
        holdings[symbol] = {
            "qty": float(position["qty"]),
            "entry_price": float(position["avg_entry_price"]),
            "entered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "score": None,
        }
        log(f"{symbol}: yerel holdings state'ine eklendi (qty={position['qty']}, entry={position['avg_entry_price']}).")
    save_holdings_local(USERNAME, holdings)
    log(f"{holdings_path(USERNAME)} güncellendi.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit (used by GitHub Actions).")
    parser.add_argument(
        "--reconcile-symbols", default="",
        help="Virgülle ayrılmış sembol listesi - hiçbir alım/satım yapmadan, Alpaca'daki gerçek "
             "pozisyonları okuyup yerel holdings state'ine ekler (bkz. reconcile_symbols()).",
    )
    args = parser.parse_args()

    if args.reconcile_symbols:
        reconcile_symbols([s.strip().upper() for s in args.reconcile_symbols.split(",") if s.strip()])
    elif args.once:
        run_once()
