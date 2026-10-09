"""Alpaca hesap türünün (Sanal Para / Gerçek Para) arayüz tarafı: seçili
türe göre kullanıcının anahtarları ve adresi, hesap türü rozeti, onaylı
geçiş ayarı ve Hesabım sayfasındaki anahtar formu. Ayarın kendisi ve sunucu
işleri için bkz. alpaca_account.py, anahtarların saklanması için alpaca_keys.py."""
import streamlit as st

import alpaca_keys
import storage
from alpaca_account import (
    CONFIRM_TEXT, JOB_USERNAME, LIVE, MODE_LABELS, PAPER, has_live_keys, load_account_mode, save_account_mode,
    trading_url, user_keys,
)
from alpaca_client import AlpacaClient
from theme import get_palette

_MODE_STATE_KEY = "alpaca_account_mode_{}"
KEYS_HINT = "**Hesabım → Alpaca Anahtarları** bölümünden"


def has_alpaca_account(username: str) -> bool:
    """Kullanıcı Hesabım sayfasında herhangi bir (paper ya da live) anahtar girmiş mi."""
    try:
        return alpaca_keys.has_keys(username, PAPER) or alpaca_keys.has_keys(username, LIVE)
    except alpaca_keys.KeyStoreError:
        return False


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
    try:
        key_id, secret_key = user_keys(username, mode)
    except alpaca_keys.KeyStoreError as e:
        st.error(str(e))
        key_id = secret_key = None
    return key_id, secret_key, trading_url(mode)


def missing_keys_warning(username: str, extra: str = "") -> None:
    if get_account_mode(username) == LIVE:
        st.warning(
            f"Hesap türü **Gerçek Para** ama gerçek hesap anahtarlarınız girilmemiş - {KEYS_HINT} "
            f"ekleyin ya da Giriş Sayfası'ndan Sanal Para'ya dönün.{extra}"
        )
    else:
        st.warning(f"Alpaca Sanal Para (paper) anahtarlarınız girilmemiş - {KEYS_HINT} ekleyin.{extra}")


def is_live(username: str) -> bool:
    return has_alpaca_account(username) and get_account_mode(username) == LIVE


