"""Kullanıcının fon hisse düşüş bildirimi ayarlarının (Telegram chat ID,
günlük kayıp eşiği) kalıcılığı - bkz. config.py'deki save_initial_capital
ile aynı GitHub + yerel dosya yedekleme deseni.

Bu dosyalar düz JSON olarak repoya commit'lendiği için hem Streamlit
tarafı (GitHub API ile) hem de fon_hisse_uyari.py'yi çalıştıran GitHub
Actions (kendi checkout'undan doğrudan dosya okuyarak) aynı veriye
erişebiliyor.
"""
import json
import os

import streamlit as st

from config import GITHUB_REPO
from github_config import read_json_from_github, write_json_to_github

DEFAULT_LOSS_THRESHOLD_PCT = -3.0


def _settings_file(username: str) -> str:
    return f"bildirim_ayarlari_{username}.json"


def load_notification_settings(username: str) -> dict:
    settings_file = _settings_file(username)
    data = None
    token = st.secrets.get("GITHUB_TOKEN")
    if token:
        try:
            data = read_json_from_github(GITHUB_REPO, token, settings_file, None)
        except Exception:
            data = None

    if data is None and os.path.exists(settings_file):
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = None

    data = data or {}
    return {
        "telegram_chat_id": data.get("telegram_chat_id", ""),
        "loss_threshold_pct": data.get("loss_threshold_pct", DEFAULT_LOSS_THRESHOLD_PCT),
    }


def save_notification_settings(settings: dict, username: str) -> None:
    settings_file = _settings_file(username)
    token = st.secrets.get("GITHUB_TOKEN")
    if token:
        try:
            write_json_to_github(GITHUB_REPO, token, settings_file, settings, f"Update bildirim ayarları ({username})")
        except Exception as e:
            st.warning(f"⚠️ Bildirim ayarları GitHub'a kalıcı olarak kaydedilemedi (sadece bu oturumda geçerli olacak): {e}")

    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
