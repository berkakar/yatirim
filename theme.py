"""Gündüz (açık) ve gece (koyu) kullanım modları için uygulama renk paleti,
CSS enjeksiyonu ve mod anahtarı (switcher).

Streamlit'in kendi tema sistemi (.streamlit/config.toml) çalışma zamanında iki
özel paleti programatik olarak değiştirmeyi desteklemediği için, burada asıl
görünümü CSS değişkenleri ve hedefli seçicilerle biz kontrol ediyoruz.
config.toml sadece ilk boya (flash) sırasında görünen statik geri düşüş değeri.

Not: Streamlit'in yerleşik `st.dataframe` ızgarası (Styler kullanılmayan çıplak
tablolar) ve bazı yerleşik bileşenler (tooltip, toast) config.toml'daki statik
temadan renk alır; bu modül onları anlık olarak değiştiremez. Bu dosyadaki
`zebra_style` tabanlı tablolar ve Plotly grafikleri ise tam olarak mod ile
birlikte değişir.
"""

import streamlit as st

DAY = "day"
NIGHT = "night"
_DEFAULT_MODE = DAY
_QUERY_KEY = "theme"
_STATE_KEY = "app_theme"

MODE_LABELS = {DAY: "☀️ Gündüz", NIGHT: "🌙 Gece"}

# "Gündüz": yeşil-mavi ağırlıklı, açık/ferah bir finans terminali paleti.
# "Gece": camgöbeği/lacivert ağırlıklı, TradingView/Bloomberg tarzı koyu
# terminal paleti (kontrast ve göz yorgunluğu için saf siyah değil).
PALETTES = {
    # Not: metin/arka plan çiftleri (text, text_muted, positive, negative,
    # warning - hem bg_elevated hem bg_subtle üzerinde) WCAG AA (>=4.5:1)
    # eşiğini geçecek şekilde seçildi; tablolarda okunabilirlik için önemli.
    DAY: dict(
        bg="#f4f9f8",
        bg_elevated="#ffffff",
        bg_subtle="#dcebe6",
        sidebar_bg="#eef6f4",
        border="#c9ddd7",
        text="#0f2a2c",
        text_muted="#4f6b69",
        primary="#0a7f72",
        primary_hover="#086b60",
        on_primary="#ffffff",
        accent="#2f6fed",
        positive="#0f6b38",
        negative="#c31f35",
        warning="#8a5606",
        info="#2f6fed",
        code_bg="#eef2f7",
        plotly_template="plotly_white",
    ),
    NIGHT: dict(
        bg="#0a0e17",
        bg_elevated="#131a29",
        bg_subtle="#212c47",
        sidebar_bg="#0d1320",
        border="#324066",
        text="#e7edf7",
        text_muted="#8b96ac",
        primary="#2dd4bf",
        primary_hover="#5eead4",
        on_primary="#04141f",
        accent="#5b8cff",
        positive="#34d399",
        negative="#f87171",
        warning="#fbbf24",
        info="#5b8cff",
        code_bg="#0f1626",
        plotly_template="plotly_dark",
    ),
}


def _init_mode():
    if _STATE_KEY not in st.session_state:
        qp_val = st.query_params.get(_QUERY_KEY)
        st.session_state[_STATE_KEY] = qp_val if qp_val in PALETTES else _DEFAULT_MODE
    return st.session_state[_STATE_KEY]


def get_mode():
    """Aktif mod: 'day' ya da 'night'."""
    return st.session_state.get(_STATE_KEY) or _init_mode()


def get_palette(mode=None):
    return PALETTES[mode or get_mode()]


def get_plotly_template(mode=None):
    return get_palette(mode)["plotly_template"]


def positive_color(mode=None):
    return get_palette(mode)["positive"]


def negative_color(mode=None):
    return get_palette(mode)["negative"]


def set_mode(mode):
    if mode not in PALETTES or st.session_state.get(_STATE_KEY) == mode:
        return
    st.session_state[_STATE_KEY] = mode
    st.query_params[_QUERY_KEY] = mode
    st.rerun()


def render_mode_switcher(key="theme_mode_switcher"):
    """Gündüz/Gece seçimi için kompakt bir kontrol çizer (login ekranı ve
    kenar çubuğunda kullanılır)."""
    mode = get_mode()
    options = [DAY, NIGHT]
    if hasattr(st, "segmented_control"):
        choice = st.segmented_control(
            "Görünüm", options=options, format_func=lambda m: MODE_LABELS[m],
            default=mode, key=key, label_visibility="collapsed",
        )
        choice = choice or mode
    else:
        choice = st.radio(
            "Görünüm", options=options, format_func=lambda m: MODE_LABELS[m],
            index=options.index(mode), horizontal=True, key=key,
            label_visibility="collapsed",
        )
    set_mode(choice)


