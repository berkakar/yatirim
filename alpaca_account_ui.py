"""Alpaca hesap türünün (Sanal Para / Gerçek Para) arayüz tarafı: seçili
türe göre kullanıcının anahtarları ve adresi, hesap türü rozeti ve onaylı
geçiş ayarı. Ayarın kendisi ve sunucu işleri için bkz. alpaca_account.py."""
import streamlit as st

import storage
from alpaca_account import (
    CONFIRM_TEXT, JOB_USERNAME, LIVE, MODE_LABELS, PAPER, has_live_keys, load_account_mode, save_account_mode,
    secrets_keys, trading_url,
)
from theme import get_palette

_MODE_STATE_KEY = "alpaca_account_mode_{}"


def _user_secrets(username: str) -> dict:
    return st.secrets.get("alpaca", {}).get(username, {}) or {}


def has_alpaca_account(username: str) -> bool:
    """secrets.toml'da kullanıcı için bir [alpaca.<kullanıcı>] bölümü var mı."""
    return bool(_user_secrets(username))


def get_account_mode(username: str) -> str:
    """Seçili hesap türü - oturum boyunca önbellekte (her yeniden çizimde
    GitHub'a gidilmesin); geçiş yapıldığında güncellenir."""
    key = _MODE_STATE_KEY.format(username)
    if key not in st.session_state:
        st.session_state[key] = load_account_mode(username, st.secrets.get("GITHUB_TOKEN"))
    return st.session_state[key]


def get_user_alpaca(username: str) -> tuple[str | None, str | None, str]:
    """(key_id, secret_key, trading_url) - seçili hesap türüne göre. Gerçek
    Para seçili ve gerçek hesap anahtarları yoksa anahtarlar None döner."""
    mode = get_account_mode(username)
    key_id, secret_key = secrets_keys(_user_secrets(username), mode)
    return key_id, secret_key, trading_url(mode)


def missing_keys_warning(username: str, extra: str = "") -> None:
    if get_account_mode(username) == LIVE:
        st.warning(
            f"Hesap türü **Gerçek Para** ama '{username}' için gerçek hesap anahtarları tanımlı değil "
            f"(`.streamlit/secrets.toml` içinde `[alpaca.{username}]` altında `live_key_id` / "
            f"`live_secret_key`). Giriş Sayfası'ndan Sanal Para'ya dönebilirsiniz.{extra}"
        )
    else:
        st.warning(
            f"'{username}' için Alpaca hesabı tanımlı değil (`.streamlit/secrets.toml` içinde "
            f"`[alpaca.{username}]`).{extra}"
        )


def is_live(username: str) -> bool:
    return has_alpaca_account(username) and get_account_mode(username) == LIVE


def render_account_mode_badge(username: str):
    """Sanal Para / Gerçek Para rozeti - Giriş Sayfası'nda ve Algoritmik
    Ticaret sayfalarının en üstünde. Gerçek Para kırmızı gösterilir."""
    p = get_palette()
    if get_account_mode(username) == LIVE:
        icon, detail, color = "💰", "Alpaca canlı hesap - emirler gerçek parayla gerçekleşir", p["negative"]
        label = MODE_LABELS[LIVE]
    else:
        icon, detail, color = "🧪", "Alpaca Paper Trading hesabı", p["info"]
        label = MODE_LABELS[PAPER]
    st.markdown(
        f'<div style="display:inline-block; padding:0.3rem 0.8rem; margin-bottom:0.6rem; '
        f'border:2px solid {color}; border-radius:999px; color:{color}; font-weight:700;">'
        f'{icon} {label} <span style="font-weight:400; opacity:0.85;">· {detail}</span></div>',
        unsafe_allow_html=True,
    )


def _switch(username: str, mode: str) -> None:
    token = st.secrets.get("GITHUB_TOKEN")
    try:
        save_account_mode(username, mode, token)
    except Exception as e:
        st.error(f"Hesap türü kaydedilemedi: {e}")
        return
    st.session_state[_MODE_STATE_KEY.format(username)] = mode
    # Önbelleğe alınmış pozisyon/emir/hesap verileri eski hesaba ait.
    st.cache_data.clear()
    st.session_state["_account_mode_switched"] = mode
    st.rerun()


def render_account_mode_setting(username: str):
    """Giriş Sayfası'ndaki hesap türü ayarı. Gerçek Para'ya geçiş onay kutusu
    ve onay metni ister; Sanal Para'ya dönüş tek tıkla yapılır."""
    switched = st.session_state.pop("_account_mode_switched", None)
    if switched:
        st.success(f"Alpaca hesap türü **{MODE_LABELS[switched]}** olarak kaydedildi.")

    mode = get_account_mode(username)
    with st.expander(f"⚙️ Alpaca Hesap Türü: {MODE_LABELS[mode]}", expanded=False):
        if not storage.enabled() and not st.secrets.get("GITHUB_TOKEN"):
            st.warning("`.streamlit/secrets.toml` içinde GITHUB_TOKEN tanımlı değil - hesap türü kaydedilemez.")
            return
        bots_note = (
            " Sunucudaki zamanlanmış botlar da (stop, alım, ORB, RS, Heikin Ashi, Otomatik Alım/Satım) "
            "bu ayarla aynı hesapta işlem yapar."
            if username == JOB_USERNAME else ""
        )
        if mode == LIVE:
            st.markdown("Şu an **💰 Gerçek Para** hesabı kullanılıyor - emirler gerçek parayla gerçekleşir." + bots_note)
            if st.button("🧪 Sanal Paraya Dön", key="account_mode_to_paper"):
                _switch(username, PAPER)
            return

        st.markdown("Şu an **🧪 Sanal Para** (Alpaca Paper Trading) hesabı kullanılıyor." + bots_note)
        if not has_live_keys(_user_secrets(username)):
            st.info(
                "Gerçek Para'ya geçmek için önce gerçek hesap anahtarlarını `.streamlit/secrets.toml` içinde "
                f"`[alpaca.{username}]` altına `live_key_id` ve `live_secret_key` olarak ekleyin"
                + (" ve sunucuda `/etc/yatirim/env` dosyasına `APCA_LIVE_API_KEY_ID` / "
                   "`APCA_LIVE_API_SECRET_KEY` yazın." if username == JOB_USERNAME else ".")
            )
            return

        st.warning(
            "**Gerçek Para'ya geçiş:** bundan sonra arayüzden ve botlardan verilen tüm emirler gerçek hesabınızda "
            "gerçek parayla gerçekleşir. Sanal hesaptaki açık pozisyonlar gerçek hesaba taşınmaz - geçişi, "
            "botların açık pozisyonu yokken yapmanız önerilir."
        )
        understood = st.checkbox(
            "Emirlerin gerçek parayla gerçekleşeceğini ve olası zararın gerçek olduğunu anlıyorum.",
            key="account_mode_live_ack",
        )
        typed = st.text_input(f"Onaylamak için **{CONFIRM_TEXT}** yazın", key="account_mode_live_text")
        confirmed = understood and typed.strip().upper() == CONFIRM_TEXT
        if st.button("💰 Gerçek Paraya Geç", type="primary", disabled=not confirmed, key="account_mode_to_live"):
            _switch(username, LIVE)
