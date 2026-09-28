"""📒 İşlem Günlüğü sayfası - [2026-09-28 · Öneri 6].

Üç sekme:
  - 📒 İşlem Günlüğü: Alpaca emir geçmişinden kapanmış işlemler, R çarpanı,
    çıkış sebebi, seans dilimi ve kural sürümüne göre özet (trade_journal.py).
  - 📊 İşlem Günlüğü Analizi: kapanmış işlemler + Alpaca canlı pozisyonları
    birleşik; hangi hisse hangi algoritma ile ne kadar kazandırdı/kaybettirdi
    (trade_journal_analysis.py).
  - 📝 Değişiklik Günlüğü: 2026-09-28 emir analizi ve ondan çıkan
    değişikliklerin gerekçeleri, kod yerleri, ayarları ve takip ölçütleri
    (changelog.py).
"""

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from alpaca_client import AlpacaClient
from alpaca_dashboard import TR_TZ
from changelog import ANALYSIS_SUMMARY, CHANGES, VERIFICATION_NOTES
from github_config import read_portfolio_config
from rules_version import MIN_TRADES_FOR_EVALUATION
from theme import get_palette, get_plotly_template
from trade_journal import SESSION_EXTENDED, SESSION_OPENING, SESSION_REGULAR, build_round_trips, summarize, walk_fills
from trade_journal_analysis import (
    STATUS_CLOSED, STATUS_OPEN, closed_records, cumulative_realized, filter_records, group_summary,
    open_r_multiple, open_records, pnl_matrix,
)
from ui_style import freshness_caption, zebra_style

GITHUB_REPO = "berkakar/yatirim"
JOURNAL_DAYS_OPTIONS = [30, 60, 90, 180]
# Açık pozisyonun giriş emri seçili pencerede yoksa o sembolün geçmişi bu
# kadar geriye sorgulanır (sadece eksik semboller için, önbellekli).
OPEN_ENTRY_LOOKBACK_DAYS = 1095
HEATMAP_MAX_SYMBOLS = 25
CUMULATIVE_MAX_SERIES = 8


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_orders(key_id: str, secret_key: str, days: int) -> list[dict]:
    return AlpacaClient(key_id, secret_key).get_recent_orders(days=days, limit=500, nested=True)


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_positions(key_id: str, secret_key: str) -> list[dict]:
    return AlpacaClient(key_id, secret_key).get_all_positions()


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_symbol_orders(key_id: str, secret_key: str, symbol: str) -> list[dict]:
    return AlpacaClient(key_id, secret_key).get_recent_orders(
        days=OPEN_ENTRY_LOOKBACK_DAYS, limit=500, nested=True, symbols=symbol)


def _credentials(username: str) -> tuple[str | None, str | None]:
    user_alpaca = st.secrets.get("alpaca", {}).get(username, {})
    key_id, secret_key = user_alpaca.get("key_id"), user_alpaca.get("secret_key")
    if not key_id or not secret_key:
        st.warning(f"'{username}' için Alpaca hesabı tanımlı değil (`.streamlit/secrets.toml` içinde `[alpaca.{username}]`).")
    return key_id, secret_key


def _rules_since(username: str) -> tuple[datetime | None, str | None]:
    token = st.secrets.get("GITHUB_TOKEN")
    config = {}
    if token:
        try:
            config = read_portfolio_config(GITHUB_REPO, token, username)
        except Exception:
            config = {}
    raw = config.get("rules_version_since")
    return (datetime.fromisoformat(raw) if raw else None), raw


