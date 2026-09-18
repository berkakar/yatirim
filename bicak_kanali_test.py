"""Bıçak Kanalı Test modülü - bicak_kanali.py'deki (kılavuz/bıçak/sıfır
çizgisi + türetilmiş oran) analizini gerçek piyasa verisiyle hızlıca
denemek için bağımsız bir sayfa. Herhangi bir alım/satım sinyaline bağlı
değildir, sadece yöntemin görsel doğrulaması amaçlıdır - bkz.
bicak_kanali.py."""

import plotly.graph_objects as go
import streamlit as st

from bicak_kanali import BicakKanali, find_channel
from scanner import SCAN_TIMEFRAMES, SCAN_TIMEFRAME_LABELS, bars_from_df, get_scanner_data
from structure import Bar

DEFAULT_LOOKBACK_DAYS = 180

_POSITIVE_HEX = "#2ec4b6"
_NEGATIVE_HEX = "#e63946"
_KILAVUZ_COLOR = "#e63946"      # direnç tarafı
_SIFIR_COLOR = "#2ec4b6"        # destek tarafı
_BICAK_COLOR = "#ffd60a"
_YESIL_CIZGI_COLOR = "#06d6a0"


def render_bicak_kanali_test(target_list):
    st.header("🔪 Bıçak Kanalı Test Modülü")
    st.caption(
        "bicak_kanali.py'deki kılavuz/bıçak/sıfır çizgisi ve bunlardan türetilen "
        "uzatma seviyesini (yeşil çizgi) gerçek piyasa verisiyle hızlıca denemek için "
        "bağımsız bir test sayfası. Üretimdeki bir alım/satım sinyaline bağlı değildir."
    )

    col_ticker, col_tf, col_days = st.columns([2, 1, 1])
    with col_ticker:
        ticker = st.selectbox("Hisse:", target_list, key="bicak_kanali_test_ticker")
    with col_tf:
        timeframe = st.selectbox(
            "Mum Seviyesi:", SCAN_TIMEFRAMES,
            format_func=lambda tf: SCAN_TIMEFRAME_LABELS[tf],
            index=SCAN_TIMEFRAMES.index("1Day"), key="bicak_kanali_test_timeframe",
        )
    with col_days:
        lookback_days = st.number_input(
            "Geriye Dönük Gün Sayısı:", min_value=5, max_value=730,
            value=DEFAULT_LOOKBACK_DAYS, step=5, key="bicak_kanali_test_days",
        )

    if not st.button("📈 Analiz Et", type="primary"):
        return

    with st.spinner(f"{ticker} verisi getiriliyor..."):
        df, _cup, _obo, _tobo = get_scanner_data(ticker, timeframe=timeframe, period_days=int(lookback_days))

    if df is None or df.empty:
        st.error(f"❌ {ticker} için geçerli piyasa verisi alınamadı.")
        return

    bars = bars_from_df(df)
    result = find_channel(bars)

    if result is None:
        st.info(
            "ℹ️ Bu hisse/mum seviyesi/gün aralığı için geçerli bir bıçak kanalı "
            "kurulamadı (yeterli tepe/dip yapısı yok, ya da düşüş sona doğru yavaşlayıp "
            "beklenen sıfır çizgisi < bıçak < kılavuz sıralaması bozuluyor). Başka bir "
            "hisse, mum seviyesi veya gün aralığı deneyebilirsiniz."
        )
        return

    _render_chart(bars, ticker, timeframe, result)

    st.caption(
        f"Seçilen düşüş: {result.leg_tepe.t[:10]} ({result.leg_tepe.price:.2f}) → "
        f"{result.leg_dip.t[:10]} ({result.leg_dip.price:.2f}) · "
        f"üst_oran: {result.ust_oran:.4f} · alt_oran: {result.alt_oran:.4f} · "
        f"türetilmiş_oran: {result.turetilmis_oran:.4f}"
    )


def _render_chart(bars: list[Bar], ticker: str, timeframe: str, result: BicakKanali):
    n = len(bars)
    xs = list(range(n))
    dates = [b.t[:10] for b in bars]

    fig = go.Figure(data=[go.Candlestick(
        x=xs, open=[b.o for b in bars], high=[b.h for b in bars],
        low=[b.l for b in bars], close=[b.c for b in bars], name="Fiyat",
        increasing=dict(line=dict(color=_POSITIVE_HEX), fillcolor=_POSITIVE_HEX),
        decreasing=dict(line=dict(color=_NEGATIVE_HEX), fillcolor=_NEGATIVE_HEX),
        text=dates, hoverinfo="x+text",
    )])

    tick_step = max(1, n // 12)
    fig.update_xaxes(tickmode="array", tickvals=xs[::tick_step], ticktext=dates[::tick_step])

    fig.add_trace(go.Scatter(
        x=[result.leg_tepe.index, result.leg_dip.index],
        y=[result.leg_tepe.price, result.leg_dip.price],
        mode="markers+text", name="Seçilen Düşüş",
        text=["Tepe", "Dip"], textposition="top center",
        marker=dict(symbol="star", size=14, color="#ffffff", line=dict(color="#000000", width=1)),
    ))

    for label, line, color in (
        ("Kılavuz", result.kilavuz, _KILAVUZ_COLOR),
        ("Bıçak", result.bicak, _BICAK_COLOR),
        ("Sıfır Çizgisi", result.sifir_cizgisi, _SIFIR_COLOR),
    ):
        slope, intercept = line
        fig.add_trace(go.Scatter(
            x=xs, y=[slope * x + intercept for x in xs], mode="lines",
            name=label, line=dict(color=color, width=1.5, dash="dot"),
        ))

    fig.add_trace(go.Scatter(
        x=xs, y=[result.yesil_cizgi(x) for x in xs], mode="lines",
        name="Yeşil Çizgi (türetilmiş)", line=dict(color=_YESIL_CIZGI_COLOR, width=2, dash="dash"),
    ))

    fig.update_layout(
        title=f"{ticker} - Bıçak Kanalı ({SCAN_TIMEFRAME_LABELS.get(timeframe, timeframe)})",
        template="plotly_dark", height=650, xaxis_rangeslider_visible=False,
        xaxis_title="Bar # (üzerine gelince tarih görünür)",
    )
    st.plotly_chart(fig, use_container_width=True)
