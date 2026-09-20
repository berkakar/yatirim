"""Bıçak Kanalı Test modülü - piyasayı tarayıp bicak_kanali.py'deki
kılavuz/bıçak/sıfır çizgisi + türetilmiş oran yöntemini her hisseye
uygular; yeşil çizginin (alım çizgisi) gerçek fiyatla en son kesiştiği
(= en güncele en yakın, dolayısıyla en yakın alım fırsatı sayılan) barı
bulup hisseleri bir tabloda listeler (bkz. "Alım Bölgesi Tarama"
modülündeki tarama deseni - app.py). Sonuç tablosu, "Hisse Patern
Analizi" modülündeki gibi sütun başlıklarına tıklanarak sıralanabilir
(st.dataframe + hücre seçimi). Herhangi bir alım/satım sinyaline bağlı
değildir, sadece yöntemin görsel doğrulaması amaçlıdır - bkz.
bicak_kanali.py."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from bicak_kanali import Kilavuz, find_kilavuz, yesil_cizgi_kesisimi
from scanner import (
    DAILY_DEFAULT_DAYS, DAILY_MAX_DAYS, INTRADAY_DEFAULT_DAYS, INTRADAY_MAX_DAYS,
    SCAN_TIMEFRAMES, SCAN_TIMEFRAME_LABELS, bars_from_df, get_scanner_data,
)
from structure import Bar

_POSITIVE_HEX = "#2ec4b6"
_NEGATIVE_HEX = "#e63946"
_KILAVUZ_COLOR = "#f72585"
_TREND_COLOR = "#ff9f1c"
_BICAK_COLOR = "#ffd60a"
_SIFIR_COLOR = "#4cc9f0"
_YESIL_COLOR = "#38b000"
_DIP_KESISIM_COLOR = "#9d4edd"


def render_bicak_kanali_test(target_list):
    st.header("🔪 Bıçak Kanalı Test Modülü")
    st.caption(
        "bicak_kanali.py'deki kılavuz/bıçak/sıfır çizgisi + türetilmiş oran yöntemini "
        "seçili piyasadaki tüm hisselere uygulayıp, yeşil çizginin (alım çizgisi) "
        "gerçek fiyatla kesiştiği hisseleri listeler. Üretimdeki bir alım/satım "
        "sinyaline bağlı değildir, sadece yöntemin görsel doğrulaması amaçlıdır."
    )

    with st.expander("ℹ️ Yöntem Nasıl Çalışıyor? (Kılavuz / Bıçak / Sıfır / Yeşil Çizgi)"):
        st.markdown(
            """
1. **Düşüş bacağı**: Pivotlar (tepe/dip) taranıp maximum-drawdown mantığıyla en büyük
   genlikli tepe→dip düşüşü bulunur. Bu tarama aşağıdaki **"Düşüş Bacağı Seçim
   Yöntemi"** seçeneğine göre ya en güncel N bar ile sınırlanır ya da tüm seride yapılır.
2. **Kılavuz çizgisi**: Bu bacak içindeki tepe pivotlarından fiyatça **en yüksek**
   olanı ("en tepe") ile kronolojik olarak **en son** oluşanı ("son tepe") seçilip
   bu iki noktadan geçen doğru çizilir - klasik direnç trend çizgisi mantığı.
3. **Bıçak çizgisi**: Aynı bacaktaki en düşük dip pivotundan ("en dip nokta"),
   kılavuz ile aynı eğimde geçen paralel doğru.
4. **Dip kesişim mumu**: Bıçak çizgisinin, en dip'ten ÖNCEKİ barlarda soldan sağa
   ilk kestiği YEŞİL mum.
5. **Sıfır nokta**: Dip kesişim mumundan geriye dönük son 60 bar içindeki dip
   pivotlarından fiyatça **en yükseği** ("en yüksek alım noktası") - kılavuz ile
   aynı eğimde bu noktadan geçen paralel doğru "sıfır çizgisi"ni oluşturur.
6. **Yeşil çizgi (alım çizgisi)**: Kılavuz-bıçak ve bıçak-sıfır çizgisi arasındaki
   oranların çarpımı (**türetilmiş oran**) kadar, kılavuzun kanal genişliği
   kadarının üstüne ötelenmiş paralel doğru. Bu çizginin fiyatla en son kesiştiği
   bar, "en yakın alım noktası" olarak işaretlenir.

