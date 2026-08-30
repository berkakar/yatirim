"""Uygulama genelinde tablolara tutarlı, tema-duyarlı görünüm kazandıran ortak
yardımcılar. st.dataframe (ve Styler kabul eden benzer bileşenler) hücre bazlı
CSS string'lerini render eder; burada satırlara alternatif arka plan (zebra)
uygulanır, tema tipi st.context.theme üzerinden okunur.
"""

import pandas as pd
import streamlit as st

DARK_COLORS = ("#0e1117", "#161b22")
LIGHT_COLORS = ("#ffffff", "#f0f2f6")


def get_zebra_colors():
    """(çift_satır_rengi, tek_satır_rengi) - aktif temaya göre."""
    theme_type = st.context.theme.get("type") or "dark"
    return LIGHT_COLORS if theme_type == "light" else DARK_COLORS


def zebra_style(df, extra_style_fn=None):
    """DataFrame'e tema-duyarlı satır bandı (zebra) stili uygular.

    extra_style_fn verilirse (df -> aynı boyutta CSS string'i DataFrame'i üreten
    bir fonksiyon), onun hücre bazlı stilleri zebra arka planının üzerine
    katmanlanır (örn. bir hücrenin kendi arka plan rengi varsa o öncelikli olur,
    diğer özellikler - yazı rengi, kalınlık - birlikte uygulanır).
    """
    even_bg, odd_bg = get_zebra_colors()

    def _apply(data):
        style_df = pd.DataFrame(
            [[f"background-color: {odd_bg if i % 2 else even_bg};" for _ in data.columns] for i in range(len(data))],
            index=data.index, columns=data.columns,
        )
        if extra_style_fn is not None:
            extra_df = extra_style_fn(data)
            style_df = style_df + extra_df
        return style_df

    return df.style.apply(_apply, axis=None)
