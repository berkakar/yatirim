"""Giriş Sayfası'ndaki "Piyasa Duyarlılığı" ve "ABD Sektör ETF'leri" bölümleri -
market_sentiment.py'nin günlük servisinin yazdığı kayıtları gösterir."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import market_sentiment as ms
from theme import get_palette, get_plotly_template
from ui_style import freshness_caption, zebra_style

TR_TZ = ZoneInfo("Europe/Istanbul")


def _history_chart(history):
    df = pd.DataFrame(history)
    df["date"] = pd.to_datetime(df["date"])
    palette = get_palette()
    fig = go.Figure(go.Scatter(
        x=df["date"], y=df["score"], mode="lines", line=dict(width=2, color=palette["accent"]),
        hovertemplate="%{x|%d.%m.%Y}: <b>%{y:.0f}</b><extra></extra>",
    ))
    # Etiket sınırları (25 / 45 / 55 / 75) silik kesikli çizgi olarak.
    for level in (25, 45, 55, 75):
        fig.add_hline(y=level, line=dict(width=1, dash="dot", color="rgba(128,128,128,0.45)"))
    fig.update_layout(
        template=get_plotly_template(), height=220, margin=dict(l=0, r=0, t=8, b=0),
        yaxis=dict(range=[0, 100], tickvals=[0, 25, 50, 75, 100], showgrid=False),
        xaxis=dict(showgrid=False), hovermode="x unified", showlegend=False,
    )
    return fig


def _render_market(market, snap):
    st.markdown(f"**{market}**")
    if not snap:
        st.info("Henüz hesaplanmadı - servis hafta içi ABD kapanışından sonra çalışır.")
        return
    delta = None
    if snap.get("previous") is not None and snap.get("score") is not None:
        delta = round(snap["score"] - snap["previous"], 1)
    st.metric(
        snap.get('label', ''), f"{snap['score']:.0f} / 100",
        delta=f"{delta:+.1f} (önceki gün)" if delta is not None else None,
        help="0 = aşırı korku, 100 = aşırı açgözlülük. 25 / 45 / 55 / 75 sınırları: "
             "Aşırı Korku · Korku · Nötr · Açgözlülük · Aşırı Açgözlülük.",
    )
    if snap.get("history"):
        st.plotly_chart(_history_chart(snap["history"]), use_container_width=True,
                        config={"displayModeBar": False}, key=f"sentiment_chart_{market}")

    rows = [
        {"Bileşen": c["label"], "Skor": c["score"], "Açıklama": c["detail"]}
        for c in snap.get("components", {}).values()
    ]
    st.dataframe(
        pd.DataFrame(rows), hide_index=True, use_container_width=True,
        column_config={
            "Skor": st.column_config.ProgressColumn("Skor", min_value=0, max_value=100, format="%.0f"),
        },
    )
    pc = snap.get("put_call")
    if pc:
        st.caption(f"Opsiyon put/call hacim oranı ({pc['symbol']}, en yakın vadeler): **{pc['ratio']}** "
                   f"→ {pc['score']:.0f}/100 · skora katılmaz, yalnızca bilgi amaçlı.")
    when = _computed_at(snap.get("updated_at"))
    freshness_caption(f"Kapanış verisi: {snap['as_of']}{when} · {snap.get('universe_size', 0)} hisse "
                      f"(Yahoo Finance, günlük servis).")


def _computed_at(updated):
    try:
        dt = datetime.strptime(updated, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return f", hesaplandı: {dt.astimezone(TR_TZ):%d.%m.%Y %H:%M} TRT"
    except (TypeError, ValueError):
        return ""


def _pct_style(df: pd.DataFrame) -> pd.DataFrame:
    palette = get_palette()
    styles = pd.DataFrame("", index=df.index, columns=df.columns)
    for c in ("Günlük %", "1 Hafta %", "SPY'ye Göre (1 Hafta)"):
        for i, v in df[c].items():
            if isinstance(v, (int, float)) and not pd.isna(v) and v != 0:
                styles.at[i, c] = f"color: {palette['positive'] if v > 0 else palette['negative']}; font-weight: 600"
    return styles


def render_sector_etfs():
    st.subheader("ABD Sektör ETF'leri")
    st.caption("S&P 500'ün 11 sektörünü izleyen SPDR Select Sector ETF'leri - son bir haftalık (5 işlem günü) "
               "değişime göre güçlüden zayıfa sıralı.")
    snap = ms.load_sectors()
    if not snap.get("sectors"):
        st.info("Henüz hesaplanmadı - servis hafta içi ABD kapanışından sonra çalışır.")
        if st.button("Şimdi hesapla", key="sectors_compute_now",
                     help="Servisi beklemeden Yahoo Finance'ten hesaplar (birkaç saniye)."):
            with st.spinner("Sektör ETF'leri hesaplanıyor..."):
                failed = ms.run_sectors()
            if failed:
                st.warning("Sektör ETF'leri hesaplanamadı - Yahoo Finance'e ulaşılamamış olabilir.")
            else:
                st.rerun()
        return

    bench = snap.get("benchmark") or {}
    df = pd.DataFrame([{
        "ETF": r["symbol"], "Sektör": r["name"], "Kapanış $": r["close"],
        "Günlük %": r["day_pct"], "1 Hafta %": r["week_pct"],
        "SPY'ye Göre (1 Hafta)": r["vs_benchmark"], "Son 1 Ay": r.get("trend") or [],
    } for r in snap["sectors"]])
    styler = zebra_style(df, _pct_style).format(
        {"Kapanış $": "{:,.2f}", "Günlük %": "{:+.2f}", "1 Hafta %": "{:+.2f}", "SPY'ye Göre (1 Hafta)": "{:+.2f}"},
        na_rep="—")
    st.dataframe(
        styler, hide_index=True, use_container_width=True,
        column_config={
            "SPY'ye Göre (1 Hafta)": st.column_config.NumberColumn(
                "SPY'ye Göre (1 Hafta)", help="ETF'nin 1 haftalık değişimi − SPY'nin 1 haftalık değişimi "
                                                "(yüzde puan). Pozitif: sektör piyasadan güçlü."),
            "Son 1 Ay": st.column_config.LineChartColumn("Son 1 Ay", help="Son ~20 işlem günü kapanışları."),
        },
    )
    if bench.get("week_pct") is not None:
        day = f", günlük {bench['day_pct']:+.2f}%" if bench.get("day_pct") is not None else ""
        st.caption(f"Karşılaştırma: {bench['symbol']} (S&P 500) son 1 hafta {bench['week_pct']:+.2f}%{day}.")
    freshness_caption(f"Kapanış verisi: {snap['as_of']}{_computed_at(snap.get('updated_at'))} "
                      "(Yahoo Finance, günlük servis).")


def render_market_sentiment():
    st.subheader("Piyasa Duyarlılığı (Korku / Açgözlülük)")
    st.caption("Momentum, oynaklık endeksi, genişlik, yeni zirve/dip ve güvenli liman talebinden "
               "hesaplanan 0-100 skor. Her hafta içi ABD kapanışından sonra güncellenir.")
    data = ms.load_all()
    markets = [cfg["market"] for cfg in ms.MARKETS.values()]
    cols = st.columns(len(markets))
    for col, market in zip(cols, markets):
        with col:
            _render_market(market, data.get(market))

    if not all(data.get(m) for m in markets):
        if st.button("Şimdi hesapla", key="sentiment_compute_now",
                     help="Servisi beklemeden Yahoo Finance'ten hesaplar (~1 dakika)."):
            with st.spinner("Piyasa duyarlılığı hesaplanıyor..."):
                missing = [slug for slug, cfg in ms.MARKETS.items() if not data.get(cfg["market"])]
                failures = ms.run(missing, pause_s=0)
            if failures:
                st.warning("Bazı piyasalar hesaplanamadı - Yahoo Finance'e ulaşılamamış olabilir.")
            else:
                st.rerun()