def _trips_dataframe(trips) -> pd.DataFrame:
    rows = []
    for t in reversed(trips):
        r = t.r_multiple
        rows.append({
            "Hisse": t.symbol,
            "Giriş (TRT)": t.entry_time.astimezone(TR_TZ).strftime("%d.%m %H:%M"),
            "Çıkış (TRT)": t.exit_time.astimezone(TR_TZ).strftime("%d.%m %H:%M"),
            "Süre (saat)": round(t.holding_hours, 1),
            "Adet": t.qty,
            "Giriş $": round(t.entry_price, 2),
            "Çıkış $": round(t.exit_price, 2),
            "İlk Stop $": round(t.initial_stop, 2) if t.initial_stop is not None else None,
            "K/Z $": round(t.pnl, 2),
            "K/Z %": round(t.pnl_pct, 2),
            "R": round(r, 2) if r is not None else None,
            "Giriş Kaynağı": t.entry_source,
            "Çıkış Sebebi": t.exit_reason,
            "Giriş Dilimi": t.entry_session,
            "Çıkış Dilimi": t.exit_session,
        })
    return pd.DataFrame(rows)


def _count_table(counts: dict, label: str) -> pd.DataFrame:
    total = sum(counts.values()) or 1
    return pd.DataFrame(
        [{label: k, "İşlem": v, "Pay %": round(v / total * 100, 1)} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
    )


def _render_journal(username: str):
    key_id, secret_key = _credentials(username)
    if not key_id or not secret_key:
        return
    rules_since, rules_since_raw = _rules_since(username)

    c1, c2 = st.columns([1, 2])
    days = c1.selectbox("Geriye dönük gün", JOURNAL_DAYS_OPTIONS, index=2, key="tj_days")
    scope_options = ["all"] + (["rules"] if rules_since else [])
    scope = c2.radio(
        "Kapsam", scope_options, horizontal=True, key="tj_scope",
        format_func=lambda v: "Tüm işlemler" if v == "all" else f"Mevcut kural sürümü ({rules_since_raw[:10]} sonrası)",
    )

    try:
        orders = _fetch_orders(key_id, secret_key, int(days))
    except Exception as e:
        st.error(f"Alpaca emir geçmişi alınamadı: {e}")
        return
    trips = build_round_trips(orders)
    rules_trips = [t for t in trips if rules_since and t.entry_time >= rules_since]
    shown = rules_trips if scope == "rules" else trips

    if rules_since:
        n_rules = len(rules_trips)
        if n_rules < MIN_TRADES_FOR_EVALUATION:
            st.warning(
                f"🧊 Mevcut kural sürümü ({rules_since_raw[:10]}) ile **{n_rules}** işlem kapandı. Sonuçlar "
                f"{MIN_TRADES_FOR_EVALUATION} işlem birikmeden istatistiksel olarak anlamlı değil - bu süre "
                "zarfında algoritma/stop/risk ayarlarını değiştirmemeniz önerilir."
            )
        else:
            st.success(f"🧊 Mevcut kural sürümü ile {n_rules} işlem kapandı - kurallar değerlendirilebilir.")

    freshness_caption(f"Veri güncelliği: {datetime.now(TR_TZ):%d.%m.%Y %H:%M:%S} TRT (Alpaca emir geçmişi, 5 dk önbellek).")
    if not shown:
        st.info("Bu kapsamda kapanmış işlem yok.")
        return

    s = summarize(shown)
    m = st.columns(6)
    m[0].metric("İşlem", s["trades"])
    m[1].metric("İsabet", f"%{s['win_rate']:.0f}")
    m[2].metric("Net K/Z", f"{s['total_pnl']:,.2f}$")
    m[3].metric("Ort. kazanç / kayıp", f"{s['avg_win']:,.0f}$ / {s['avg_loss']:,.0f}$")
    m[4].metric("Toplam R", f"{s['total_r']:+.2f}R" if s["r_count"] else "—")
    m[5].metric("Beklenen değer", f"{s['expectancy_r']:+.2f}R" if s["expectancy_r"] is not None else "—",
                help="İşlem başına ortalama R. Pozitifse sistem uzun vadede kazandırır. İlk stopu bilinen "
                     f"{s['r_count']} işlem üzerinden.")

    st.dataframe(zebra_style(_trips_dataframe(shown)), use_container_width=True, hide_index=True)

    t1, t2 = st.columns(2)
    with t1:
        st.markdown("**Çıkış sebebi**")
        st.dataframe(_count_table(s["exits_by_reason"], "Sebep"), use_container_width=True, hide_index=True)
    with t2:
        st.markdown("**Çıkış seans dilimi**")
        st.dataframe(_count_table(s["exits_by_session"], "Dilim"), use_container_width=True, hide_index=True)
        entry_sessions: dict[str, int] = {}
        for t in shown:
            entry_sessions[t.entry_session] = entry_sessions.get(t.entry_session, 0) + 1
        st.markdown("**Giriş seans dilimi**")
        st.dataframe(_count_table(entry_sessions, "Dilim"), use_container_width=True, hide_index=True)

    st.caption(
        f"'{SESSION_OPENING}': 09:30-09:45 ET · '{SESSION_REGULAR}': 09:45-16:00 ET · '{SESSION_EXTENDED}': "
        "pre-market / after-hours. 28.09.2026 öncesi stop emirleri etiketsiz olduğu için sebepleri "
        "'Stop (etiketsiz)' görünür. R, girişten sonra kurulan ilk stopa göre hesaplanır."
    )


# ------------------------------------------------------------------------------
# 📊 İşlem Günlüğü Analizi
# ------------------------------------------------------------------------------

def _fmt_money(v: float | None) -> str:
    return "—" if v is None else f"{v:+,.2f}$"


def _fmt_pf(v: float | None) -> str:
    if v is None:
        return "—"
    return "∞" if v == float("inf") else f"{v:.2f}"


def _pnl_color_style(cols: list[str]):
    """K/Z sütunlarında pozitif/negatif değerleri tema renkleriyle boyar."""
    p = get_palette()

    def fn(frame: pd.DataFrame) -> pd.DataFrame:
        styles = pd.DataFrame("", index=frame.index, columns=frame.columns)
        for c in cols:
            if c not in frame.columns:
                continue
            for i, v in frame[c].items():
                if isinstance(v, (int, float)) and v != 0:
                    styles.at[i, c] = f"color: {p['positive'] if v > 0 else p['negative']}; font-weight: 600"
        return styles
    return fn


def _summary_dataframe(rows: list[dict], label: str, capital: float | None) -> pd.DataFrame:
    out = []
    for r in rows:
        row = {
            label: r["key"],
            "Kapalı": r["closed"],
            "Açık": r["open"],
            "İsabet %": round(r["win_rate"], 0) if r["win_rate"] is not None else None,
            "Gerçekleşen $": round(r["realized"], 2),
            "Açık K/Z $": round(r["unrealized"], 2),
            "Toplam $": round(r["total"], 2),
            "Getiri %": round(r["return_pct"], 2),
            "Ort. R": round(r["avg_r"], 2) if r["avg_r"] is not None else None,
            "Toplam R": round(r["total_r"], 2) if r["total_r"] is not None else None,
            "Profit Factor": _fmt_pf(r["profit_factor"]),
            "En iyi": r["best"],
            "En kötü": r["worst"],
        }
        if capital:
            row["Sermaye %"] = round(r["total"] / capital * 100, 2)
        out.append(row)
    return pd.DataFrame(out)


def _records_dataframe(records, split_timeframe: bool) -> pd.DataFrame:
    rows = []
    for r in sorted(records, key=lambda r: (r.exit_time or r.entry_time or datetime.min.replace(tzinfo=TR_TZ)), reverse=True):
        r_mult = r.r_multiple if r.status == STATUS_CLOSED else open_r_multiple(r)
        rows.append({
            "Hisse": r.symbol,
            "Algoritma": r.algo_label(split_timeframe),
            "Durum": r.status,
            "Giriş (TRT)": r.entry_time.astimezone(TR_TZ).strftime("%d.%m.%y %H:%M") if r.entry_time else "—",
            "Çıkış (TRT)": r.exit_time.astimezone(TR_TZ).strftime("%d.%m.%y %H:%M") if r.exit_time else "—",
            "Adet": r.qty,
            "Giriş $": round(r.entry_price, 2),
            "Son/Çıkış $": round(r.last_price, 2),
            "İlk Stop $": round(r.initial_stop, 2) if r.initial_stop is not None else None,
            "Gerçekleşen $": round(r.realized, 2),
            "Açık K/Z $": round(r.unrealized, 2),
            "Toplam $": round(r.total, 2),
            "K/Z %": round(r.pnl_pct, 2),
            "R": round(r_mult, 2) if r_mult is not None else None,
        })
    return pd.DataFrame(rows)


def _algo_bar_chart(rows: list[dict]):
    p = get_palette()
    rows = list(reversed(rows))  # yatay çubukta en kârlı en üstte
    fig = go.Figure(go.Bar(
        x=[r["total"] for r in rows], y=[r["key"] for r in rows], orientation="h",
        marker_color=[p["positive"] if r["total"] >= 0 else p["negative"] for r in rows],
        customdata=[[r["realized"], r["unrealized"], r["closed"], r["open"]] for r in rows],
        hovertemplate="<b>%{y}</b><br>Toplam: %{x:+,.2f}$<br>Gerçekleşen: %{customdata[0]:+,.2f}$"
                      "<br>Açık K/Z: %{customdata[1]:+,.2f}$<br>Kapalı/Açık: %{customdata[2]} / %{customdata[3]}<extra></extra>",
    ))
    fig.update_layout(template=get_plotly_template(), height=max(220, 36 * len(rows) + 80),
                      margin=dict(l=10, r=10, t=10, b=30), xaxis_title="Toplam K/Z ($)", showlegend=False)
    fig.add_vline(x=0, line_width=1, line_color=p["text_muted"])
    return fig


def _heatmap(matrix: dict) -> go.Figure | None:
    if not matrix:
        return None
    p = get_palette()
    sym_totals: dict[str, float] = {}
    for (sym, _), v in matrix.items():
        sym_totals[sym] = sym_totals.get(sym, 0.0) + v
    symbols = sorted(sym_totals, key=lambda s: -abs(sym_totals[s]))[:HEATMAP_MAX_SYMBOLS]
    symbols.sort(key=lambda s: -sym_totals[s])
    algos = sorted({a for (_, a) in matrix}, key=lambda a: -sum(v for (s, x), v in matrix.items() if x == a))
    z = [[matrix.get((s, a)) for a in algos] for s in symbols]
    text = [[f"{v:+,.0f}" if v is not None else "" for v in row] for row in z]
    vmax = max((abs(v) for row in z for v in row if v is not None), default=1.0) or 1.0
    fig = go.Figure(go.Heatmap(
        z=z, x=algos, y=symbols, text=text, texttemplate="%{text}", zmid=0, zmin=-vmax, zmax=vmax,
        colorscale=[[0, p["negative"]], [0.5, p["bg_subtle"]], [1, p["positive"]]],
        hovertemplate="<b>%{y}</b> · %{x}<br>Toplam K/Z: %{z:+,.2f}$<extra></extra>", xgap=2, ygap=2,
        colorbar=dict(title="$"),
    ))
    fig.update_layout(template=get_plotly_template(), height=max(260, 26 * len(symbols) + 120),
                      margin=dict(l=10, r=10, t=10, b=10), yaxis=dict(autorange="reversed"), xaxis=dict(side="top"))
    return fig


def _cumulative_chart(points) -> go.Figure | None:
    if not points:
        return None
    finals: dict[str, float] = {}
    for _, label, v in points:
        finals[label] = v
    # Seri sayısı sınırlı: en büyük mutlak sonuca sahip algoritmalar çizilir,
    # renk algoritmanın adına göre sabit sırayla atanır (filtreyle değişmesin).
    keep = sorted(finals, key=lambda k: -abs(finals[k]))[:CUMULATIVE_MAX_SERIES]
    fig = go.Figure()
    for label in sorted(keep):
        xs = [t.astimezone(TR_TZ) for t, lab, _ in points if lab == label]
        ys = [v for _, lab, v in points if lab == label]
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", name=label, line=dict(width=2, shape="hv"),
                                 marker=dict(size=6),
                                 hovertemplate=f"<b>{label}</b><br>%{{x|%d.%m.%y %H:%M}}<br>Birikimli: %{{y:+,.2f}}$<extra></extra>"))
    fig.add_hline(y=0, line_width=1, line_color=get_palette()["text_muted"])
    fig.update_layout(template=get_plotly_template(), height=360, margin=dict(l=10, r=10, t=10, b=10),
                      yaxis_title="Birikimli gerçekleşen K/Z ($)", hovermode="closest",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    return fig


def _initial_capital(username: str) -> float | None:
    try:
        from config import load_initial_capital
        v = load_initial_capital(username)
        return float(v) if v else None
    except Exception:
        return None


def _render_analysis(username: str):
    key_id, secret_key = _credentials(username)
    if not key_id or not secret_key:
        return
    rules_since, rules_since_raw = _rules_since(username)

    c1, c2, c3 = st.columns([1, 2, 2])
    days = c1.selectbox("Geriye dönük gün", JOURNAL_DAYS_OPTIONS, index=2, key="tja_days",
                        help="Kapanmış işlemler için pencere. Açık pozisyonlar her zaman dahildir.")
    scope_options = ["all"] + (["rules"] if rules_since else [])
    scope = c2.radio(
        "Kapsam", scope_options, horizontal=True, key="tja_scope",
        format_func=lambda v: "Tüm işlemler" if v == "all" else f"Mevcut kural sürümü ({rules_since_raw[:10]} sonrası)",
    )
    status_opt = c3.radio("Durum", ["Hepsi", STATUS_OPEN, STATUS_CLOSED], horizontal=True, key="tja_status")
    c4, c5 = st.columns(2)
    split_tf = c4.toggle("Periyodu ayrı göster (ör. 1Day / 1Hour)", value=False, key="tja_split")
    include_manual = c5.toggle("Elle / bilinmeyen işlemleri dahil et", value=True, key="tja_manual")

    try:
        orders = _fetch_orders(key_id, secret_key, int(days))
        positions = _fetch_positions(key_id, secret_key)
    except Exception as e:
        st.error(f"Alpaca verisi alınamadı: {e}")
        return

    trips, lots = walk_fills(orders)
    missing = [p["symbol"] for p in positions if p.get("symbol") not in lots]
    for sym in missing:
        try:
            _, sym_lots = walk_fills(_fetch_symbol_orders(key_id, secret_key, sym))
        except Exception:
            continue
        if sym in sym_lots:
            lots[sym] = sym_lots[sym]

    records = closed_records(trips) + open_records(positions, lots)
    records = filter_records(
        records, since=rules_since if scope == "rules" else None, include_manual=include_manual,
        status=None if status_opt == "Hepsi" else status_opt,
    )

    freshness_caption(
        f"Veri güncelliği: {datetime.now(TR_TZ):%d.%m.%Y %H:%M:%S} TRT (pozisyonlar 1 dk, emir geçmişi 5 dk önbellek).")
    if not records:
        st.info("Bu filtrelerle gösterilecek işlem ya da pozisyon yok.")
        return

    capital = _initial_capital(username)
    realized = sum(r.realized for r in records)
    unrealized = sum(r.unrealized for r in records)
    n_closed = sum(1 for r in records if r.status == STATUS_CLOSED)
    n_open = len(records) - n_closed
    m = st.columns(5)
    m[0].metric("Gerçekleşen K/Z", _fmt_money(realized), help="Kapanmış işlemler + açık pozisyonlardaki kısmi satışlar.")
    m[1].metric("Açık K/Z", _fmt_money(unrealized), help="Alpaca canlı pozisyonlarının gerçekleşmemiş K/Z'si.")
    m[2].metric("Toplam", _fmt_money(realized + unrealized))
    m[3].metric("Sermayeye göre", f"%{(realized + unrealized) / capital * 100:+.2f}" if capital else "—",
                help=f"İlk sermaye: {capital:,.0f}$" if capital else "İlk sermaye tanımlı değil (Genel Bakış).")
    m[4].metric("Kapalı / Açık", f"{n_closed} / {n_open}")

    algo_rows = group_summary(records, lambda r: r.algo_label(split_tf))
    pnl_cols = ["Gerçekleşen $", "Açık K/Z $", "Toplam $", "Getiri %", "Sermaye %"]

    st.markdown("#### 🧠 Algoritma bazında")
    st.plotly_chart(_algo_bar_chart(algo_rows), use_container_width=True)
    st.dataframe(zebra_style(_summary_dataframe(algo_rows, "Algoritma", capital), _pnl_color_style(pnl_cols)),
                 use_container_width=True, hide_index=True)

    st.markdown("#### 🗺️ Hisse × Algoritma")
    fig = _heatmap(pnl_matrix(records, split_tf))
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True)
        st.caption(f"Toplam K/Z (gerçekleşen + açık). Mutlak sonucu en büyük {HEATMAP_MAX_SYMBOLS} hisse gösterilir.")
    symbol_rows = group_summary(records, lambda r: r.symbol)
    with st.expander(f"📋 Hisse bazında özet ({len(symbol_rows)} hisse)"):
        st.dataframe(zebra_style(_summary_dataframe(symbol_rows, "Hisse", capital), _pnl_color_style(pnl_cols)),
                     use_container_width=True, hide_index=True)

    st.markdown("#### 📈 Birikimli gerçekleşen K/Z")
    cfig = _cumulative_chart(cumulative_realized(records, split_tf))
    if cfig is not None:
        st.plotly_chart(cfig, use_container_width=True)
        st.caption(f"Kapanmış işlemlerin çıkış anına göre. En çok etki eden {CUMULATIVE_MAX_SERIES} algoritma çizilir.")
    else:
        st.caption("Bu filtrelerle kapanmış işlem yok.")

    st.markdown("#### 🔍 Hisse detayı")
    symbols = [row["key"] for row in symbol_rows]
    pick = st.selectbox("Hisse", ["Tümü"] + symbols, key="tja_symbol")
    detail = records if pick == "Tümü" else [r for r in records if r.symbol == pick]
    st.dataframe(zebra_style(_records_dataframe(detail, split_tf), _pnl_color_style(pnl_cols + ["K/Z %"])),
                 use_container_width=True, hide_index=True)
    st.caption(
        "Algoritma, pozisyonu 0'dan açan alım emrinin etiketinden okunur (algo-/rebuy-/orb-/rs-/hai-). Açık pozisyonun "
        f"giriş emri seçili pencerede yoksa sembolün son {OPEN_ENTRY_LOOKBACK_DAYS} günlük geçmişi taranır; yine "
        "bulunamazsa 'Bilinmiyor' görünür. Açık pozisyonlardaki R anlık fiyata göredir ve Ort. R'ye katılmaz."
    )


