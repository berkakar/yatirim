"""Giriş Sayfası'ndaki "Piyasa Duyarlılığı" bölümü - market_sentiment.py'nin
günlük servisinin yazdığı kaydı gösterir."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import market_sentiment as ms
from theme import get_palette, get_plotly_template
from ui_style import freshness_caption

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
        f"{snap.get('icon', '')} {snap.get('label', '')}", f"{snap['score']:.0f} / 100",
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
    updated = snap.get("updated_at")
    when = ""
    if updated:
        try:
            dt = datetime.strptime(updated, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            when = f", hesaplandı: {dt.astimezone(TR_TZ):%d.%m.%Y %H:%M} TRT"
        except ValueError:
            pass
    freshness_caption(f"Kapanış verisi: {snap['as_of']}{when} · {snap.get('universe_size', 0)} hisse "
                      f"(Yahoo Finance, günlük servis).")


def render_market_sentiment():
    st.subheader("🧭 Piyasa Duyarlılığı (Korku / Açgözlülük)")
    st.caption("Momentum, oynaklık endeksi, genişlik, yeni zirve/dip ve güvenli liman talebinden "
               "hesaplanan 0-100 skor. Her hafta içi ABD kapanışından sonra güncellenir.")
    data = ms.load_all()
    markets = [cfg["market"] for cfg in ms.MARKETS.values()]
    cols = st.columns(len(markets))
    for col, market in zip(cols, markets):
        with col:
            _render_market(market, data.get(market))

    if not all(data.get(m) for m in markets):
        if st.button("🔄 Şimdi hesapla", key="sentiment_compute_now",
                     help="Servisi beklemeden Yahoo Finance'ten hesaplar (~1 dakika)."):
            with st.spinner("Piyasa duyarlılığı hesaplanıyor..."):
                missing = [slug for slug, cfg in ms.MARKETS.items() if not data.get(cfg["market"])]
                failures = ms.run(missing, pause_s=0)
            if failures:
                st.warning("Bazı piyasalar hesaplanamadı - Yahoo Finance'e ulaşılamamış olabilir.")
            else:
                st.rerun()
