"""Yapay Zeka Analiz Modülü - "3. Model Eğitimi" bölümü: kayıtlı veri setinin
eğitim verisiyle Factorized Self-Attention modelini eğitir, test sonuçlarını ve
sonraki günlerin tahminini gösterir (hesaplama: ai_model.py)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ai_dataset as ad
import ai_model as am
from theme import DAY, get_mode, get_plotly_template

TR_TZ = ZoneInfo("Europe/Istanbul")

# İki seri (gerçek / tahmin, eğitim / doğrulama) - doğrulanmış kategorik palet,
# açık ve koyu mod için ayrı adımlar.
SERIES = {DAY: ("#2a78d6", "#eb6834")}
SERIES_DARK = ("#3987e5", "#d95926")

LOOKBACK_CHOICES = (32, 48, 64, 96)


def _colors():
    return SERIES.get(get_mode(), SERIES_DARK)


def _fmt_time(iso_text):
    try:
        dt = datetime.strptime(iso_text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=ZoneInfo("UTC"))
    except (TypeError, ValueError):
        return iso_text or "-"
    return f"{dt.astimezone(TR_TZ):%d.%m.%Y %H:%M} TRT"


def _line_chart(x, series: dict, y_title: str, height=280):
    fig = go.Figure()
    for (name, y), color in zip(series.items(), _colors()):
        fig.add_trace(go.Scatter(x=x, y=y, name=name, mode="lines", line=dict(width=2, color=color),
                                 hovertemplate=f"{name}: %{{y:,.4g}}<extra></extra>"))
    fig.update_layout(template=get_plotly_template(), height=height, margin=dict(l=0, r=0, t=8, b=0),
                      hovermode="x unified", yaxis_title=y_title,
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    fig.update_xaxes(showgrid=False)
    return fig


def _render_data_check(prep: dict):
    r = prep["report"]
    c = st.columns(4)
    c[0].metric("Kanal", r["channels"], help="Takvim sütunları kanal değil, temporal embedding girdisi.")
    c[1].metric("Doldurulan boş hücre", r["nan_filled_total"], help="Zamana göre doğrusal interpolasyon.")
    c[2].metric("IQR ile baskılanan", r["clipped_total"],
                help=f"[Q1 - {r['iqr_k']:g}·IQR, Q3 + {r['iqr_k']:g}·IQR] dışındaki değerler sınıra çekildi; "
                     "sınırlar yalnızca eğitim döneminden.")
    c[3].metric("Eğitim / Test günü", f"{r['n_train']} / {r['n_test']}")
    st.caption(f"Eğitim: {r['train_range'][0]} → {r['train_range'][1]} · "
               + (f"Test: {r['test_range'][0]} → {r['test_range'][1]}" if r["test_range"] else "Test: -"))
    if r["dropped_all_nan"]:
        st.warning("Tamamen boş olduğu için çıkarılan kanal: " + ", ".join(r["dropped_all_nan"]))
    if r["nan_filled"] or r["clipped"]:
        with st.expander("Veri kontrolü ayrıntısı"):
            if r["nan_filled"]:
                st.markdown("**İnterpolasyonla doldurulan boş hücreler**")
                st.dataframe(pd.DataFrame(sorted(r["nan_filled"].items(), key=lambda kv: -kv[1]),
                                          columns=["Kanal", "Hücre"]), hide_index=True, use_container_width=True)
            if r["clipped"]:
                st.markdown("**IQR eşiğiyle baskılanan şok değerler**")
                st.dataframe(pd.DataFrame([{"Kanal": k, "Eğitim": v["train"], "Test": v["test"]}
                                           for k, v in sorted(r["clipped"].items(),
                                                              key=lambda kv: -(kv[1]["train"] + kv[1]["test"]))]),
                             hide_index=True, use_container_width=True)


def _render_run(run: dict):
    m = run["metrics"]
    cfg = run["config"]
    h1 = m["close"][0]
    gain = (1 - m["norm_mse"] / m["naive_norm_mse"]) * 100 if m["naive_norm_mse"] else None
    c = st.columns(4)
    c[0].metric("Test penceresi", m["test_windows"])
    c[1].metric("Normalize MSE (tüm kanallar)", f"{m['norm_mse']:.3f}",
                delta=f"{gain:+.1f}% naive'e göre" if gain is not None else None,
                help=f"Naive (son değer değişmez): {m['naive_norm_mse']:.3f}. Pozitif = model daha iyi.")
    c[2].metric("Kapanış MAE (1. gün)", f"{h1['mae']:.2f}",
                delta=f"{h1['mae'] - h1['naive_mae']:+.2f} naive'e göre", delta_color="inverse",
                help=f"Naive MAE: {h1['naive_mae']:.2f}. Negatif = model daha iyi.")
    c[3].metric("Yön isabeti (1. gün)", f"{h1['direction_acc']:.1f}%" if h1["direction_acc"] is not None else "-",
                help="Tahmin edilen değişimin yönü (son kapanışa göre) gerçekleşenle aynı mı. %50 yazı-tura.")
    st.caption(f"Eğitim: {_fmt_time(run['created_at'])} · pencere {cfg['lookback']} gün, ufuk {cfg['horizon']} gün, "
               f"d_model {cfg['d_model']}, {cfg['n_layers']} katman, blok {cfg['block_size']} · "
               f"en iyi epoch {run['best_epoch']} / {len(run['history'])} · {run['params']:,} parametre")

    st.markdown("**Kapanış - ufuk bazında test sonuçları**")
    st.dataframe(pd.DataFrame([{
        "Gün": h["horizon"], "MAE": h["mae"], "Naive MAE": h["naive_mae"],
        "MAPE %": h["mape"], "Naive MAPE %": h["naive_mape"], "Yön isabeti %": h["direction_acc"],
    } for h in m["close"]]).round(3), hide_index=True, use_container_width=True)

    tp = run["test_predictions"]
    st.markdown("**Test dönemi - kapanış ve 1 gün sonrası tahmini**")
    st.plotly_chart(_line_chart(pd.to_datetime(tp["date"]), {"Gerçek": tp["actual"], "Tahmin (1. gün)": tp["pred_h1"]},
                                "Kapanış $"), use_container_width=True, config={"displayModeBar": False},
                    key=f"ai_model_pred_{run['id']}")
    hist = pd.DataFrame(run["history"])
    st.markdown("**Kayıp (normalize MSE)**")
    st.plotly_chart(_line_chart(hist["epoch"], {"Eğitim": hist["train"], "Doğrulama": hist["val"]}, "MSE",
                                height=220), use_container_width=True, config={"displayModeBar": False},
                    key=f"ai_model_loss_{run['id']}")

    fc = run["forecast"]
    st.markdown(f"**Sonraki {len(fc['dates'])} işlem günü tahmini** (son kapanış {fc['last_date']}: "
                f"{fc['last_close']:,.2f})")
    st.dataframe(pd.DataFrame({"Tarih": fc["dates"], "Kapanış tahmini": fc["close"],
                               "Son kapanışa göre %": [(v / fc["last_close"] - 1) * 100 for v in fc["close"]]})
                 .round(2), hide_index=True, use_container_width=True)
    st.caption("Tarihler hafta içi günlerdir; borsa tatilleri dikkate alınmaz. Model yalnızca geçmiş veriden "
               "öğrenir - yatırım tavsiyesi değildir. Naive'i geçemeyen bir model kullanılmamalıdır.")


def render_model_section(ticker: str, df: pd.DataFrame):
    st.subheader("3. Model Eğitimi - Factorized Self-Attention")
    st.caption("Eğitim verisiyle çok değişkenli tahmin modeli: kanal bağımsız (her sütun ayrı bir seri, ortak "
               "ağırlıklar), RevIN (pencere başına ortalama / varyans normalize edilip tahminde geri çevrilir), "
               "temporal embedding (ay, ayın günü, haftanın günü, ay başı / sonu), d_model 128. Factorized "
               "self-attention: her gün önce kendi bloğundaki günlere, sonra diğer blokların aynı sıradaki "
               "günlerine bakar. Veri kronolojik bölünür: ilk %80 eğitim, son %20 test.")
    if not am.torch_available():
        st.warning(am.INSTALL_HINT)
        return

    train_df = ad.training_frame(df)
    o = st.columns(4)
    lookback = o[0].selectbox("Pencere (gün)", LOOKBACK_CHOICES, index=LOOKBACK_CHOICES.index(64),
                              key=f"ai_m_lb_{ticker}", help="Modelin baktığı geçmiş gün sayısı (blok boyu 8'in katı).")
    horizon = o[1].number_input("Ufuk (gün)", 1, 20, am.DEFAULTS["horizon"], key=f"ai_m_h_{ticker}")
    epochs = o[2].number_input("En fazla epoch", 5, 200, am.DEFAULTS["epochs"], key=f"ai_m_ep_{ticker}",
                               help=f"Doğrulama kaybı {am.DEFAULTS['patience']} epoch iyileşmezse erken durur.")
    iqr_k = o[3].number_input("IQR katsayısı (k)", 1.5, 10.0, am.DEFAULTS["iqr_k"], 0.5, key=f"ai_m_k_{ticker}",
                              help="Şok baskılama eşiği: Q1 - k·IQR ve Q3 + k·IQR.")

    st.markdown("**Veri kontrolü**")
    try:
        prep = am.prepare_data(train_df, am.DEFAULTS["train_ratio"], float(iqr_k))
    except ValueError as e:
        st.error(str(e))
        return
    _render_data_check(prep)

    if st.button("Modeli eğit", type="primary", key=f"ai_m_train_{ticker}",
                 help="Sunucu CPU'sunda birkaç dakika sürebilir; sayfayı kapatmayın."):
        bar = st.progress(0.0, text="Eğitim başlıyor...")

        def on_epoch(epoch, total, train_loss, val_loss):
            bar.progress(epoch / total, text=f"Epoch {epoch} / {total} - eğitim {train_loss:.4f}, "
                                              f"doğrulama {val_loss:.4f}")
        try:
            res = am.train(train_df, {"lookback": int(lookback), "horizon": int(horizon), "epochs": int(epochs),
                                      "iqr_k": float(iqr_k)}, progress=on_epoch)
        except Exception as e:
            bar.empty()
            st.error(f"Eğitim başarısız: {e}")
            return
        run_id = am.save_run(ticker, res)
        bar.empty()
        st.session_state[f"ai_m_run_{ticker}"] = run_id
        st.success(f"Model eğitildi ve kaydedildi (#{run_id}).")

    runs = am.list_runs(ticker)
    if not runs:
        st.info("Bu veri seti için henüz eğitilmiş model yok.")
        return
    ids = [r["id"] for r in runs]
    if st.session_state.get(f"ai_m_run_{ticker}") not in ids:
        st.session_state[f"ai_m_run_{ticker}"] = ids[0]
    run_id = st.selectbox("Kayıtlı model", ids, key=f"ai_m_run_{ticker}",
                          format_func=lambda i: next(f"#{r['id']} · {_fmt_time(r['created_at'])} · pencere "
                                                     f"{r['config']['lookback']}, ufuk {r['config']['horizon']}"
                                                     for r in runs if r["id"] == i))
    run = next(r for r in runs if r["id"] == run_id)
    _render_run(run)
    if st.button("Bu modeli sil", key=f"ai_m_del_{ticker}_{run_id}"):
        am.delete_run(run_id)
        st.session_state.pop(f"ai_m_run_{ticker}", None)
        st.rerun()