def _render_changelog():
    st.markdown(f"#### 🔎 {ANALYSIS_SUMMARY['title']} · {ANALYSIS_SUMMARY['date']}")
    st.markdown(ANALYSIS_SUMMARY["body"])
    st.caption("Kodda ilgili yerler '[2026-09-28 · Öneri N]' yorumuyla işaretli.")
    for change in CHANGES:
        with st.expander(f"Öneri {change['id']} · {change['title']} ({change['date']})"):
            st.markdown(f"**Sorun:** {change['problem']}")
            st.markdown(f"**Değişiklik:**\n\n{change['change']}")
            st.markdown(f"**Kod:** `{change['where']}`")
            st.markdown(f"**Ayar:** {change['settings']}")
            st.markdown(f"**Nasıl takip edilir:** {change['track']}")
    if VERIFICATION_NOTES:
        with st.expander("🧪 Test ve doğrulama sonuçları"):
            st.markdown(VERIFICATION_NOTES)


def render_trade_journal(username: str):
    tab_journal, tab_analysis, tab_changes = st.tabs(
        ["📒 İşlem Günlüğü", "📊 İşlem Günlüğü Analizi", "📝 Değişiklik Günlüğü"])
    with tab_journal:
        _render_journal(username)
    with tab_analysis:
        _render_analysis(username)
    with tab_changes:
        _render_changelog()
