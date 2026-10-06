"""Hesap ekranları: giriş / kayıt / şifre sıfırlama talebi, zorunlu şifre
değiştirme, 👤 Hesabım ve (yöneticiler için) 🛡️ Kullanıcı Yönetimi.

Kullanıcılar veritabanında (bkz. user_registry.py); bu modül yalnızca arayüz.
Yönetici bildirimleri secrets.toml'daki TELEGRAM_BOT_TOKEN ile
TELEGRAM_CHAT_ID'ye gönderilir.
"""
import time

import pandas as pd
import streamlit as st

import alpaca_keys
import user_registry as reg
from alpaca_account_ui import render_alpaca_keys_setting
from telegram_notify import send_telegram_message

# Oturum başına kötüye kullanım sınırları (saniye penceresi, en fazla deneme).
_RATE_WINDOW_S = 3600
_MAX_REGISTRATIONS = 3
_MAX_RESET_REQUESTS = 3


def _secret(name: str):
    try:
        return st.secrets.get(name)
    except Exception:  # secrets.toml hiç yoksa .get de hata fırlatır
        return None


def notify_admins(text: str) -> None:
    """Yönetici Telegram'ına bildirim. Ayar yoksa ya da gönderilemezse sessizce geçer
    (kayıt işlemi bildirim yüzünden başarısız sayılmasın)."""
    token, chat_id = _secret("TELEGRAM_BOT_TOKEN"), _secret("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        send_telegram_message(token, str(chat_id), text)
    except Exception:
        pass


def _rate_limited(key: str, limit: int) -> bool:
    """Bu oturumda son _RATE_WINDOW_S içinde `limit` deneme yapıldıysa True; değilse denemeyi sayar."""
    now = time.time()
    attempts = [t for t in st.session_state.get(key, []) if now - t < _RATE_WINDOW_S]
    if len(attempts) >= limit:
        st.session_state[key] = attempts
        return True
    st.session_state[key] = attempts + [now]
    return False


# ------------------------------------------------------------------ giriş ekranı

def _on_login(event: dict) -> None:
    try:
        reg.record_login(event.get("username") or "")
    except Exception:
        pass


def _render_registration() -> None:
    st.caption(
        "Başvurunuz yönetici onayından sonra etkinleşir. Davet kodunuz varsa hesabınız hemen açılır."
    )
    with st.form("register_form", clear_on_submit=False):
        username = st.text_input("Kullanıcı adı", help="3-20 karakter: küçük harf, rakam ve _")
        name = st.text_input("Ad Soyad")
        email = st.text_input("E-posta")
        password = st.text_input("Şifre", type="password",
                                 help=f"En az {reg.MIN_PASSWORD_LEN} karakter, harf ve rakam içermeli")
        confirm = st.text_input("Şifre (tekrar)", type="password")
        invite = st.text_input("Davet kodu (varsa)")
        risk_ack = st.checkbox(
            "Uygulamadaki analizlerin yatırım tavsiyesi olmadığını, alım/satım kararlarının ve olası "
            "zararların sorumluluğunun bana ait olduğunu kabul ediyorum."
        )
        submitted = st.form_submit_button("📝 Hesap Oluştur", type="primary")
    if not submitted:
        return
    if not risk_ack:
        st.error("Devam etmek için onay kutusunu işaretleyin.")
        return
    if _rate_limited("_register_attempts", _MAX_REGISTRATIONS):
        st.error("Bu oturumdan çok fazla başvuru yapıldı, lütfen daha sonra tekrar deneyin.")
        return
    try:
        status = reg.register(username, name, email, password, confirm, invite)
    except reg.RegistryError as e:
        st.error(str(e))
        return
    uname = reg.normalize_username(username)
    if status == reg.ACTIVE:
        st.success(f"Hesabınız açıldı. **{uname}** kullanıcı adıyla 'Giriş Yap' sekmesinden giriş yapabilirsiniz.")
        notify_admins(f"👤 Yeni kullanıcı (davet koduyla): {uname} - {name.strip()} <{email.strip()}>")
    else:
        st.success("Başvurunuz alındı. Yönetici onayladığında aynı kullanıcı adı ve şifreyle giriş yapabilirsiniz.")
        notify_admins(
            f"📝 Yeni üyelik başvurusu: {uname} - {name.strip()} <{email.strip()}>\n"
            "Onaylamak için: Yatırım Terminali → 👤 Hesap → 🛡️ Kullanıcı Yönetimi"
        )


def _render_reset_request() -> None:
    st.caption(
        "Talebiniz yöneticiye iletilir. Yönetici size geçici bir şifre verir; ilk girişte yeni şifre "
        "belirlemeniz istenir."
    )
    with st.form("reset_request_form", clear_on_submit=True):
        username = st.text_input("Kullanıcı adı")
        submitted = st.form_submit_button("📨 Şifre sıfırlama talebi gönder")
    if not submitted:
        return
    if _rate_limited("_reset_attempts", _MAX_RESET_REQUESTS):
        st.error("Bu oturumdan çok fazla talep gönderildi, lütfen daha sonra tekrar deneyin.")
        return
    uname = reg.normalize_username(username)
    user = reg.get_user(uname) if uname else None
    if user and user["status"] == reg.ACTIVE:
        reg.log_event(uname, "reset_request", uname)
        notify_admins(f"🔑 Şifre sıfırlama talebi: {uname} - {user['name']}")
    # Kullanıcı adının var olup olmadığı belli edilmesin.
    st.success("Talebiniz alındı. Hesap mevcutsa yönetici sizinle iletişime geçecek.")


def render_auth_screen(authenticator, render_switcher) -> None:
    """Giriş yapılmamışsa Giriş / Hesap Oluştur / Şifremi Unuttum sekmelerini çizer.
    Çerezle ya da formla giriş başarılı olursa sayfa yeniden çalıştırılır;
    giriş yapılmamışken st.stop() ile durur."""
    if st.session_state.get("authentication_status") is True:
        return
    render_switcher()
    notice = st.session_state.pop("_auth_notice", None)
    if notice:
        st.warning(notice)
    login_tab, register_tab, reset_tab = st.tabs(["🔐 Giriş Yap", "📝 Hesap Oluştur", "❓ Şifremi Unuttum"])
    with login_tab:
        try:
            authenticator.login(
                location="main", callback=_on_login,
                fields={"Form name": "Giriş", "Username": "Kullanıcı adı", "Password": "Şifre", "Login": "Giriş Yap"},
            )
        except Exception:
            # Çerezdeki kullanıcı artık aktif değil (devre dışı / silinmiş): çerezi sil, formu göster.
            st.session_state["authentication_status"] = None
            st.session_state["_auth_notice"] = "Hesabınız etkin değil ya da oturumunuz sona erdi."
            authenticator.cookie_controller.delete_cookie()
            st.rerun()
        status = st.session_state.get("authentication_status")
        if status is True:
            st.rerun()
        if status is False:
            st.error("❌ Kullanıcı adı veya şifre hatalı ya da hesabınız henüz onaylanmadı.")
    with register_tab:
        _render_registration()
    with reset_tab:
        _render_reset_request()
    st.stop()


def enforce_active_session(authenticator, username: str) -> dict:
    """Her çalıştırmada: kullanıcı hâlâ aktif mi (yönetici devre dışı bırakmış /
    silmiş olabilir; giriş çerezi o zamana kadar geçerli kalır). Değilse oturumu
    kapatır ve durur. Aktifse kullanıcı kaydını döner."""
    user = reg.get_user(username)
    if not user or user["status"] != reg.ACTIVE:
        # Çerez silinirken sayfa yeniden çalışabilir; mesaj giriş ekranında gösterilir.
        st.session_state["_auth_notice"] = "Hesabınız etkin değil. Yöneticiyle iletişime geçin."
        authenticator.logout(location="unrendered")
        st.rerun()
    return user


def render_forced_password_change(user: dict) -> None:
    """Yönetici şifreyi sıfırladıysa uygulamaya geçmeden önce yeni şifre istenir."""
    if not user["must_change_pw"]:
        return
    st.subheader("🔑 Yeni şifre belirleyin")
    st.info("Şifreniz yönetici tarafından sıfırlandı. Devam etmek için kendi şifrenizi belirleyin.")
    _render_password_form(user["username"], key="forced_pw")
    st.stop()


# ------------------------------------------------------------------ Hesabım

def _render_password_form(username: str, key: str) -> None:
    with st.form(f"{key}_form", clear_on_submit=True):
        current = st.text_input("Mevcut (ya da geçici) şifre", type="password")
        new = st.text_input("Yeni şifre", type="password",
                            help=f"En az {reg.MIN_PASSWORD_LEN} karakter, harf ve rakam içermeli")
        confirm = st.text_input("Yeni şifre (tekrar)", type="password")
        submitted = st.form_submit_button("Şifreyi değiştir", type="primary")
    if submitted:
        try:
            reg.change_password(username, current, new, confirm)
        except reg.RegistryError as e:
            st.error(str(e))
            return
        st.success("Şifreniz değiştirildi.")
        st.rerun()


def render_my_account(user: dict) -> None:
    username = user["username"]
    st.header("👤 Hesabım")
    role = "Yönetici" if user["role"] == reg.ADMIN else "Kullanıcı"
    st.caption(f"Kullanıcı adı: **{username}** · Rol: {role} · Üyelik: {user['created_at'][:10]}")

    render_alpaca_keys_setting(username)

    st.divider()
    st.subheader("🪪 Profil")
    with st.form("profile_form"):
        name = st.text_input("Ad Soyad", value=user["name"])
        email = st.text_input("E-posta", value=user["email"])
        if st.form_submit_button("Kaydet"):
            try:
                reg.update_profile(username, name, email, username)
            except reg.RegistryError as e:
                st.error(str(e))
            else:
                st.success("Profil güncellendi.")

    st.divider()
    st.subheader("🔑 Şifre Değiştir")
    _render_password_form(username, key="my_pw")


# ------------------------------------------------------------------ Kullanıcı Yönetimi

def _fmt_time(value) -> str:
    return value[:16].replace("T", " ") if value else "-"


def _render_pending(admin: str) -> None:
    pending = reg.list_users(reg.PENDING)
    if not pending:
        st.info("Onay bekleyen başvuru yok.")
        return
    for u in pending:
        with st.container(border=True):
            st.markdown(f"**{u['username']}** · {u['name']} · {u['email']}")
            st.caption(f"Başvuru: {_fmt_time(u['created_at'])} UTC")
            c1, c2, c3 = st.columns([1, 2, 1])
            if c1.button("✅ Onayla", key=f"approve_{u['username']}", type="primary"):
                reg.approve(u["username"], admin)
                st.session_state["_admin_msg"] = (
                    f"'{u['username']}' onaylandı. Kullanıcıya giriş yapabileceğini bildirin."
                )
                st.rerun()
            note = c2.text_input("Ret nedeni (isteğe bağlı)", key=f"reject_note_{u['username']}",
                                 label_visibility="collapsed", placeholder="Ret nedeni (isteğe bağlı)")
            if c3.button("❌ Reddet", key=f"reject_{u['username']}"):
                reg.reject(u["username"], admin, note)
                st.session_state["_admin_msg"] = f"'{u['username']}' başvurusu reddedildi."
                st.rerun()


def _render_users(admin: str) -> None:
    users = reg.list_users()
    if not users:
        st.info("Kullanıcı yok.")
        return
    table = pd.DataFrame([{
        "Kullanıcı": u["username"], "Ad Soyad": u["name"], "E-posta": u["email"],
        "Durum": reg.STATUS_LABELS[u["status"]], "Rol": "Yönetici" if u["role"] == reg.ADMIN else "Kullanıcı",
        "Alpaca": " / ".join(m for m, label in (("paper", "Paper"), ("live", "Live"))
                             if _safe_has_keys(u["username"], m)) or "-",
        "Son giriş": _fmt_time(u["last_login_at"]), "Kayıt": _fmt_time(u["created_at"]),
    } for u in users])
    st.dataframe(table, use_container_width=True, hide_index=True)

    names = [u["username"] for u in users]
    selected = st.selectbox("İşlem yapılacak kullanıcı", names, key="admin_user_select")
    user = next(u for u in users if u["username"] == selected)
    is_self = selected == admin

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Rol**")
        new_role = st.radio("Rol", [reg.USER, reg.ADMIN], index=[reg.USER, reg.ADMIN].index(user["role"]),
                            format_func=lambda r: "Yönetici" if r == reg.ADMIN else "Kullanıcı",
                            key=f"role_{selected}", label_visibility="collapsed", horizontal=True)
        if new_role != user["role"] and st.button("Rolü kaydet", key=f"role_save_{selected}"):
            _admin_action(lambda: reg.set_role(selected, new_role, admin), f"'{selected}' rolü güncellendi.")
    with c2:
        st.markdown("**Durum**")
        if user["status"] == reg.ACTIVE:
            if st.button("⛔ Devre dışı bırak", key=f"disable_{selected}", disabled=is_self):
                _admin_action(lambda: reg.disable(selected, admin), f"'{selected}' devre dışı bırakıldı.")
        elif user["status"] == reg.DISABLED:
            if st.button("✅ Etkinleştir", key=f"enable_{selected}"):
                _admin_action(lambda: reg.enable(selected, admin), f"'{selected}' etkinleştirildi.")
        elif user["status"] in (reg.PENDING, reg.REJECTED):
            if st.button("✅ Onayla", key=f"approve2_{selected}"):
                _admin_action(lambda: reg.approve(selected, admin), f"'{selected}' onaylandı.")
    with c3:
        st.markdown("**Şifre**")
        if st.button("🔑 Geçici şifre üret", key=f"reset_{selected}", disabled=is_self,
                     help="Kendi şifrenizi Hesabım sayfasından değiştirin."):
            temp = reg.reset_password(selected, admin)
            st.session_state["_admin_temp_pw"] = (selected, temp)
            st.rerun()
    temp = st.session_state.pop("_admin_temp_pw", None)
    if temp:
        st.warning(f"**{temp[0]}** için geçici şifre: `{temp[1]}`  \n"
                   "Bu şifre bir daha gösterilmez; kullanıcıya güvenli bir kanaldan iletin. "
                   "İlk girişte yeni şifre belirlemesi istenecek.")

    with st.expander(f"🗑️ '{selected}' kullanıcısını sil", expanded=False):
        st.caption("Kullanıcı, Alpaca anahtarları ve (seçilirse) tüm ayar/strateji kayıtları silinir. "
                   "Geri alınamaz; sadece girişi engellemek için 'Devre dışı bırak' kullanın.")
        purge = st.checkbox("Kullanıcının uygulama verilerini de sil", value=True, key=f"purge_{selected}")
        typed = st.text_input("Onaylamak için kullanıcı adını yazın", key=f"delete_confirm_{selected}")
        if st.button("Kalıcı olarak sil", key=f"delete_{selected}", type="primary",
                     disabled=is_self or typed.strip().lower() != selected):
            def _delete():
                reg.delete_user(selected, admin, purge_data=purge)
                alpaca_keys.delete_all(selected)
            _admin_action(_delete, f"'{selected}' silindi.")

    with st.expander("📜 Bu kullanıcının işlem geçmişi"):
        log = reg.audit_log(selected, limit=50)
        if log:
            st.dataframe(pd.DataFrame(log), use_container_width=True, hide_index=True)
        else:
            st.caption("Kayıt yok.")


def _safe_has_keys(username: str, mode: str) -> bool:
    try:
        return alpaca_keys.has_keys(username, mode)
    except alpaca_keys.KeyStoreError:
        return False


def _admin_action(fn, success: str) -> None:
    try:
        fn()
    except (reg.RegistryError, alpaca_keys.KeyStoreError) as e:
        st.error(str(e))
        return
    st.session_state["_admin_msg"] = success
    st.rerun()


def _render_invites(admin: str) -> None:
    st.caption("Davet koduyla yapılan başvurular onay beklemeden aktif olur.")
    with st.form("invite_form"):
        c1, c2 = st.columns(2)
        max_uses = c1.number_input("Kullanım hakkı", min_value=1, max_value=100, value=1, step=1)
        days = c2.number_input("Geçerlilik (gün, 0 = süresiz)", min_value=0, max_value=365, value=7, step=1)
        if st.form_submit_button("🎟️ Davet kodu üret", type="primary"):
            code = reg.create_invite(admin, int(max_uses), int(days) or None)
            st.success(f"Davet kodu: `{code}`")
    invites = reg.list_invites()
    if not invites:
        return
    st.dataframe(pd.DataFrame([{
        "Kod": i["code"], "Kullanım": f"{i['used']}/{i['max_uses']}", "Son tarih": _fmt_time(i["expires_at"]),
        "Durum": "Geçerli" if i["usable"] else ("İptal" if i["revoked"] else "Bitti"),
        "Oluşturan": i["created_by"], "Oluşturma": _fmt_time(i["created_at"]),
    } for i in invites]), use_container_width=True, hide_index=True)
    usable = [i["code"] for i in invites if i["usable"]]
    if usable:
        c1, c2 = st.columns([3, 1])
        code = c1.selectbox("İptal edilecek kod", usable, key="invite_revoke_select")
        if c2.button("İptal et", key="invite_revoke"):
            _admin_action(lambda: reg.revoke_invite(code, admin), f"{code} iptal edildi.")


def render_user_admin(admin: str) -> None:
    st.header("🛡️ Kullanıcı Yönetimi")
    if not reg.is_admin(admin):
        st.error("Bu sayfa yalnızca yöneticiler içindir.")
        return
    msg = st.session_state.pop("_admin_msg", None)
    if msg:
        st.success(msg)
    if not _secret("TELEGRAM_CHAT_ID") or not _secret("TELEGRAM_BOT_TOKEN"):
        st.info("Yeni başvuru bildirimleri için secrets.toml'a TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID ekleyin.")
    pending_count = len(reg.list_users(reg.PENDING))
    tabs = st.tabs([f"⏳ Bekleyenler ({pending_count})", "👥 Kullanıcılar", "🎟️ Davet Kodları", "📜 İşlem Kaydı"])
    with tabs[0]:
        _render_pending(admin)
    with tabs[1]:
        _render_users(admin)
    with tabs[2]:
        _render_invites(admin)
    with tabs[3]:
        log = reg.audit_log(limit=300)
        if log:
            st.dataframe(pd.DataFrame(log).rename(columns={
                "at": "Zaman (UTC)", "username": "Kullanıcı", "action": "İşlem", "actor": "Yapan", "detail": "Ayrıntı",
            }), use_container_width=True, hide_index=True)
        else:
            st.caption("Kayıt yok.")
