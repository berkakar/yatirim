"""Kullanıcının elinde bulunan (alım yaptığı) Türk fonlarını kaydedip
takip edebildiği modül.

Kaydedilen her fon için, fonu oluşturan en büyük 6 yatırım aracının
yüzdeleri KAP'ın (Kamuyu Aydınlatma Platformu) "Portföy Dağılım Raporu"
bildiriminden (bkz. kap_client.py) çekilir. Zaten önbellekte olan bir
rapor tarihi için tekrar KAP'a gidilmez; yeni bir rapor tarihi geldiğinde
eskisi silinmez, aynı hücrede ilave bir satır olarak birikir (bkz.
turk_fonlari_takip_data.add_report).
"""
import pandas as pd
import streamlit as st

import kap_client
import turk_fonlari_takip_data as data
from tefas_client import fetch_fund_by_code
from ui_style import zebra_style


def _init_state(username: str) -> None:
    if "takip_fonlari" not in st.session_state:
        st.session_state.takip_fonlari = data.load_tracked_funds(username)
    if "kap_portfoy_cache" not in st.session_state:
        st.session_state.kap_portfoy_cache = data.load_portfolio_cache()


def _refresh_fund(code: str, name: str) -> tuple[bool, str | None]:
    """KAP'ta bu fon için daha güncel bir Portföy Dağılım Raporu var mı
    kontrol eder; varsa indirip önbelleğe (session_state) ekler. Zaten
    güncelse KAP'a hiç PDF indirmeden döner. (değişti mi, hata mesajı)."""
    cache = st.session_state.kap_portfoy_cache
    try:
        meta = kap_client.find_latest_report_meta(code, name)
    except kap_client.KapFetchError as e:
        return False, str(e)

    if data.latest_report_date_sort(cache, code) == meta["report_date_sort"]:
        return False, None  # zaten en güncel rapor önbellekte

    try:
        meta["holdings"] = kap_client.fetch_holdings_for_report(meta["disclosure_index"])
    except kap_client.KapFetchError as e:
        return False, str(e)

    return data.add_report(cache, code, name, meta), None


def _format_holdings_cell(code: str) -> str:
    entry = st.session_state.kap_portfoy_cache.get(code)
    reports = (entry or {}).get("reports") or []
    if not reports:
        return "— (henüz veri yok, \"🔄 Tümünü Kontrol Et\" ile çekebilirsiniz)"

    lines = []
    for r in sorted(reports, key=lambda r: r["report_date_sort"], reverse=True):
        holdings_str = ", ".join(f"{h[0]} %{h[1]:.2f}" for h in r["holdings"])
        lines.append(f"{r['report_date']} ({r['period_label']}): {holdings_str}")
    return "\n".join(lines)


def render_turk_fonlari_takip(username: str) -> None:
    st.caption(
        "Elinizde bulunan fonları kodlarıyla ekleyin; her fon için KAP'ın periyodik "
        "\"Portföy Dağılım Raporu\" bildiriminden en büyük 6 yatırım aracının yüzdesi "
        "çekilir. Daha önce çekilmiş bir rapor önbellekte varsa tekrar KAP'a gidilmez; "
        "yeni bir rapor yayınlandığında eskisi silinmeden aynı hücreye ilave satır olarak eklenir."
    )

    _init_state(username)

    with st.form("add_takip_fon_form", clear_on_submit=True):
        col_input, col_btn = st.columns([3, 1])
        with col_input:
            new_code = st.text_input("Fon Kodu", placeholder="ör. THF").strip().upper()
        with col_btn:
            st.write("<br>", unsafe_allow_html=True)
            submitted = st.form_submit_button("➕ Ekle", use_container_width=True)

    if submitted:
        if not new_code:
            st.warning("⚠️ Lütfen bir fon kodu girin.")
        elif any(f["code"] == new_code for f in st.session_state.takip_fonlari):
            st.warning(f"⚠️ **{new_code}** zaten takip listenizde.")
        else:
            with st.spinner(f"{new_code} TEFAS'ta aranıyor..."):
                fund_info = fetch_fund_by_code(new_code)
            if not fund_info or not fund_info.get("fund_name"):
                st.error(f"❌ TEFAS'ta **{new_code}** kodlu bir fon bulunamadı.")
            else:
                fund_name = fund_info["fund_name"]
                st.session_state.takip_fonlari.append({"code": new_code, "name": fund_name})
                data.save_tracked_funds(st.session_state.takip_fonlari, username)

                with st.spinner(f"{new_code} için KAP'tan Portföy Dağılım Raporu çekiliyor..."):
                    changed, error = _refresh_fund(new_code, fund_name)
                if error:
                    st.warning(f"⚠️ **{new_code}** eklendi ama KAP'tan veri çekilemedi: {error}")
                else:
                    data.save_portfolio_cache(st.session_state.kap_portfoy_cache)
                    st.success(f"✅ **{new_code} - {fund_name}** takip listenize eklendi.")
                st.rerun()

    if not st.session_state.takip_fonlari:
        st.info("Henüz takip ettiğiniz bir fon yok - yukarıdan fon kodu girerek ekleyebilirsiniz.")
        return

    if st.button("🔄 Tümünü Kontrol Et", help="Her fon için KAP'ta daha güncel bir Portföy Dağılım Raporu olup olmadığını kontrol eder."):
        any_changed = False
        errors = []
        progress = st.progress(0)
        funds = st.session_state.takip_fonlari
        for i, f in enumerate(funds):
            changed, error = _refresh_fund(f["code"], f["name"])
            any_changed = any_changed or changed
            if error:
                errors.append(f"**{f['code']}**: {error}")
            progress.progress((i + 1) / len(funds))
        progress.empty()

        if any_changed:
            data.save_portfolio_cache(st.session_state.kap_portfoy_cache)
        if errors:
            st.warning("⚠️ Bazı fonlar için veri çekilemedi:\n\n" + "\n\n".join(errors))
        if any_changed and not errors:
            st.success("✅ Yeni rapor bulunan fonlar güncellendi.")
        elif not any_changed and not errors:
            st.info("Tüm fonlar zaten güncel.")

    table_rows = [
        {
            "Fon Kodu": f["code"],
            "Fon Adı": f["name"],
            "Yatırım Yüzdeleri": _format_holdings_cell(f["code"]),
        }
        for f in st.session_state.takip_fonlari
    ]
    df = pd.DataFrame(table_rows)
    st.dataframe(
        zebra_style(df),
        column_config={
            "Fon Kodu": st.column_config.TextColumn("Fon Kodu", width="small"),
            "Fon Adı": st.column_config.TextColumn("Fon Adı", width="medium"),
            "Yatırım Yüzdeleri": st.column_config.TextColumn("Yatırım Yüzdeleri (en büyük 6 yatırım aracı)", width="large"),
        },
        use_container_width=True,
        hide_index=True,
    )

    st.divider()
    st.subheader("🗑️ Takipten Çıkar")
    options = [f"{f['code']} - {f['name']}" for f in st.session_state.takip_fonlari]
    to_remove = st.selectbox("Takipten çıkarmak istediğiniz fon:", options)
    if st.button("Takipten Çıkar", type="secondary"):
        removed_code = to_remove.split(" - ")[0]
        st.session_state.takip_fonlari = [f for f in st.session_state.takip_fonlari if f["code"] != removed_code]
        data.save_tracked_funds(st.session_state.takip_fonlari, username)
        st.success(f"🗑️ **{removed_code}** takipten çıkarıldı.")
        st.rerun()
