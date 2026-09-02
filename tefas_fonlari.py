"""Türk Fonları modülü - TEFAS'tan günlük çekilen (bkz. tefas_fetch.py,
GitHub Actions ile günde 1 kez) Hisse Senedi Yoğun / Değişken / Mutlak
Getiri / İstatistiksel Arbitraj fonlarının fiyat ve hacim (fon toplam
değeri) değişim tablosunu gösterir.

Bu modül TEFAS'a hiç istek atmaz - sadece tefas_fetch.py'nin GitHub'a
commit'lediği önbellek dosyasını (tefas_fonlari_cache.json) okur, bu yüzden
Alpaca modüllerindeki gibi kullanıcıya özel API anahtarı gerekmez.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from tefas_fonlari_data import CACHE_FILE, FUND_CATEGORIES, load_cache
from ui_style import zebra_style

TR_TZ = ZoneInfo("Europe/Istanbul")


def render_turk_fonlari():
    st.caption(
        "Kategori, fon unvanındaki anahtar kelimelere göre otomatik belirlenir. "
        "Veriler günde 1 kez, TEFAS fiyatlarını tamamladıktan 10 dakika sonra "
        "otomatik güncellenir - \"X Günlük\" ifadeleri iş günü sayısını belirtir "
        "(TEFAS hafta sonu/tatil günleri veri yayınlamaz)."
    )

    cache = load_cache()
    table = cache.get("table") or []
    meta = cache.get("meta") or {}

    if not table:
        st.info(
            "Henüz önbelleklenmiş veri yok - ilk otomatik çalıştırma bekleniyor "
            f"(bkz. `{CACHE_FILE}`, GitHub Actions ile günde 1 kez güncellenir)."
        )
        return

    last_updated = meta.get("last_updated")
    if last_updated:
        try:
            ts = datetime.fromisoformat(last_updated).astimezone(TR_TZ)
            st.caption(f"Son güncelleme: {ts.strftime('%d.%m.%Y %H:%M:%S')} TRT · {len(table)} fon.")
        except ValueError:
            pass

    category_labels = ["Tümü"] + [label for label, _ in FUND_CATEGORIES.values()]
    selected = st.selectbox("Kategori filtresi", category_labels)

    df = pd.DataFrame(table)
    if selected != "Tümü":
        df = df[df["Kategori"] == selected]

    st.dataframe(zebra_style(df), use_container_width=True, hide_index=True)
