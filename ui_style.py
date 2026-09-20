"""Uygulama genelinde tablolara tutarlı, tema-duyarlı görünüm kazandıran ortak
yardımcılar. st.dataframe (ve Styler kabul eden benzer bileşenler) hücre bazlı
CSS string'lerini render eder; burada satırlara alternatif arka plan (zebra)
uygulanır, tema (gündüz/gece) theme.py üzerinden okunur.
"""

import pandas as pd

from theme import get_palette


def get_zebra_colors():
    """(çift_satır_rengi, tek_satır_rengi) - aktif gündüz/gece moduna göre."""
    p = get_palette()
    return (p["bg_elevated"], p["bg_subtle"])


def zebra_style(df, extra_style_fn=None):
    """DataFrame'e tema-duyarlı satır bandı (zebra) stili uygular.

    extra_style_fn verilirse (df -> aynı boyutta CSS string'i DataFrame'i üreten
    bir fonksiyon), onun hücre bazlı stilleri zebra arka planının üzerine
    katmanlanır (örn. bir hücrenin kendi arka plan rengi varsa o öncelikli olur,
    diğer özellikler - yazı rengi, kalınlık - birlikte uygulanır).
    """
    even_bg, odd_bg = get_zebra_colors()
    p = get_palette()
    # st.dataframe zebra arka planını hücre bazlı background-color olarak
    # render eder, ama metin rengini kendi (statik config.toml temalı) ızgara
    # varsayılanından alır - gece modunda bu koyu zemin üzerinde koyu metne
    # (okunaksız) yol açar. Bu yüzden metin rengini de burada açıkça set
    # ediyoruz; extra_style_fn kendi renk vermişse (ör. pozitif/negatif) o
    # hücrede önceliklidir (aşağıda üzerine eklenir).
    base_style = f"background-color: {{bg}}; color: {p['text']};"

    def _apply(data):
        style_df = pd.DataFrame(
            [[base_style.format(bg=odd_bg if i % 2 else even_bg) for _ in data.columns] for i in range(len(data))],
            index=data.index, columns=data.columns,
        )
        if extra_style_fn is not None:
            extra_df = extra_style_fn(data)
            style_df = style_df + extra_df
        return style_df

    return df.style.apply(_apply, axis=None)
