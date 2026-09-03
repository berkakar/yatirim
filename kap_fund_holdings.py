"""Türk Fonları modülü altında, kullanıcının sahip olduğu fonları
kaydedip her biri için KAP'tan çekilen en yüksek 6 hisse pozisyonunu
gösteren bölüm - bkz. kap_fund_holdings_data.py (kalıcı saklama) ve
kap_client.py / kap_holdings_parser.py (KAP'ta bildirim bulma + PDF parse).
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from kap_fund_holdings_data import add_fund, load_data, refresh_fund, remove_fund
from tefas_fonlari_data import load_cache as load_tefas_cache

TR_TZ = ZoneInfo("Europe/Istanbul")


def _format_ts(iso_ts: str | None) -> str:
    if not iso_ts:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TR_TZ)
        return dt.astimezone(TR_TZ).strftime("%d.%m.%Y %H:%M:%S") + " TRT"
    except ValueError:
        return iso_ts


def _fund_name_lookup() -> dict[str, str]:
    table = load_tefas_cache().get("table") or []
    return {row["Fon Kodu"]: row["Fon Adı"] for row in table}


def render_kap_holdings(username: str):
    st.subheader("📌 Sahip Olduğum Fonlar")
    st.caption(
        "Bir fon kodu ekleyip \"Kontrol Et\"e bastığında, KAP'ta o fon için "
        "yayınlanmış en güncel \"Portföy Dağılım Raporu\" bildirimi aranır; "
        "daha önce parse edilenden farklı (yeni) bir rapor varsa indirilip "
        "en yüksek 6 hisse pozisyonu çıkarılır."
    )

    name_lookup = _fund_name_lookup()
    data = load_data(username)
    funds = data.get("funds", {})

    with st.form("kap_add_fund_form", clear_on_submit=True):
        code = st.text_input("Fon kodu ekle (ör. AAK)", "").strip().upper()
        submitted = st.form_submit_button("➕ Ekle")
        if submitted and code:
            if code in funds:
                st.warning(f"'{code}' zaten listende.")
            else:
                add_fund(username, code, name_lookup.get(code, ""))
                st.rerun()

    if not funds:
        st.info("Henüz eklenmiş bir fon yok.")
        return

    for code in sorted(funds.keys()):
        entry = funds[code]
        fund_name = entry.get("fund_name") or name_lookup.get(code, "")
        title = f"**{code}** · {fund_name}" if fund_name else f"**{code}**"
        with st.expander(title, expanded=True):
            info_col, action_col = st.columns([3, 1])
            with info_col:
                st.caption(f"Son kontrol: {_format_ts(entry.get('last_checked'))}")
                st.caption(f"Son parse edilen rapor: {_format_ts(entry.get('last_parsed'))}")
                if entry.get("report_title"):
                    st.caption(f"Rapor: {entry['report_title']} ({entry.get('report_publish_date') or '-'})")
                if entry.get("last_error"):
                    st.warning(entry["last_error"])
            with action_col:
                if st.button("🔄 Kontrol Et", key=f"kap_refresh_{code}"):
                    with st.spinner(f"{code} için KAP kontrol ediliyor..."):
                        refresh_fund(username, code)
                    st.rerun()
                if st.button("🗑️ Kaldır", key=f"kap_remove_{code}"):
                    remove_fund(username, code)
                    st.rerun()

            holdings = entry.get("holdings")
            if holdings:
                st.dataframe(
                    pd.DataFrame(holdings),
                    column_config={"Oran %": st.column_config.NumberColumn("Oran %", format="%.2f%%")},
                    hide_index=True,
                    use_container_width=True,
                )
            else:
                st.caption("Henüz hisse verisi yok - \"Kontrol Et\"e bas.")
