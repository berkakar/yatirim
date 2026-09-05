"""Hisse Patern Modülü - kullanıcı tarafından seçilen hisselerin günlük
kapanış fiyatlarını kullanarak yıllık, 3 aylık ve aylık periyotlar arasındaki
tekrarlayan patern benzerliğini DTW (Dynamic Time Warping) ile ölçer ve
tablo halinde gösterir. Bkz. hisse_patern_analysis.py (hesaplama katmanı) ve
dtw_analysis.py (DTW algoritması - "🔄 DTW Zaman Serisi & Benzerlik Analizi"
modülüyle ortak).
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from hisse_patern_analysis import PERIOD_CONFIGS, build_detail_chart_series, compute_pattern_table
from ui_style import zebra_style

_SERIES_COLORS = ["#00d2ff", "#ff9f1c", "#2ec4b6", "#e63946", "#7209b7", "#ffbe0b", "#8ac926", "#ff006e", "#adb5bd", "#4361ee", "#f72585", "#4cc9f0"]
_SIM_COLUMNS = [f"{cfg['label']} Benzerlik %" for cfg in PERIOD_CONFIGS.values()]


def _build_column_config():
    config = {
        "Hisse": st.column_config.TextColumn("Hisse"),
        "Son Fiyat": st.column_config.NumberColumn("Son Fiyat", format="%.2f"),
    }
    for cfg in PERIOD_CONFIGS.values():
        config[f"{cfg['label']} Benzerlik %"] = st.column_config.NumberColumn(
            f"{cfg['label']} Benzerlik %",
            format="%.2f%%",
            help=(
                f"{cfg['label']} periyodundaki tamamlanmış dönemlerin (en fazla son "
                f"{cfg['n_periods']} tanesi) günlük kapanış fiyatları ikili olarak DTW "
                "ile karşılaştırılır; buradaki değer bu ikili skorların ortalamasıdır."
            ),
        )
    return config


def render_hisse_patern(target_list):
    st.caption(
        "Seçtiğiniz hisselerin günlük kapanış fiyatları kullanılarak, hissenin kendi "
        "geçmişindeki periyotlar (yıl / çeyrek / ay) DTW (Dynamic Time Warping) ile "
        "ikili olarak karşılaştırılır; ortalama benzerlik skoru o hissenin ne kadar "
        "tekrarlayan/mevsimsel bir fiyat pareni izlediğini gösterir."
    )

    selected = st.multiselect("🎯 Analiz edilecek hisseleri seçin:", target_list, key="hisse_patern_selection")

    col_btn, col_window = st.columns([2, 2])
    with col_window:
        max_warping_window = st.slider(
            "⏱️ Max. Zamansal Kayma (Gün):", min_value=1, max_value=10, value=5, step=1,
            help=(
                "DTW'nin eşleştirme yaparken kabul ettiği maksimum gün kayması. Resmi "
                "tatil/piyasa kapanışı gibi nedenlerle dönemler arası hizalama küçük "
                "kaymalar içerebilir; yüksek değer bu kaymalara daha toleranslıdır."
            ),
        )
    with col_btn:
        st.write("<br>", unsafe_allow_html=True)
        run = st.button("🚀 Patern Analizini Başlat", type="primary", disabled=not selected)

    if run:
        with st.spinner(f"{len(selected)} hissenin günlük verisi çekiliyor ve DTW patern benzerliği hesaplanıyor..."):
            rows, segments = compute_pattern_table(selected, max_warping_window)
        st.session_state.hisse_patern_rows = rows
        st.session_state.hisse_patern_segments = segments
        if not rows:
            st.error("⚠️ Seçilen hisseler için yeterli veri bulunamadı.")

    if not st.session_state.get("hisse_patern_rows"):
        st.info("Analizi başlatmak için yukarıdan hisse seçip **🚀 Patern Analizini Başlat** butonuna tıklayın.")
        return

    df = pd.DataFrame(st.session_state.hisse_patern_rows)
    for col in _SIM_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    st.divider()
    st.subheader("📊 Patern Benzerlik Tablosu")

    filt_col, thresh_col = st.columns([2, 2])
    with filt_col:
        filter_label = st.selectbox("🔎 Filtrelenecek Periyot:", [cfg["label"] for cfg in PERIOD_CONFIGS.values()])
    with thresh_col:
        min_sim = st.slider("🎯 Min. Benzerlik Skoru (%):", min_value=0, max_value=100, value=0, step=5)

    filter_col_name = f"{filter_label} Benzerlik %"
    df_filtered = df[df[filter_col_name].fillna(-1) >= min_sim].sort_values(by=filter_col_name, ascending=False)

    st.caption(
        f"Toplam {len(df)} hisseden, **{filter_label}** periyodunda %{min_sim} ve üzeri "
        f"benzerliğe sahip {len(df_filtered)} hisse listeleniyor."
    )

    display_cols = ["Hisse", "Son Fiyat"] + _SIM_COLUMNS
    if df_filtered.empty:
        st.warning("⚠️ Seçtiğiniz eşik değerinin üzerinde benzerlik gösteren hisse bulunamadı.")
    else:
        st.dataframe(
            zebra_style(df_filtered[display_cols]),
            column_config=_build_column_config(),
            use_container_width=True,
            hide_index=True,
        )

    st.divider()
    st.subheader("🔍 Patern Detayı")
    st.caption(
        "Tablodaki bir periyot sütununa \"tıklamanın\" karşılığı: bir hisse ve periyot tipi "
        "seçin, o periyoda ait geçmiş dönemlerin fiyatları farklı renklerde üst üste "
        "çizilsin (yıllık için aylık, 3 aylık için haftalık, aylık için günlük kapanışlar)."
    )

    tickers_with_data = df["Hisse"].tolist()
    dcol1, dcol2 = st.columns([2, 3])
    with dcol1:
        detail_ticker = st.selectbox("Hisse:", tickers_with_data, key="hisse_patern_detail_ticker")
    with dcol2:
        period_key = st.radio(
            "Periyot:",
            list(PERIOD_CONFIGS.keys()),
            format_func=lambda k: PERIOD_CONFIGS[k]["label"],
            horizontal=True,
            key="hisse_patern_detail_period",
        )

    cfg = PERIOD_CONFIGS[period_key]
    segments = st.session_state.hisse_patern_segments.get(detail_ticker, {}).get(period_key, [])

    if len(segments) < 2:
        st.warning(f"⚠️ **{detail_ticker}** için {cfg['label']} periyodunda karşılaştırmaya yeterli tamamlanmış dönem yok.")
        return

    row = df[df["Hisse"] == detail_ticker].iloc[0]
    sim_score = row.get(f"{cfg['label']} Benzerlik %")
    pair_count = len(segments) * (len(segments) - 1) // 2
    if pd.notna(sim_score):
        st.info(
            f"💡 **{detail_ticker}** - {cfg['label']} ortalama DTW benzerlik skoru: "
            f"**%{sim_score:.2f}** ({len(segments)} dönem, {pair_count} ikili karşılaştırma)"
        )

    chart_series = build_detail_chart_series(segments, cfg["chart_rule"])

    fig = go.Figure()
    for idx, s in enumerate(chart_series):
        color = _SERIES_COLORS[idx % len(_SERIES_COLORS)]
        fig.add_trace(go.Scatter(
            x=s["x"], y=s["prices"], mode="lines+markers", name=s["label"],
            line=dict(color=color, width=2),
            customdata=s["dates"],
            hovertemplate=f"<b>{s['label']}</b><br>Tarih: %{{customdata}}<br>Fiyat: %{{y:.2f}}<extra></extra>",
        ))

    fig.update_layout(
        title=f"{detail_ticker} - {cfg['label']} Patern Karşılaştırması",
        template="plotly_dark", height=550,
        xaxis=dict(title="Periyot İçi Sıra (dönemler aynı eksende üst üste hizalanmıştır)"),
        yaxis=dict(title="Kapanış Fiyatı"),
        legend_title_text="Dönem",
    )
    st.plotly_chart(fig, use_container_width=True)
