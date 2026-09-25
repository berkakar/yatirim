"""Heikin Ashi Gün İçi modülünün yarım saatte bir çalışan otomatik adımı.

`heikin_ashi_intraday.py` (Streamlit sayfası) sadece ayarları kaydeder ve
salt-okunur önizleme gösterir - GERÇEK emirleri bu script verir (bkz.
heikin_ashi_intraday_core.py'nin modül üstü notu). orb_scan_runner.py ile
aynı yapı: Streamlit'e bağımlı değil, kullanıcı adı sabit.

Cron seans boyunca her 30 dakikalık bar kapanışından ~2 dakika sonra ve
gün sonu kapatma için ayrıca kapanıştan ~15 dakika önce tetiklenir (bkz.
.github/workflows/heikin_ashi_intraday.yml). Piyasa kapalıyken hiçbir şey
yapılmaz. Gün sonu kapatma, modül devre dışı olsa bile elde kalan
pozisyonları kapatır."""

import argparse
import json
import os

from alpaca_client import AlpacaClient, DEFAULT_TRADING_URL, DEFAULT_DATA_URL
from alpaca_trailing_stop import log
from heikin_ashi_intraday_core import config_path, holdings_path, load_holdings_local, run_pass

USERNAME = "berkakar"
CONFIG_PATH = config_path(USERNAME)
STOP_LOSS_SETTINGS_PATH = f"stop_loss_settings_{USERNAME}.json"
MODULE_NAME = "Heikin Ashi Gün İçi"


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


def run_once() -> None:
    config = load_local_config()
    # Devre dışıyken bile, elde pozisyon kaldıysa gün sonu kapatma çalışmalı.
    if not config.get("enabled") and not (os.path.exists(holdings_path(USERNAME)) and load_holdings_local(USERNAME)):
        log(f"{MODULE_NAME} devre dışı ve elde pozisyon yok, atlanıyor.")
        return

    client = build_client()
    if not client.get_clock()["is_open"]:
        log("Piyasa kapalı, atlanıyor.")
        return

    try:
        summary = run_pass(client, USERNAME, config, load_local_stop_settings())
    except Exception as e:
        log(f"{MODULE_NAME} pipeline hatası, bu koşu atlanıyor: {e}")
        return

    config["last_run_at"] = summary["run_at"]
    config["last_run_summary"] = summary
    save_local_config(config)

    if summary.get("skipped"):
        log(f"{MODULE_NAME} atlandı: {summary.get('reason')}.")
        return
    log(
        f"{MODULE_NAME} tamamlandı ({summary.get('phase')}): evren={summary.get('universe_size', '—')}, "
        f"aday={summary.get('candidate_count', '—')}, satılan={summary['sold']}, alınan={summary['bought']}."
    )
    if summary.get("errors"):
        log(f"Hatalar: {summary['errors']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit (used by GitHub Actions).")
    args = parser.parse_args()

    if args.once:
        run_once()