Detaylı kod referansı için `bicak_kanali.py` modül docstring'ine bakılabilir.
"""
        )

    st.caption("Mum Periyodu")
    tf_cols = st.columns(len(SCAN_TIMEFRAMES))
    selected_timeframes = [
        tf_code for col, tf_code in zip(tf_cols, SCAN_TIMEFRAMES)
        if col.checkbox(SCAN_TIMEFRAME_LABELS[tf_code], value=(tf_code == "1Day"), key=f"bicak_tf_{tf_code}")
    ]

    days_col1, days_col2 = st.columns(2)
    intraday_days = days_col1.number_input(
        "15dk / 30dk / 1sa mumlar için geriye gidilecek gün sayısı",
        min_value=1, max_value=INTRADAY_MAX_DAYS, value=INTRADAY_DEFAULT_DAYS, step=1,
        key="bicak_intraday_days",
        help=f"Yahoo Finance gün-içi mumlarda en fazla {INTRADAY_MAX_DAYS} gün geriye gidebiliyor.",
    )
    daily_days = days_col2.number_input(
        "1 gün mumlar için geriye gidilecek gün sayısı",
        min_value=1, max_value=DAILY_MAX_DAYS, value=DAILY_DEFAULT_DAYS, step=1,
        key="bicak_daily_days",
        help=f"En fazla {DAILY_MAX_DAYS} gün (yaklaşık 2 yıl) geriye gidilebiliyor.",
    )

    leg_mode = st.radio(
        "Düşüş Bacağı Seçim Yöntemi",
        options=["Son N Bar (güncel düşüş)", "Tüm Seri (tarihteki en büyük düşüş)"],
        index=0, key="bicak_leg_mode", horizontal=True,
        help="Son N Bar: tarama en güncel N bar ile sınırlanır, en güncel düşüşü "
             "önceliklendirir. Tüm Seri: tüm bar serisinde en büyük genlikli "
             "tepe->dip düşüşü seçilir (eski/orijinal davranış).",
    )
    if leg_mode == "Son N Bar (güncel düşüş)":
        pencere = st.number_input(
            "Pencere (bar)", min_value=1, value=30, step=5, key="bicak_pencere",
            help="Düşüş bacağı taraması sadece en güncel N bar içindeki pivotlarla sınırlanır.",
        )
    else:
        pencere = None

    if st.button("🚀 Piyasayı Tara", type="primary", disabled=not selected_timeframes):
        with st.spinner("Hisseler taranıyor..."):
            signals = []
            for tf_code in selected_timeframes:
                tf_label = SCAN_TIMEFRAME_LABELS[tf_code]
                tf_days = daily_days if tf_code == "1Day" else intraday_days
                for t in target_list:
                    df_temp, _cup, _obo, _tobo = get_scanner_data(t, timeframe=tf_code, period_days=tf_days)
                    if df_temp is None or df_temp.empty:
                        continue
                    bars = bars_from_df(df_temp)
                    result = find_kilavuz(bars, pencere=pencere)
                    if result is None:
                        continue
                    kesisim = yesil_cizgi_kesisimi(bars, result)
                    if kesisim is None:
                        continue
                    signals.append({
                        "Hisse": t, "Mum Periyodu": tf_label, "_tf_code": tf_code,
                        "Kesişim Fiyatı": round(kesisim.price, 2),
                        "Güncel Muma Uzaklık (bar)": (len(bars) - 1) - kesisim.index,
                    })

            st.session_state.bicak_signals = signals
            st.session_state.bicak_show_chart = False

    if "bicak_signals" in st.session_state:
        signals = st.session_state.bicak_signals
        if not signals:
            st.warning("Tarama sonucunda yeşil çizginin (alım çizgisi) fiyatla kesiştiği hisse bulunamadı.")
        else:
            st.subheader(f"🎯 Bulunan Kesişimler ({len(signals)})")
            st.caption(
                "💡 Tablo, Güncel Muma Uzaklık'a göre sıralı geliyor (en yakın kesişim en üstte). "
                "Bir satıra tıklayarak o hissenin grafiğini aşağıda açabilirsiniz. Sütun başlıklarına "
                "tıklayarak tabloyu farklı sıralayabilirsiniz."
            )
            signals_df = pd.DataFrame(signals)
            signals_df = signals_df.sort_values("Güncel Muma Uzaklık (bar)", ascending=True).reset_index(drop=True)
            display_cols = ["Hisse", "Güncel Muma Uzaklık (bar)", "Kesişim Fiyatı", "Mum Periyodu"]
            table_event = st.dataframe(
                signals_df[display_cols], use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="single-cell", key="bicak_signals_table",
            )
            selected_cells = table_event.selection.cells if table_event and table_event.selection else []
            if selected_cells:
                row_idx, _col_name = selected_cells[0]
                picked = signals_df.iloc[row_idx]
                st.session_state.bicak_selected_ticker = picked["Hisse"]
                st.session_state.bicak_selected_tf = picked["_tf_code"]
                st.session_state.bicak_show_chart = True

    if st.session_state.get("bicak_show_chart") and st.session_state.get("bicak_selected_ticker"):
        active_t = st.session_state.bicak_selected_ticker
        active_tf = st.session_state.bicak_selected_tf
        st.write("---")
        st.markdown(f"### 📈 Bıçak Kanalı Grafiği: **{active_t}** ({SCAN_TIMEFRAME_LABELS.get(active_tf, active_tf)})")

        active_days = daily_days if active_tf == "1Day" else intraday_days
        with st.spinner(f"{active_t} verisi getiriliyor..."):
            df, _cup, _obo, _tobo = get_scanner_data(active_t, timeframe=active_tf, period_days=active_days)

        if df is None or df.empty:
            st.error(f"❌ {active_t} için geçerli piyasa verisi alınamadı.")
        else:
            bars = bars_from_df(df)
            result = find_kilavuz(bars, pencere=pencere)
            if result is None:
                st.info("ℹ️ Bu hisse için artık geçerli bir bıçak kanalı bulunamadı (veri güncellenmiş olabilir).")
            else:
                _render_chart(bars, active_t, active_tf, result)
                en_tepe, son_tepe = result.kilavuz_noktalari
                st.caption(
                    f"Seçilen düşüş: {result.leg_tepe.t[:10]} ({result.leg_tepe.price:.2f}) → "
                    f"{result.leg_dip.t[:10]} ({result.leg_dip.price:.2f}) · "
                    f"kılavuz noktaları: {en_tepe.t[:10]} ({en_tepe.price:.2f}) ve "
                    f"{son_tepe.t[:10]} ({son_tepe.price:.2f}) · "
                    f"en dip nokta: {result.en_dip.t[:10]} ({result.en_dip.price:.2f}) · "
                    f"sıfır nokta: {result.sifir_nokta.t[:10]} ({result.sifir_nokta.price:.2f})"
                )
                st.caption(
                    f"üst_oran (kılavuz-bıçak): {result.ust_oran:.4f} · "
                    f"alt_oran (bıçak-sıfır çizgisi): {result.alt_oran:.4f} · "
                    f"türetilmiş_oran: {result.turetilmis_oran:.4f}"
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

    if result.dip_kesisim_mumu is not None:
        fig.add_trace(go.Scatter(
            x=[result.dip_kesisim_mumu.index], y=[result.dip_kesisim_mumu.price],
            mode="markers+text", name="Dip Kesişim Mumu",
            text=["Dip Kesişim"], textposition="bottom center",
            marker=dict(symbol="diamond", size=13, color=_DIP_KESISIM_COLOR, line=dict(color="#000000", width=1.5)),
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

    fig.add_trace(go.Scatter(
        x=[result.sifir_nokta.index], y=[result.sifir_nokta.price],
        mode="markers+text", name="Sıfır Nokta",
        text=["Sıfır"], textposition="bottom center",
        marker=dict(symbol="diamond", size=13, color=_SIFIR_COLOR, line=dict(color="#000000", width=1.5)),
    ))

    sifir_slope, sifir_intercept = result.sifir_cizgisi
    fig.add_trace(go.Scatter(
        x=xs, y=[sifir_slope * x + sifir_intercept for x in xs], mode="lines",
        name="Sıfır Çizgisi", line=dict(color=_SIFIR_COLOR, width=2, dash="dot"),
    ))

    yesil_slope, yesil_intercept = result.yesil_cizgi
    fig.add_trace(go.Scatter(
        x=xs, y=[yesil_slope * x + yesil_intercept for x in xs], mode="lines",
        name="Yeşil Çizgi (Alım)", line=dict(color=_YESIL_COLOR, width=2.5, dash="dash"),
    ))

    # Yeşil çizgiyi birden fazla mum kesebilir - bunlardan en sonuncusu
    # (en güncele en yakın olan) en yakın alım fırsatı sayılır, o yüzden
    # ayrıca ve belirgin şekilde işaretlenir.
    kesisim = yesil_cizgi_kesisimi(bars, result)
    if kesisim is not None:
        fig.add_trace(go.Scatter(
            x=[kesisim.index], y=[kesisim.price],
            mode="markers+text", name="En Yakın Alım Noktası",
            text=["En Yakın Alım"], textposition="top center",
            marker=dict(symbol="star", size=20, color="#ffffff", line=dict(color=_YESIL_COLOR, width=2.5)),
        ))

    # Kılavuz/bıçak/sıfır çizgisi, sıfır nokta'nın en tepe'den uzaklığına
    # bağlı olarak grafiğin uçlarında çok ekstrapole olabilir (aynı eğim,
    # gerçek fiyat aralığından çok uzağa taşabilir) - eksen ölçeğini buna
    # göre değil, gerçek mum verisine göre sabitliyoruz. Yeşil çizgi
    # (asıl gösterilmek istenen "alım çizgisi") ekseninde en tepe ve son
    # bar seviyeleri de dahil edilir, ki kırpılıp görünmez olmasın.
    price_values = [b.h for b in bars] + [b.l for b in bars]
    price_values.append(yesil_slope * result.leg_tepe.index + yesil_intercept)
    price_values.append(yesil_slope * (n - 1) + yesil_intercept)
    y_min, y_max = min(price_values), max(price_values)
    y_pad = (y_max - y_min) * 0.08 or 1.0

    fig.update_layout(
        title=f"{ticker} - Kılavuz + Bıçak + Sıfır + Yeşil Çizgi ({SCAN_TIMEFRAME_LABELS.get(timeframe, timeframe)})",
        template="plotly_dark", height=650, xaxis_rangeslider_visible=False,
        xaxis_title="Bar # (üzerine gelince tarih görünür)",
        yaxis=dict(range=[y_min - y_pad, y_max + y_pad]),
    )
    st.plotly_chart(fig, use_container_width=True)