def render_account_mode_badge(username: str):
    """Sanal Para / Gerçek Para rozeti - Giriş Sayfası'nda ve Algoritmik
    Ticaret sayfalarının en üstünde. Gerçek Para kırmızı gösterilir."""
    p = get_palette()
    if get_account_mode(username) == LIVE:
        detail, color = "Alpaca canlı hesap - emirler gerçek parayla gerçekleşir", p["negative"]
        label = MODE_LABELS[LIVE]
    else:
        detail, color = "Alpaca Paper Trading hesabı", p["info"]
        label = MODE_LABELS[PAPER]
    st.markdown(
        f'<div style="display:inline-block; padding:0.3rem 0.8rem; margin-bottom:0.6rem; '
        f'border:2px solid {color}; border-radius:999px; color:{color}; font-weight:700;">'
        f'{label} <span style="font-weight:400; opacity:0.85;">· {detail}</span></div>',
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
    with st.expander(f"Alpaca Hesap Türü: {MODE_LABELS[mode]}", expanded=False):
        if not storage.enabled() and not st.secrets.get("GITHUB_TOKEN"):
            st.warning("`.streamlit/secrets.toml` içinde GITHUB_TOKEN tanımlı değil - hesap türü kaydedilemez.")
            return
        bots_note = (
            " Sunucudaki zamanlanmış botlar da (stop, alım, ORB, RS, Heikin Ashi, Otomatik Alım/Satım) "
            "bu ayarla aynı hesapta işlem yapar."
            if username == JOB_USERNAME else ""
        )
        if mode == LIVE:
            st.markdown("Şu an **Gerçek Para** hesabı kullanılıyor - emirler gerçek parayla gerçekleşir." + bots_note)
            if st.button("Sanal Paraya Dön", key="account_mode_to_paper"):
                _switch(username, PAPER)
            return

        st.markdown("Şu an **Sanal Para** (Alpaca Paper Trading) hesabı kullanılıyor." + bots_note)
        if not has_live_keys(username):
            st.info(f"Gerçek Para'ya geçmek için önce gerçek hesap anahtarlarınızı {KEYS_HINT} girin.")
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
        if st.button("Gerçek Paraya Geç", type="primary", disabled=not confirmed, key="account_mode_to_live"):
            _switch(username, LIVE)


def _test_keys(key_id: str, secret_key: str, mode: str) -> tuple[bool, str]:
    """Anahtarları Alpaca'da dener; (başarılı mı, açıklama)."""
    try:
        account = AlpacaClient(key_id, secret_key, trading_url(mode)).get_account()
    except Exception as e:
        return False, str(e)
    number = (account or {}).get("account_number") or "?"
    status = (account or {}).get("status") or "?"
    return True, f"hesap {number}, durum {status}"


def _render_keys_form(username: str, mode: str) -> None:
    info = alpaca_keys.key_info(username, mode)
    label = MODE_LABELS[mode]
    if info:
        st.markdown(f"Kayıtlı: `{alpaca_keys.mask(info['key_id'])}` · güncellendi {info['updated_at'][:16].replace('T', ' ')} UTC")
    else:
        st.caption(f"{label} anahtarı girilmemiş.")

    with st.form(f"alpaca_keys_form_{mode}", clear_on_submit=True):
        key_id = st.text_input("API Key ID", key=f"alpaca_key_id_{mode}")
        secret_key = st.text_input("Secret Key", type="password", key=f"alpaca_secret_{mode}")
        ack = True
        if mode == LIVE:
            ack = st.checkbox(
                "Bunun gerçek parayla işlem yapan canlı hesabımın anahtarı olduğunu anlıyorum.",
                key="alpaca_live_keys_ack",
            )
        submitted = st.form_submit_button("Bağlantıyı test et ve kaydet", type="primary")
    if submitted:
        if not ack:
            st.error("Kaydetmek için onay kutusunu işaretleyin.")
            return
        if not key_id.strip() or not secret_key.strip():
            st.error("API Key ID ve Secret Key boş olamaz.")
            return
        ok, detail = _test_keys(key_id.strip(), secret_key.strip(), mode)
        if not ok:
            st.error(f"Alpaca bu anahtarları kabul etmedi ({label} adresi: {trading_url(mode)}): {detail}")
            return
        try:
            alpaca_keys.set_keys(username, mode, key_id, secret_key)
        except alpaca_keys.KeyStoreError as e:
            st.error(str(e))
            return
        st.cache_data.clear()
        st.success(f"{label} anahtarları doğrulandı ve şifreli olarak kaydedildi ({detail}).")

    if info and st.button(f"{label} anahtarlarını sil", key=f"alpaca_keys_delete_{mode}"):
        if mode == LIVE and get_account_mode(username) == LIVE:
            # Gerçek Para seçiliyken anahtar kalmazsa her şey hata verir; önce Sanal Para'ya dön.
            save_account_mode(username, PAPER, st.secrets.get("GITHUB_TOKEN"))
            st.session_state[_MODE_STATE_KEY.format(username)] = PAPER
        alpaca_keys.delete_keys(username, mode)
        st.cache_data.clear()
        st.rerun()


def render_alpaca_keys_setting(username: str) -> None:
    """Hesabım sayfası: kullanıcının kendi Sanal Para ve Gerçek Para anahtarları."""
    st.subheader("Alpaca Anahtarları")
    if not alpaca_keys.encryption_available():
        st.error(
            "Sunucuda şifreleme anahtarı (YATIRIM_SECRET_KEY) tanımlı değil; Alpaca anahtarları kaydedilemez. "
            "Yöneticinin `sudo bash deploy/web/users.sh anahtar` çalıştırması gerekiyor."
        )
        return
    st.caption(
        "Anahtarları Alpaca panelinde *API Keys* bölümünden oluşturabilirsiniz. Kaydetmeden önce Alpaca'da "
        "denenir; gizli anahtar veritabanında şifreli saklanır ve bir daha gösterilmez."
        + (" Sunucudaki zamanlanmış botlar da bu anahtarlarla işlem yapar." if username == JOB_USERNAME else "")
    )
    paper_tab, live_tab = st.tabs(["Sanal Para (Paper)", "Gerçek Para (Live)"])
    with paper_tab:
        _render_keys_form(username, PAPER)
    with live_tab:
        st.warning("Gerçek hesap anahtarlarıyla verilen emirler gerçek parayla gerçekleşir. Hesap türünü "
                   "Giriş Sayfası'ndaki ayardan ayrıca onaylayarak değiştirirsiniz.")
        _render_keys_form(username, LIVE)
