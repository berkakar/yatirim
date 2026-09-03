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

from tefas_fonlari_data import CACHE_FILE, FUND_CATEGORIES, LOOKBACK_WINDOWS, load_cache
from ui_style import zebra_style

TR_TZ = ZoneInfo("Europe/Istanbul")

_PCT_COLUMNS = [f"{w}G Fiyat Değişim %" for w in LOOKBACK_WINDOWS] + [f"{w}G Hacim Değişim %" for w in LOOKBACK_WINDOWS]

# valuation.py'deki style_valuation_df ile aynı palet (uygulama genelinde
# tutarlılık için): kırmızı = olumsuz, teal/yeşil = olumlu.
_POSITIVE_COLOR = "color: #2ec4b6;"
_NEGATIVE_COLOR = "color: #e63946;"


def _style_turk_fonlari(df):
    """Fiyat/hacim değişim yüzdesi sütunlarını işaretine göre renklendirir,
    üzerine zebra_style'ın satır bandını uygular."""
    def apply_styles(val_df):
        style_df = pd.DataFrame("", index=val_df.index, columns=val_df.columns)
        for col in _PCT_COLUMNS:
            if col not in val_df.columns:
                continue
            for idx in val_df.index:
                v = val_df.loc[idx, col]
                if pd.notna(v):
                    style_df.loc[idx, col] = _POSITIVE_COLOR if v > 0 else (_NEGATIVE_COLOR if v < 0 else "")
        return style_df

    return zebra_style(df, extra_style_fn=apply_styles)


def _build_column_config() -> dict:
    config = {
        "Fon Kodu": st.column_config.TextColumn("Fon Kodu", help="TEFAS fon kodu", width="small"),
        "Fon Adı": st.column_config.TextColumn("Fon Adı", width="large"),
        "Kategori": st.column_config.TextColumn("Kategori", width="medium"),
        "Tarih": st.column_config.TextColumn("Tarih", help="Son fiyatın ait olduğu tarih", width="small"),
        "Önceki Gün Fiyat": st.column_config.NumberColumn("Önceki Gün Fiyat", format="%.4f"),
        "Bugünkü Fiyat": st.column_config.NumberColumn("Bugünkü Fiyat", format="%.4f"),
    }
    for w in LOOKBACK_WINDOWS:
        config[f"{w}G Fiyat Değişim %"] = st.column_config.NumberColumn(
            f"{w}G Fiyat Δ%", help=f"Son {w} iş günündeki fiyat değişimi", format="%.2f%%",
        )
        config[f"{w}G Hacim Değişim %"] = st.column_config.NumberColumn(
            f"{w}G Hacim Δ%", help=f"Son {w} iş günündeki fon toplam değeri (TL) değişimi", format="%.2f%%",
        )
    return config


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

    search_col, category_col = st.columns([2, 1])
    with search_col:
        search_term = st.text_input("🔍 Fon ara (kod veya isim):", "", placeholder="ör. AAK, Hisse Senedi...")
    with category_col:
        category_labels = ["Tümü"] + [label for label, _ in FUND_CATEGORIES.values()]
        selected_category = st.selectbox("Kategori filtresi", category_labels)

    df = pd.DataFrame(table)
    if selected_category != "Tümü":
        df = df[df["Kategori"] == selected_category]
    if search_term.strip():
        q = search_term.strip()
        mask = (
            df["Fon Kodu"].str.contains(q, case=False, na=False, regex=False)
            | df["Fon Adı"].str.contains(q, case=False, na=False, regex=False)
        )
        df = df[mask]

    st.caption(f"{len(df)} fon gösteriliyor.")
    st.dataframe(
        _style_turk_fonlari(df),
        column_config=_build_column_config(),
        use_container_width=True,
        hide_index=True,
    )
