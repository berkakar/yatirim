"""Relative Strength Rotasyonu modülünün haftada 1 kez çalışan otomatik adımı.

`relative_strength.py` (Streamlit sayfası) kullanıcıya SADECE ayarları
(evren, top_n, geri bakış penceresi, nakit payı, stop algoritması) düzenleme
ve salt-okunur bir önizleme imkânı verir - GERÇEK emirleri asla o sayfa
vermez (bkz. relative_strength_core.py'nin modül üstü notu #1, bu sistemdeki
her Streamlit sayfasıyla aynı ilke). Kullanıcı sayfadaki "otomatik çalıştır"
kutucuğunu işaretlerse, bu script GitHub Actions üzerinden haftada 1 kez
gerçek rebalance'ı (relative_strength_core.rebalance) çalıştırır.

Diğer tek-kullanıcı script'leri (alpaca_buy_points.py, otomatik_alim_satim_runner.py)
gibi Streamlit'e bağımlı değildir ve kullanıcı adı sabittir - bkz. o
dosyalardaki aynı gerekçe."""

import argparse
import json
import os

from alpaca_client import AlpacaClient, DEFAULT_TRADING_URL, DEFAULT_DATA_URL
from alpaca_trailing_stop import log
from relative_strength_core import config_path, rebalance

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


def run_once() -> None:
    config = load_local_config()
    if not config.get("enabled"):
        log("Relative Strength Rotasyonu devre dışı, atlanıyor.")
        return

    client = build_client()
    clock = client.get_clock()
    if not clock["is_open"]:
        log("Piyasa kapalı, rebalance atlanıyor.")
        return

    stop_settings = load_local_stop_settings()
    try:
        summary = rebalance(client, USERNAME, config, stop_settings)
    except Exception as e:
        log(f"Relative Strength Rotasyonu pipeline hatası, bu koşu atlanıyor: {e}")
        return

    config["last_run_at"] = summary["run_at"]
    config["last_run_summary"] = summary
    save_local_config(config)

    if summary.get("skipped"):
        log(f"Relative Strength Rotasyonu atlandı: {summary.get('reason')}.")
        return

    log(
        f"Relative Strength Rotasyonu tamamlandı: evren={summary['universe_size']}, "
        f"hedef={summary['target_symbols']}, tutulan={summary['held']}, "
        f"satılan={summary['sold']}, alınan={summary['bought']}."
    )
    if summary.get("sell_errors"):
        log(f"Satış hataları: {summary['sell_errors']}")
    if summary.get("buy_errors"):
        log(f"Alım hataları: {summary['buy_errors']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit (used by GitHub Actions).")
    args = parser.parse_args()

    if args.once:
        run_once()