def inject_css(mode=None):
    p = get_palette(mode)
    st.markdown(
        f"""
        <style>
        .stApp {{
            background-color: {p['bg']} !important;
            color: {p['text']} !important;
        }}
        [data-testid="stHeader"] {{
            background-color: {p['bg']} !important;
        }}
        [data-testid="stSidebar"] {{
            background-color: {p['sidebar_bg']} !important;
            border-right: 1px solid {p['border']};
        }}
        [data-testid="stSidebar"] * {{
            color: {p['text']};
        }}
        h1, h2, h3, h4, h5, h6, p, span, label, div {{
            color: {p['text']};
        }}
        [data-testid="stCaptionContainer"], small, .stCaption {{
            color: {p['text_muted']} !important;
        }}
        hr {{
            border-color: {p['border']} !important;
        }}
        a, a:visited {{
            color: {p['accent']} !important;
        }}
        code, pre, [data-testid="stCodeBlock"] {{
            background-color: {p['code_bg']} !important;
        }}

        /* Formlar (giriş kutusu dahil) */
        div[data-testid="stForm"] {{
            background-color: {p['bg_elevated']};
            border: 1px solid {p['border']};
            border-radius: 12px;
            padding: 1.5rem;
        }}

        /* Butonlar */
        .stButton > button, .stDownloadButton > button {{
            background-color: {p['bg_elevated']};
            color: {p['text']};
            border: 1px solid {p['border']};
            border-radius: 8px;
        }}
        .stButton > button:hover, .stDownloadButton > button:hover {{
            border-color: {p['primary']};
            color: {p['primary']};
        }}
        button[kind="primary"], [data-testid="stBaseButton-primary"] {{
            background-color: {p['primary']} !important;
            color: {p['on_primary']} !important;
            border: none !important;
        }}
        button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {{
            background-color: {p['primary_hover']} !important;
        }}
        /* Giriş formundaki gönder düğmesi ana eylem olarak vurgulanır */
        [data-testid="stFormSubmitButton"] button {{
            background-color: {p['primary']} !important;
            color: {p['on_primary']} !important;
            border: none !important;
        }}
        [data-testid="stFormSubmitButton"] button:hover {{
            background-color: {p['primary_hover']} !important;
        }}

        /* Metin/parola/sayı girişleri */
        input, textarea {{
            background-color: {p['bg_elevated']} !important;
            color: {p['text']} !important;
            border-color: {p['border']} !important;
        }}
        /* Parola alanındaki göster/gizle düğmesi (girişle aynı arka plana uysun) */
        div[data-testid="stTextInputRootElement"] > div,
        div[data-testid="stTextInputRootElement"] button {{
            background-color: {p['bg_elevated']} !important;
        }}
        div[data-testid="stTextInputRootElement"] button span {{
            color: {p['text_muted']} !important;
        }}
        [data-baseweb="select"] > div, [data-baseweb="base-input"] {{
            background-color: {p['bg_elevated']} !important;
            border-color: {p['border']} !important;
            color: {p['text']} !important;
        }}
        [data-baseweb="popover"], [data-baseweb="menu"], ul[role="listbox"] {{
            background-color: {p['bg_elevated']} !important;
        }}
        li[role="option"] {{
            background-color: {p['bg_elevated']} !important;
            color: {p['text']} !important;
        }}
        li[role="option"]:hover, li[aria-selected="true"] {{
            background-color: {p['bg_subtle']} !important;
        }}

        /* Sekmeler */
        [data-baseweb="tab-list"] {{
            border-bottom: 1px solid {p['border']};
        }}
        [data-baseweb="tab"] {{
            color: {p['text_muted']};
        }}
        [aria-selected="true"][data-baseweb="tab"] {{
            color: {p['primary']} !important;
        }}
        [data-baseweb="tab-highlight"] {{
            background-color: {p['primary']} !important;
        }}

        /* Genişletilebilir bölümler */
        [data-testid="stExpander"] {{
            background-color: {p['bg_elevated']};
            border: 1px solid {p['border']};
            border-radius: 8px;
        }}

        /* Metrikler */
        [data-testid="stMetric"] {{
            background-color: {p['bg_elevated']};
            border: 1px solid {p['border']};
            border-radius: 10px;
            padding: 0.75rem 1rem;
        }}

        /* st.dataframe ızgarası <canvas> üzerine çizilir; başlık satırının
        (ve Styler ile boyanmamış hücrelerin) rengi Streamlit'in tek statik
        temasından (config.toml) gelir ve CSS ile değiştirilemez. Burada en
        azından ızgarayı temayla uyumlu bir çerçeveyle "kart" gibi sarmalıyoruz
        ki başlık şeridi kopuk değil, kasıtlı bir tasarım gibi görünsün. */
        [data-testid="stDataFrame"] {{
            border: 1px solid {p['border']};
            border-radius: 8px;
            overflow: hidden;
        }}

        /* Segmented control / radio (görünüm anahtarı dahil) */
        [data-testid="stSegmentedControl"] label[data-baseweb="radio"],
        [data-testid="stSegmentedControl"] button {{
            background-color: {p['bg_elevated']};
            border-color: {p['border']} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
