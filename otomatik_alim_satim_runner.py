"""Otomatik Alım/Satım modülünün günde 1 kez çalışan otomatik adımı.

`otomatik_alim_satim.py` (Streamlit sayfası) kullanıcıya adım adım buton ile
aynı pipeline'ı (tara → RSI/EMA ile daralt → backtest → Premium Buy Point
Portföyüne aktar) manuel çalıştırma imkânı verir. Kullanıcı sayfanın en
altındaki "otomatik çalıştır" kutucuğunu işaretlerse, bu script GitHub
Actions üzerinden günde 1 kez aynı pipeline'ı (`otomatik_alim_satim_core.
run_pipeline`) kendisi çalıştırır - böylece manuel buton tıklamaya gerek
kalmadan `premium-buy-portfolio-<user>` watchlist'i ve `portfolio_config_
<user>.json` güncellenir; gerçek alım/satım emirlerini zaten her 5 dakikada
bir çalışan alpaca_buy_points.py / alpaca_trailing_stop.py yürütür.

Diğer tek-kullanıcı script'leri (alpaca_buy_points.py) gibi Streamlit'e
bağımlı değildir ve kullanıcı adı sabittir - bkz. o dosyadaki WATCHLIST_NAME/
CONFIG_PATH ile aynı gerekçe."""

import argparse
import json
import os

from alpaca_client import AlpacaClient, DEFAULT_TRADING_URL, DEFAULT_DATA_URL
from alpaca_trailing_stop import log
from otomatik_alim_satim_core import run_pipeline

USERNAME = "berkakar"
CONFIG_PATH = f"otomatik_alim_satim_config_{USERNAME}.json"
BACKTEST_RESULTS_PATH = f"backtest_results_{USERNAME}.json"


def load_local_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {"enabled": False}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_local_config(config: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def append_backtest_results(new_runs: list[dict]) -> None:
    if not new_runs:
        return
    results = []
    if os.path.exists(BACKTEST_RESULTS_PATH):
        with open(BACKTEST_RESULTS_PATH, encoding="utf-8") as f:
            results = json.load(f)
    results.extend(new_runs)
    with open(BACKTEST_RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def build_client() -> AlpacaClient:
    key_id = os.environ["APCA_API_KEY_ID"]
    secret_key = os.environ["APCA_API_SECRET_KEY"]
    trading_url = os.environ.get("APCA_API_BASE_URL", DEFAULT_TRADING_URL)
    data_url = os.environ.get("APCA_API_DATA_URL", DEFAULT_DATA_URL)
    return AlpacaClient(key_id, secret_key, trading_url, data_url)


def run_once() -> None:
    config = load_local_config()
    if not config.get("enabled"):
        log("Otomatik Alım/Satım devre dışı, atlanıyor.")
        return

    client = build_client()
    try:
        summary = run_pipeline(USERNAME, client, config)
    except Exception as e:
        log(f"Otomatik Alım/Satım pipeline hatası, bu koşu atlanıyor: {e}")
        return

    backtest_results = summary.pop("backtest_results", [])
    append_backtest_results(backtest_results)

    config["last_run_at"] = summary["run_at"]
    config["last_run_summary"] = summary
    save_local_config(config)

    log(
        f"Otomatik Alım/Satım tamamlandı: evren={summary['universe_size']}, "
        f"likidite sonrası={summary['liquid_universe_size']}, "
        f"sinyal={summary['scan_signal_count']}, aday={summary['candidate_count']}, "
        f"seçilen={summary['selected_symbols']}."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit (used by GitHub Actions).")
    args = parser.parse_args()

    if args.once:
        run_once()
