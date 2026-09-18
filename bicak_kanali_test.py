"""Bıçak Kanalı Test modülü - bicak_kanali.py'deki analizi gerçek piyasa
verisiyle hızlıca denemek için bağımsız bir sayfa. Herhangi bir alım/satım
sinyaline bağlı değildir, sadece yöntemin adım adım görsel doğrulaması
amaçlıdır - bkz. bicak_kanali.py. Şu an gösterilenler: düşüş trendi, bu
trendin en tepe/son tepe noktasından geçen kılavuz çizgisi ve trendin en
dip noktasından, kılavuza paralel geçen bıçak çizgisi."""

import plotly.graph_objects as go
import streamlit as st

from bicak_kanali import Kilavuz, find_kilavuz
from scanner import SCAN_TIMEFRAMES, SCAN_TIMEFRAME_LABELS, bars_from_df, get_scanner_data
from structure import Bar

DEFAULT_LOOKBACK_DAYS = 180

_POSITIVE_HEX = "#2ec4b6"
_NEGATIVE_HEX = "#e63946"
_KILAVUZ_COLOR = "#e63946"
_TREND_COLOR = "#ff9f1c"
_BICAK_COLOR = "#ffd60a"


def render_bicak_kanali_test(target_list):
    st.header("🔪 Bıçak Kanalı Test Modülü")
    st.caption(
        "bicak_kanali.py'deki yöntemi gerçek piyasa verisiyle adım adım doğrulamak için "
        "bağımsız bir test sayfası. Şu an gösterilenler: en büyük genlikli düşüş trendi, "
        "bu trendin tepe adayları arasından en yükseği ile kronolojik olarak en son "
        "oluşanı seçilip bu iki noktadan çekilen kılavuz çizgisi, ve trendin en dip "
        "noktasından kılavuza paralel geçen bıçak çizgisi. "
        "Üretimdeki bir alım/satım sinyaline bağlı değildir."
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
    result = find_kilavuz(bars)

    if result is None:
        st.info(
            "ℹ️ Bu hisse/mum seviyesi/gün aralığında bir düşüş trendi ya da kılavuz "
            "çizgisi için yeterli tepe noktası (en az 2) bulunamadı. Başka bir hisse, "
            "mum seviyesi veya gün aralığı deneyebilirsiniz."
        )
        return

    _render_chart(bars, ticker, timeframe, result)

    en_tepe, son_tepe = result.kilavuz_noktalari
    st.caption(
        f"Seçilen düşüş: {result.leg_tepe.t[:10]} ({result.leg_tepe.price:.2f}) → "
        f"{result.leg_dip.t[:10]} ({result.leg_dip.price:.2f}) · "
        f"toplam tepe adayı: {len(result.tepe_pivots)} · "
        f"kılavuz noktaları: {en_tepe.t[:10]} ({en_tepe.price:.2f}) ve "
        f"{son_tepe.t[:10]} ({son_tepe.price:.2f}) · "
        f"en dip nokta: {result.en_dip.t[:10]} ({result.en_dip.price:.2f})"
    )


def _render_chart(bars: list[Bar], ticker: str, timeframe: str, result: Kilavuz):
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
        mode="lines+markers+text", name="Trend Çizgisi (Tepe-Dip)",
        text=["Tepe", "Dip"], textposition="top center",
        line=dict(color=_TREND_COLOR, width=2),
        marker=dict(symbol="star", size=14, color="#ffffff", line=dict(color="#000000", width=1)),
    ))

    fig.add_trace(go.Scatter(
        x=[p.index for p in result.tepe_pivots],
        y=[p.price for p in result.tepe_pivots],
        mode="markers", name="Tepe Adayları",
        marker=dict(symbol="triangle-down", size=8, color=_KILAVUZ_COLOR, opacity=0.5,
                    line=dict(color="#000000", width=1)),
    ))

    en_tepe, son_tepe = result.kilavuz_noktalari
    fig.add_trace(go.Scatter(
        x=[en_tepe.index, son_tepe.index], y=[en_tepe.price, son_tepe.price],
        mode="markers+text", name="Kılavuz Noktaları (seçilen 2)",
        text=["En Tepe", "Son Tepe"], textposition="top center",
        marker=dict(symbol="diamond", size=13, color=_KILAVUZ_COLOR, line=dict(color="#000000", width=1.5)),
    ))

    slope, intercept = result.kilavuz
    fig.add_trace(go.Scatter(
        x=xs, y=[slope * x + intercept for x in xs], mode="lines",
        name="Kılavuz", line=dict(color=_KILAVUZ_COLOR, width=2, dash="dot"),
    ))

    fig.add_trace(go.Scatter(
        x=[result.en_dip.index], y=[result.en_dip.price],
        mode="markers+text", name="En Dip Nokta",
        text=["En Dip"], textposition="bottom center",
        marker=dict(symbol="diamond", size=13, color=_BICAK_COLOR, line=dict(color="#000000", width=1.5)),
    ))

    bicak_slope, bicak_intercept = result.bicak
    fig.add_trace(go.Scatter(
        x=xs, y=[bicak_slope * x + bicak_intercept for x in xs], mode="lines",
        name="Bıçak", line=dict(color=_BICAK_COLOR, width=2, dash="dot"),
    ))

    fig.update_layout(
        title=f"{ticker} - Kılavuz + Bıçak ({SCAN_TIMEFRAME_LABELS.get(timeframe, timeframe)})",
        template="plotly_dark", height=650, xaxis_rangeslider_visible=False,
        xaxis_title="Bar # (üzerine gelince tarih görünür)",
    )
    st.plotly_chart(fig, use_container_width=True)
