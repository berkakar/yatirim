"""Telegram Bot API üzerinden bildirim gönderen ince istemci.

Tek bir bot (BOT_TOKEN, hem Streamlit secrets'ta hem GitHub Actions
secret'ında aynı) tüm kullanıcılar için paylaşılır; her kullanıcı kendi
chat_id'sini (bkz. get_recent_chats - botla konuştuktan sonra bulunur)
bildirim ayarlarına kaydeder.
"""
import requests

API_BASE = "https://api.telegram.org/bot{token}"


class TelegramError(Exception):
    """Telegram API'sine istek atarken/yanıtı işlerken oluşan hata."""


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> None:
    if not bot_token:
        raise TelegramError("Telegram bot token tanımlı değil.")
    if not chat_id:
        raise TelegramError("Telegram chat ID tanımlı değil.")

    url = API_BASE.format(token=bot_token) + "/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=15)
    except requests.RequestException as exc:
        raise TelegramError(f"Telegram'a bağlanılamadı: {exc}") from exc

    if r.status_code != 200:
        try:
            desc = r.json().get("description", r.text)
        except ValueError:
            desc = r.text
        raise TelegramError(f"Telegram mesajı gönderilemedi: {desc}")


def get_recent_chats(bot_token: str, limit: int = 10) -> list[dict]:
    """Botla en son konuşan kullanıcıları [{"chat_id","name"}, ...] olarak
    döner (en yeniden en eskiye) - kullanıcının kendi chat_id'sini bulması
    için UI'da kullanılır. Bot Telegram'da /start ile konuşulmadan hiçbir
    güncelleme dönmez."""
    if not bot_token:
        raise TelegramError("Telegram bot token tanımlı değil.")

    url = API_BASE.format(token=bot_token) + "/getUpdates"
    try:
        r = requests.get(url, params={"limit": 100}, timeout=15)
    except requests.RequestException as exc:
        raise TelegramError(f"Telegram'a bağlanılamadı: {exc}") from exc
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise TelegramError(f"Telegram API hatası: {data.get('description')}")

    seen = {}
    for update in reversed(data.get("result", [])):
        msg = update.get("message") or update.get("channel_post")
        if not msg:
            continue
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id"))
        if not chat_id or chat_id in seen:
            continue
        name = chat.get("title") or " ".join(
            filter(None, [chat.get("first_name"), chat.get("last_name")])
        ) or chat.get("username") or chat_id
        seen[chat_id] = name
        if len(seen) >= limit:
            break

    return [{"chat_id": cid, "name": name} for cid, name in seen.items()]
