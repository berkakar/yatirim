"""Giriş Sayfası'ndaki "Bağlantılar" başlığı için, uygulamanın veri çektiği
5 dış kaynağa (Yahoo Finance, Alpaca Markets, TEFAS, KAP, Telegram) o an
erişilip erişilemediğini kontrol eder.

Her kaynağın kendi veri API'sini (ör. TEFAS'ın fonGnlBlgSiraliGetir'i,
6 istek/dk limitli) tüketmemek için, mümkün olan yerde sadece sitenin kök
adresine hafif bir istek atılır - amaç veriyi doğrulamak değil, sadece
erişilebilirliği (DNS/TLS/HTTP) ölçmek. Bütün kontroller birbirini
beklemeden paralel çalışır (ThreadPoolExecutor) ve sonuç kısa bir süre
(ttl) önbelleğe alınır, böylece sayfa her yeniden çizildiğinde (Streamlit'in
her etkileşimde tüm scripti tekrar çalıştırma davranışı) dış servislere
gereksiz istek gitmez.
"""
import concurrent.futures

import requests
import streamlit as st

from alpaca_client import AlpacaClient

_TIMEOUT = 5
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _check_yahoo_finance() -> tuple[bool, str]:
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        return r.ok, ("Bağlantı OK" if r.ok else f"HTTP {r.status_code}")
    except requests.RequestException as e:
        return False, str(e)


def _check_alpaca(key_id: str | None, secret_key: str | None) -> tuple[bool, str]:
    if not key_id or not secret_key:
        return False, "Bu kullanıcı için Alpaca anahtarı tanımlı değil"
    try:
        AlpacaClient(key_id, secret_key).get_account()
        return True, "Bağlantı OK"
    except Exception as e:
        return False, str(e)


def _check_tefas() -> tuple[bool, str]:
    try:
        r = requests.get("https://www.tefas.gov.tr", headers=_HEADERS, timeout=_TIMEOUT)
        return r.ok, ("Bağlantı OK" if r.ok else f"HTTP {r.status_code}")
    except requests.RequestException as e:
        return False, str(e)


def _check_kap() -> tuple[bool, str]:
    try:
        r = requests.get("https://www.kap.org.tr", headers=_HEADERS, timeout=_TIMEOUT)
        return r.ok, ("Bağlantı OK" if r.ok else f"HTTP {r.status_code}")
    except requests.RequestException as e:
        return False, str(e)


def _check_telegram(bot_token: str | None) -> tuple[bool, str]:
    if not bot_token:
        return False, "TELEGRAM_BOT_TOKEN tanımlı değil"
    try:
        r = requests.get(f"https://api.telegram.org/bot{bot_token}/getMe", timeout=_TIMEOUT)
        if not r.ok:
            return False, f"HTTP {r.status_code}"
        data = r.json()
        return bool(data.get("ok")), ("Bağlantı OK" if data.get("ok") else str(data.get("description")))
    except requests.RequestException as e:
        return False, str(e)


@st.cache_data(ttl=60, show_spinner=False)
def check_all_connections(
    key_id: str | None, secret_key: str | None, bot_token: str | None
) -> dict[str, tuple[bool, str]]:
    """Her kaynak için (bağlı_mı, detay_mesajı) döner - sıra her zaman
    Yahoo Finance, Alpaca Markets, TEFAS, KAP, Telegram şeklindedir."""
    checks = {
        "Yahoo Finance": _check_yahoo_finance,
        "Alpaca Markets": lambda: _check_alpaca(key_id, secret_key),
        "TEFAS": _check_tefas,
        "KAP": _check_kap,
        "Telegram": lambda: _check_telegram(bot_token),
    }
    results: dict[str, tuple[bool, str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(checks)) as executor:
        future_to_name = {executor.submit(fn): name for name, fn in checks.items()}
        for future in concurrent.futures.as_completed(future_to_name):
            name = future_to_name[future]
            try:
                results[name] = future.result()
            except Exception as e:
                results[name] = (False, str(e))
    return {name: results[name] for name in checks}
