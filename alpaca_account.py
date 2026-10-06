"""Alpaca hesap türü (Sanal Para / Gerçek Para) - kullanıcı bazlı ayar.

Ayar `alpaca_account_mode_<kullanıcı>.json` kaydında tutulur (SQLite açıksa
veritabanında, değilse GitHub'da / repo dosyasında - stop_loss_settings ile
aynı yol). Kayıt yoksa ya da okunamazsa her zaman Sanal Para (paper) kabul
edilir: gerçek paraya ancak arayüzde açıkça onaylanarak geçilir.

Hem arayüz hem sunucudaki zamanlanmış işler (runner'lar) emir göndereceği
adresi ve anahtarları buradan alır, böylece arayüzün gösterdiği hesap ile
botların işlem yaptığı hesap hep aynıdır:

- Sanal Para: https://paper-api.alpaca.markets/v2
- Gerçek Para: https://api.alpaca.markets/v2

Anahtarlar kullanıcının "👤 Hesabım" sayfasında girdiği, veritabanında şifreli
duran anahtarlardır (bkz. alpaca_keys.py) - arayüz de işler de bunları kullanır.
İşler, veritabanında kayıt yoksa (ör. GitHub Actions) ortam değişkenlerine düşer:
APCA_API_KEY_ID / APCA_API_SECRET_KEY ve APCA_LIVE_API_KEY_ID / APCA_LIVE_API_SECRET_KEY.

Gerçek Para seçiliyken gerçek hesap anahtarları tanımlı değilse anahtar
döndürülmez / iş hata verir - asla sessizce paper hesaba düşülmez.
"""
import os
from datetime import datetime, timezone

import alpaca_keys
import storage
from alpaca_client import DEFAULT_DATA_URL, AlpacaClient

PAPER = "paper"
LIVE = "live"
TRADING_URLS = {
    PAPER: "https://paper-api.alpaca.markets/v2",
    LIVE: "https://api.alpaca.markets/v2",
}
MODE_LABELS = {PAPER: "Sanal Para", LIVE: "Gerçek Para"}
SETTING_NAME = "alpaca_account_mode"
CONFIRM_TEXT = "GERÇEK PARA"
# Sunucudaki zamanlanmış işlerin çalıştığı kullanıcı (bkz. *_runner.py USERNAME).
JOB_USERNAME = "berkakar"


def setting_path(username: str) -> str:
    return f"{SETTING_NAME}_{username}.json"


def mode_from_record(record) -> str:
    return LIVE if isinstance(record, dict) and record.get("mode") == LIVE else PAPER


def load_account_mode(username: str, github_token: str | None = None) -> str:
    """Kullanıcının seçili hesap türü (PAPER / LIVE). github_token yalnızca
    arayüzden, SQLite kapalıyken verilir (ayar GitHub'daki dosyadan okunur);
    işler repo dosyasını / veritabanını okur."""
    try:
        if not storage.enabled() and github_token:
            from config import GITHUB_REPO
            from github_config import read_json_from_github
            record = read_json_from_github(GITHUB_REPO, github_token, setting_path(username), {})
        else:
            record = storage.load_json(setting_path(username), {})
    except Exception:
        return PAPER
    return mode_from_record(record)


def save_account_mode(username: str, mode: str, github_token: str | None = None) -> dict:
    if mode not in TRADING_URLS:
        raise ValueError(f"Geçersiz hesap türü: {mode}")
    record = {
        "mode": mode,
        "changed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "changed_by": username,
    }
    if not storage.enabled() and github_token:
        from config import GITHUB_REPO
        from github_config import write_json_to_github
        write_json_to_github(
            GITHUB_REPO, github_token, setting_path(username), record,
            f"Alpaca hesap türü: {MODE_LABELS[mode]} ({username})",
        )
    else:
        storage.save_json(setting_path(username), record)
    return record


def trading_url(mode: str) -> str:
    return TRADING_URLS[mode]


def user_keys(username: str, mode: str) -> tuple[str | None, str | None]:
    """Kullanıcının veritabanındaki (Hesabım sayfasında girilen) anahtarları.
    Gerçek Para için paper anahtarlarına geri düşülmez."""
    return alpaca_keys.get_keys(username, mode)


def has_live_keys(username: str) -> bool:
    return alpaca_keys.has_keys(username, LIVE)


def build_job_client(username: str) -> AlpacaClient:
    """Sunucudaki zamanlanmış işlerin Alpaca istemcisi - adres ve anahtarlar
    kullanıcının hesap türü ayarından. Anahtarlar önce veritabanından (Hesabım
    sayfası), yoksa ortam değişkenlerinden alınır. Gerçek Para seçili ama gerçek
    hesap anahtarı hiçbir yerde yoksa hata verir (iş durur, run_job.sh Telegram'a
    bildirir) - paper hesapla devam etmez."""
    mode = load_account_mode(username)
    key_id, secret_key = user_keys(username, mode)
    if not (key_id and secret_key):
        if mode == LIVE:
            key_id = os.environ.get("APCA_LIVE_API_KEY_ID")
            secret_key = os.environ.get("APCA_LIVE_API_SECRET_KEY")
            if not key_id or not secret_key:
                raise RuntimeError(
                    f"{username} için Alpaca hesap türü 'Gerçek Para' ama gerçek hesap anahtarı ne "
                    "Hesabım sayfasında ne de APCA_LIVE_API_KEY_ID / APCA_LIVE_API_SECRET_KEY'de tanımlı - "
                    "iş çalıştırılmadı."
                )
        else:
            key_id = os.environ["APCA_API_KEY_ID"]
            secret_key = os.environ["APCA_API_SECRET_KEY"]
    data_url = os.environ.get("APCA_API_DATA_URL", DEFAULT_DATA_URL)
    return AlpacaClient(key_id, secret_key, trading_url(mode), data_url)
