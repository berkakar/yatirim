"""🧠 Algo Analiz sayfası (eski adıyla 📒 İşlem Günlüğü).

İki sekme:
  - 🧠 Algo Analiz, yukarıdan aşağıya:
      1. Portföyün son durumu: ilk giriş (yatırılan sermaye), güncel değer, K/Z
         ve altında algoritmalara ayrılan nakit (pay, harcanan, kalan, K/Z)
      2. Karlılık: açık pozisyonlar şimdi satılırsa / stoplar devreye girerse
         ve kapanan pozisyonlardan gerçekleşen K/Z
      3. Algoritma ve birlikte kullanılan stop loss algoritmasının karlılığı
      4. Hisse hareketleri tablosu (kapalı işlemler + açık pozisyonlar)
      5. Çıkış sebebi, çıkış seans dilimi, giriş seans dilimi istatistikleri
    Hesaplar: algo_analiz.py, trade_journal.py, trade_journal_analysis.py.
  - 📝 Değişiklik Günlüğü: sistemde yapılan değişikliklerin gerekçeleri, kod
    yerleri, ayarları ve takip ölçütleri (changelog.py).
"""

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import storage
from algo_analiz import (
    OTHER_ALGOS_LABEL, apply_stop_scenario, count_by, format_stop_moves, module_cash_rows, open_stop_levels, portfolio_snapshot,
    resolve_stop_algorithm_id, scenario_totals,
)
from alpaca_account_ui import get_user_alpaca, missing_keys_warning
from alpaca_client import AlpacaClient
from alpaca_dashboard import TR_TZ
from changelog import ANALYSIS_SUMMARY, CHANGES, VERIFICATION_NOTES
from github_config import DEFAULT_CONFIG, read_json_from_github
from rules_version import MIN_TRADES_FOR_EVALUATION
from stop_algorithms import STOP_ALGORITHMS
from theme import get_palette, get_plotly_template
from trade_journal import MODULE_LABELS, SESSION_EXTENDED, SESSION_OPENING, SESSION_REGULAR, walk_fills
from trade_journal_analysis import (
    STATUS_CLOSED, STATUS_OPEN, closed_records, filter_records, group_summary, open_r_multiple, open_records,
)
from ui_style import freshness_caption, zebra_style

PAGE_TITLE = "🧠 Algo Analiz"
GITHUB_REPO = "berkakar/yatirim"
JOURNAL_DAYS_OPTIONS = [30, 60, 90, 180, 365, 1095]
# Açık pozisyonun giriş emri seçili pencerede yoksa o sembolün geçmişi bu
# kadar geriye sorgulanır (sadece eksik semboller için, önbellekli).
OPEN_ENTRY_LOOKBACK_DAYS = 1095

GROUP_ALGO_STOP = "Algoritma + Stop Loss"
GROUP_ALGO = "Sadece Algoritma"
GROUP_STOP = "Sadece Stop Loss"


# ------------------------------------------------------------------------------
# Veri
# ------------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def _fetch_orders(key_id: str, secret_key: str, trading_url: str, days: int) -> list[dict]:
    return AlpacaClient(key_id, secret_key, trading_url).get_recent_orders(days=days, limit=500, nested=True)


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_positions(key_id: str, secret_key: str, trading_url: str) -> list[dict]:
    return AlpacaClient(key_id, secret_key, trading_url).get_all_positions()


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_account(key_id: str, secret_key: str, trading_url: str) -> dict:
    return AlpacaClient(key_id, secret_key, trading_url).get_account()


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_open_orders(key_id: str, secret_key: str, trading_url: str) -> list[dict]:
    return AlpacaClient(key_id, secret_key, trading_url).get_open_orders()


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_symbol_orders(key_id: str, secret_key: str, trading_url: str, symbol: str) -> list[dict]:
    return AlpacaClient(key_id, secret_key, trading_url).get_recent_orders(
        days=OPEN_ENTRY_LOOKBACK_DAYS, limit=500, nested=True, symbols=symbol)


def _credentials(username: str) -> tuple[str | None, str | None, str]:
    key_id, secret_key, trading_url = get_user_alpaca(username)
    if not key_id or not secret_key:
        missing_keys_warning(username)
    return key_id, secret_key, trading_url


def _read_setting(path: str, default):
    """SQLite / GitHub / repo içindeki JSON - hangisi kullanılıyorsa."""
    try:
        token = st.secrets.get("GITHUB_TOKEN")
        if token or storage.db_key(path) is not None:
            return read_json_from_github(GITHUB_REPO, token, path, default)
        return storage.load_json(path, default)
    except Exception:
        return default


@st.cache_data(ttl=60, show_spinner=False)
def _stop_configs(username: str) -> tuple[dict, dict[str, str], dict[str, set]]:
    """PBP portföy ayarı, modül etiketi -> stop algoritması ve modül etiketi
    -> modülün tuttuğu semboller (bkz. algo_analiz.resolve_stop_algorithm_id)."""
    # Fonksiyon içi import: bkz. stop_loss_settings._live_stop_usage notu.
    import heikin_ashi_intraday_core
    import orb_core
    import relative_strength_core

    pbp = _read_setting(f"portfolio_config_{username}.json", DEFAULT_CONFIG) or {}
    module_algos: dict[str, str] = {}
    module_holdings: dict[str, set] = {}
    # Sıra canlı botla aynı: RS, ORB, Heikin Ashi.
    for prefix, core in (("rs", relative_strength_core), ("orb", orb_core), ("hai", heikin_ashi_intraday_core)):
        label = MODULE_LABELS[prefix]
        module_algos[label] = core.resolve_stop_algorithm(_read_setting(core.config_path(username), {}) or {})
        module_holdings[label] = set((_read_setting(core.holdings_path(username), {}) or {}).keys())
    return pbp, module_algos, module_holdings


@st.cache_data(ttl=60, show_spinner=False)
def _module_allocations(username: str) -> list[tuple[str, float, set]]:
    """(modül etiketi, nakit payı % - devre dışıysa 0, tuttuğu semboller) -
    canlı botların get_cash_allocation_pct'iyle aynı kural."""
    import heikin_ashi_intraday_core
    import orb_core
    import relative_strength_core

    out = []
    for prefix, core in (("rs", relative_strength_core), ("orb", orb_core), ("hai", heikin_ashi_intraday_core)):
        cfg = _read_setting(core.config_path(username), {}) or {}
        pct = float(cfg.get("cash_allocation_pct") or 0.0) if cfg.get("enabled") else 0.0
        symbols = set((_read_setting(core.holdings_path(username), {}) or {}).keys())
        out.append((MODULE_LABELS[prefix], pct, symbols))
    return out


def _rules_since(username: str) -> tuple[datetime | None, str | None]:
    raw = (_read_setting(f"portfolio_config_{username}.json", {}) or {}).get("rules_version_since")
    return (datetime.fromisoformat(raw) if raw else None), raw


def _initial_capital(username: str) -> float | None:
    try:
        from config import load_initial_capital
        v = load_initial_capital(username)
        return float(v) if v else None
    except Exception:
        return None


def _stop_label(algo_id: str | None) -> str:
    if not algo_id:
        return "—"
    algo = STOP_ALGORITHMS.get(algo_id)
    return algo.label if algo else algo_id


# ------------------------------------------------------------------------------
# Biçim
# ------------------------------------------------------------------------------

def _fmt_money(v: float | None, sign: bool = True) -> str:
    if v is None:
        return "—"
    return f"{v:+,.2f}$" if sign else f"{v:,.2f}$"


def _fmt_pct(v: float | None) -> str:
    # İşaret başta: st.metric delta'nın rengini/okunu baştaki işaretten seçer.
    return "—" if v is None else f"{v:+.2f}%"


def _md(text: str) -> str:
    """Markdown'da iki '$' arası LaTeX sayılır - dolar işaretleri kaçışlanır."""
    return text.replace("$", "\\$")


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
                if isinstance(v, (int, float)) and not pd.isna(v) and v != 0:
                    styles.at[i, c] = f"color: {p['positive'] if v > 0 else p['negative']}; font-weight: 600"
        return styles
    return fn


def _table(df: pd.DataFrame, pnl_cols: list[str] | None = None, column_config: dict | None = None):
    style = _pnl_color_style(pnl_cols) if pnl_cols else None
    styler = zebra_style(df, style).format(precision=2, thousands=",", na_rep="—")
    int_like = [c for c in ("Adet", "İşlem", "Kapalı", "Açık", "Stop Güncelleme") if c in df.columns]
    if int_like:
        styler = styler.format("{:g}", subset=int_like, na_rep="—")
    if "Pay %" in df.columns:
        styler = styler.format("{:.1f}", subset=["Pay %"])
    if "İsabet %" in df.columns:
        styler = styler.format("{:.0f}", subset=["İsabet %"], na_rep="—")
    st.dataframe(styler, use_container_width=True, hide_index=True, column_config=column_config)


# ------------------------------------------------------------------------------
# 1. Portföyün son durumu
# ------------------------------------------------------------------------------

def _render_portfolio(snap: dict, n_positions: int):
    st.markdown("### 💼 Portföyün Son Durumu")
    m = st.columns(4)
    m[0].metric("İlk Giriş (Yatırılan Sermaye)", _fmt_money(snap["initial_capital"], sign=False),
                help=None if snap["initial_capital"] else "İlk sermaye tanımlı değil - Giriş Sayfası > Alpaca hesap "
                                                          "özetinden kaydedebilirsiniz.")
    m[1].metric("Güncel Portföy Değeri", _fmt_money(snap["equity"], sign=False),
                delta=f"{snap['day_pl']:+,.2f}$ bugün" if snap["day_pl"] is not None else None,
                help="Nakit + tüm pozisyonların güncel piyasa değeri (Alpaca equity).")
    m[2].metric("Toplam Kârlılık", _fmt_money(snap["pl"]),
                delta=_fmt_pct(snap["pl_pct"]) if snap["pl_pct"] is not None else None,
                help="Güncel portföy değeri − ilk giriş.")
    m[3].metric("Nakit / Pozisyon", _md(f"{snap['cash']:,.0f}$ / {snap['long_value']:,.0f}$"),
                help=f"{n_positions} açık pozisyon.")


def _render_module_cash(rows: list[dict]):
    st.markdown("#### 🧮 Algoritmalara Ayrılan Nakit")
    shown = [r for r in rows if r["label"] == OTHER_ALGOS_LABEL or r["pct"] > 0 or r["spent"] > 0 or r["open"] or r["closed"]]
    df = pd.DataFrame([{
        "Algoritma": r["label"],
        "Pay %": round(r["pct"], 2),
        "Bütçe $": round(r["budget"], 2),
        "Harcanan $": round(r["spent"], 2),
        "Kalan $": round(r["remaining"], 2),
        "Kullanım %": round(r["usage_pct"], 2) if r["usage_pct"] is not None else None,
        "Açık": r["open"],
        "Kapalı": r["closed"],
        "Açık K/Z $": round(r["unrealized"], 2),
        "Gerçekleşen $": round(r["realized"], 2),
        "Toplam K/Z $": round(r["total"], 2),
        "Getiri %": round(r["return_pct"], 2) if r["return_pct"] is not None else None,
    } for r in shown])
    _table(df, ["Açık K/Z $", "Gerçekleşen $", "Toplam K/Z $", "Getiri %"])
    st.caption(
        "Bütçe = hesap değeri × algoritmanın nakit payı. Harcanan = algoritmanın elindeki açık pozisyonların alış "
        "maliyeti; Kalan = Bütçe − Harcanan; Kullanım = Harcanan / Bütçe. Son satır modüllere ayrılmayan kısım: "
        "kalanı, nakitten modüllerin harcanmamış payları düşüldükten sonra Premium Buy Point'in kullanabileceği "
        "nakittir. Gerçekleşen K/Z seçili penceredeki kapalı işlemlerden; Getiri = Toplam K/Z / Bütçe."
    )


# ------------------------------------------------------------------------------
# 2. Karlılık
# ------------------------------------------------------------------------------

def _render_profitability(snap: dict, all_open: list, records: list):
    st.markdown("### 💰 Karlılık")
    t = scenario_totals(records)
    portfolio = scenario_totals(all_open)
    initial = snap["initial_capital"]
    stop_equity = snap["equity"] + portfolio["stop_giveback"]

    def _vs_initial(equity: float) -> str:
        if not initial:
            return f"Portföy değeri: {equity:,.2f}$"
        pl = equity - initial
        return f"Portföy değeri: {equity:,.2f}$ · ilk girişe göre {pl:+,.2f}$ ({pl / initial * 100:+.2f}%)"

    c1, c2, c3 = st.columns(3)
    with c1.container(border=True):
        st.markdown("**📍 Şu anki fiyattan satılırsa**")
        st.metric("Açık pozisyonların K/Z'si", _fmt_money(t["unrealized"]), delta=_fmt_pct(t["unrealized_pct"]),
                  help="Açık pozisyonların bugünkü fiyattan kapatılması halinde (Alpaca gerçekleşmemiş K/Z).")
        st.caption(_md(f"{t['open_count']} açık pozisyon · maliyet {t['open_cost']:,.0f}$\n\n{_vs_initial(snap['equity'])}"))
    with c2.container(border=True):
        st.markdown("**🛡️ Stop loss'lar devreye girerse**")
        st.metric("Açık pozisyonların K/Z'si", _fmt_money(t["stop_unrealized"]), delta=_fmt_pct(t["stop_unrealized_pct"]),
                  help="Her pozisyonun açık stop emri tetiklenip stop seviyesinden kapanması halinde. Açılış kalkanının "
                       "geçici felaket stopu yerine geri döneceği gerçek stop kullanılır.")
        st.caption(_md(f"Şu ana göre fark: {t['stop_giveback']:+,.2f}$ (stop tetiklenirse geri verilecek)\n\n"
                       f"{_vs_initial(stop_equity)}"))
    with c3.container(border=True):
        st.markdown("**✅ Kapanan pozisyonlar (gerçekleşen)**")
        st.metric("Gerçekleşen K/Z", _fmt_money(t["closed_realized"] + t["partial_realized"]),
                  help="Seçili penceredeki kapanmış işlemler + açık pozisyonlardaki kısmi satışlar.")
        parts = [f"{t['closed_count']} kapalı işlem"]
        if t["win_rate"] is not None:
            parts.append(f"isabet %{t['win_rate']:.0f}")
        if t["total_r"] is not None:
            parts.append(f"toplam {t['total_r']:+.2f}R")
        if t["expectancy_r"] is not None:
            parts.append(f"işlem başı {t['expectancy_r']:+.2f}R")
        partial = f"\n\nKısmi satışlardan: {t['partial_realized']:+,.2f}$" if abs(t["partial_realized"]) > 0.005 else ""
        st.caption(_md(" · ".join(parts) + partial))

    if t["stopless_count"]:
        st.warning(f"⚠️ Stop emri olmayan {t['stopless_count']} açık pozisyon var ({', '.join(t['stopless_symbols'])}) - "
                   "stop senaryosunda bu pozisyonlar anlık fiyattan sayıldı.")

    opened = [r for r in records if r.status == STATUS_OPEN]
    if opened:
        with st.expander(f"📋 Açık pozisyonlar: şimdi satılırsa vs stop devreye girerse ({len(opened)})", expanded=True):
            rows = []
            for r in sorted(opened, key=lambda r: -r.unrealized):
                cost = r.entry_price * r.qty
                rows.append({
                    "Hisse": r.symbol,
                    "Algoritma": r.algorithm,
                    "Stop Loss": _stop_label(r.stop_algorithm),
                    "Adet": r.qty,
                    "Maliyet $": round(r.entry_price, 2),
                    "Son $": round(r.last_price, 2),
                    "Stop $": round(r.current_stop, 2) if r.current_stop is not None else None,
                    "Stopa uzaklık %": round((r.current_stop / r.last_price - 1) * 100, 2)
                    if r.current_stop is not None and r.last_price else None,
                    "Şimdi K/Z $": round(r.unrealized, 2),
                    "Şimdi K/Z %": round(r.unrealized / cost * 100, 2) if cost else None,
                    "Stop K/Z $": round(r.stop_unrealized, 2) if r.stop_unrealized is not None else None,
                    "Stop K/Z %": round(r.stop_unrealized / cost * 100, 2) if r.stop_unrealized is not None and cost else None,
                    "Fark $": round(r.stop_unrealized - r.unrealized, 2) if r.stop_unrealized is not None else None,
                })
            _table(pd.DataFrame(rows), ["Şimdi K/Z $", "Şimdi K/Z %", "Stop K/Z $", "Stop K/Z %", "Fark $"])
            st.caption("Stop K/Z pozitifse stop kârı kilitlemiş demektir (breakeven/trail). 'Fark' = stop K/Z − şimdi K/Z: "
                       "stop tetiklenirse bugünkü kârdan geri verilecek tutar.")


# ------------------------------------------------------------------------------
# 3. Algoritma ve stop loss karlılığı
# ------------------------------------------------------------------------------

def _group_dataframe(rows: list[dict], grouping: str, capital: float | None) -> pd.DataFrame:
    out = []
    for r in rows:
        if grouping == GROUP_ALGO_STOP:
            head = {"Algoritma": r["key"][0], "Stop Loss": r["key"][1]}
        elif grouping == GROUP_ALGO:
            head = {"Algoritma": r["key"]}
        else:
            head = {"Stop Loss": r["key"]}
        row = {
            **head,
            "Kapalı": r["closed"],
            "Açık": r["open"],
            "İsabet %": round(r["win_rate"], 0) if r["win_rate"] is not None else None,
            "Gerçekleşen $": round(r["realized"], 2),
            "Açık K/Z $": round(r["unrealized"], 2),
            "Toplam (şimdi) $": round(r["total"], 2),
            "Toplam (stop) $": round(r["stop_total"], 2),
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


def _group_label(key) -> str:
    return " · ".join(key) if isinstance(key, tuple) else str(key)


def _group_chart(rows: list[dict]) -> go.Figure:
    p = get_palette()
    rows = list(reversed(rows))  # yatay çubukta en kârlı en üstte
    labels = [_group_label(r["key"]) for r in rows]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[r["total"] for r in rows], y=labels, orientation="h", name="Şimdi satılırsa",
        marker_color=p["accent"],
        customdata=[[r["realized"], r["unrealized"], r["closed"], r["open"]] for r in rows],
        hovertemplate="<b>%{y}</b><br>Şimdi: %{x:+,.2f}$<br>Gerçekleşen: %{customdata[0]:+,.2f}$"
                      "<br>Açık K/Z: %{customdata[1]:+,.2f}$<br>Kapalı/Açık: %{customdata[2]} / %{customdata[3]}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=[r["stop_total"] for r in rows], y=labels, orientation="h", name="Stoplar devreye girerse",
        marker_color=p["text_muted"], opacity=0.55,
        hovertemplate="<b>%{y}</b><br>Stop senaryosu: %{x:+,.2f}$<extra></extra>",
    ))
    fig.update_layout(template=get_plotly_template(), barmode="group",
                      height=max(240, 48 * len(rows) + 90), margin=dict(l=10, r=10, t=10, b=30),
                      xaxis_title="Toplam K/Z ($)", legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    fig.add_vline(x=0, line_width=1, line_color=p["text_muted"])
    return fig


def _render_algo_stop(records: list, split_tf: bool, capital: float | None):
    st.markdown("### 🧠 Algoritma ve Stop Loss Karlılığı")
    grouping = st.radio("Gruplama", [GROUP_ALGO_STOP, GROUP_ALGO, GROUP_STOP], horizontal=True, key="aa_grouping",
                        label_visibility="collapsed")
    if grouping == GROUP_ALGO_STOP:
        key = lambda r: (r.algo_label(split_tf), _stop_label(r.stop_algorithm))  # noqa: E731
    elif grouping == GROUP_ALGO:
        key = lambda r: r.algo_label(split_tf)  # noqa: E731
    else:
        key = lambda r: _stop_label(r.stop_algorithm)  # noqa: E731
    rows = group_summary(records, key)
    st.plotly_chart(_group_chart(rows), use_container_width=True)
    _table(_group_dataframe(rows, grouping, capital),
           ["Gerçekleşen $", "Açık K/Z $", "Toplam (şimdi) $", "Toplam (stop) $", "Getiri %", "Sermaye %"])
    st.caption(
        "'Toplam (şimdi)' = gerçekleşen + açık pozisyonların anlık K/Z'si; 'Toplam (stop)' = açık pozisyonlar stop "
        "seviyesinden kapanırsa. Algoritma, pozisyonu 0'dan açan alım emrinin etiketinden okunur. Stop loss algoritması "
        "emir geçmişinde tutulmadığı için modüllerin güncel ayarından çözülür (RS / ORB / Heikin Ashi kendi ayarı, "
        "diğerleri Premium Buy Point portföy/hisse ayarı) - ayar sonradan değiştiyse eski işlemler yeni algoritma "
        "altında görünür. İsabet, R ve Profit Factor yalnızca kapalı işlemlerden hesaplanır."
    )


# ------------------------------------------------------------------------------
# 4. Hisse hareketleri
# ------------------------------------------------------------------------------

def _movements_dataframe(records, split_timeframe: bool) -> pd.DataFrame:
    rows = []
    for r in sorted(records, key=lambda r: (r.exit_time or r.entry_time or datetime.min.replace(tzinfo=TR_TZ)),
                    reverse=True):
        is_open = r.status == STATUS_OPEN
        r_mult = open_r_multiple(r) if is_open else r.r_multiple
        rows.append({
            "Hisse": r.symbol,
            "Durum": r.status,
            "Algoritma": r.algo_label(split_timeframe),
            "Stop Loss": _stop_label(r.stop_algorithm),
            "Stop Güncelleme": max(len(r.stop_moves) - 1, 0) if r.stop_moves else None,
            "Stop Hareketleri": format_stop_moves(r.stop_moves, TR_TZ) or "—",
            "Giriş (TRT)": r.entry_time.astimezone(TR_TZ).strftime("%d.%m.%y %H:%M") if r.entry_time else "—",
            "Çıkış (TRT)": r.exit_time.astimezone(TR_TZ).strftime("%d.%m.%y %H:%M") if r.exit_time else "—",
            "Adet": r.qty,
            "Giriş $": round(r.entry_price, 2),
            "Son/Çıkış $": round(r.last_price, 2),
            "İlk Stop $": round(r.initial_stop, 2) if r.initial_stop is not None else None,
            "Güncel Stop $": round(r.current_stop, 2) if r.current_stop is not None else None,
            "Gerçekleşen $": round(r.realized, 2),
            "Açık K/Z $": round(r.unrealized, 2),
            "Toplam $": round(r.total, 2),
            "Stop senaryosu $": round(r.stop_total, 2) if is_open else None,
            "K/Z %": round(r.pnl_pct, 2),
            "R": round(r_mult, 2) if r_mult is not None else None,
            "Çıkış Sebebi": r.exit_reason or ("Açık" if is_open else "—"),
        })
    return pd.DataFrame(rows)


def _render_movements(records: list, split_tf: bool):
    st.markdown("### 📋 Hisse Hareketleri")
    c1, c2 = st.columns([2, 3])
    symbols = sorted({r.symbol for r in records})
    pick = c1.selectbox("Hisse", ["Tümü"] + symbols, key="aa_symbol")
    status_opt = c2.radio("Durum", ["Hepsi", STATUS_OPEN, STATUS_CLOSED], horizontal=True, key="aa_status")
    shown = [r for r in records if (pick == "Tümü" or r.symbol == pick)
             and (status_opt == "Hepsi" or r.status == status_opt)]
    if not shown:
        st.info("Bu seçimle gösterilecek hareket yok.")
        return
    _table(_movements_dataframe(shown, split_tf),
           ["Gerçekleşen $", "Açık K/Z $", "Toplam $", "Stop senaryosu $", "K/Z %", "R"],
           column_config={
               "Stop Hareketleri": st.column_config.TextColumn(
                   "Stop Hareketleri", width="large",
                   help="Giriş ile çıkış (açık pozisyonda şimdi) arasında kurulan stop seviyeleri, eskiden yeniye. "
                        "Hücrenin üzerine gelince tamamı görünür."),
               "Stop Güncelleme": st.column_config.NumberColumn(
                   "Stop Güncelleme", help="İlk stoptan sonra stopun kaç kez taşındığı."),
           })
    st.caption(
        "Stop hareketleri: tarih (TRT), seviye ve sebep - İlk: ilk stop, BE: breakeven, Yapısal: yapısal trail, "
        "ATR trail: chandelier, Kalkan: açılış kalkanının geçici felaket stopu (parantezde dönülecek gerçek seviye), "
        "Kalkan sonrası: gerçek stopa dönüş. Sebepsiz seviyeler 28.09.2026 öncesi ya da elle konmuş stoplardır. "
        "Açık pozisyonun giriş emri seçili pencerede yoksa sembolün son "
        f"{OPEN_ENTRY_LOOKBACK_DAYS} günlük geçmişi taranır; yine bulunamazsa algoritma 'Bilinmiyor' görünür. Açık "
        "pozisyonlardaki R anlık fiyata göredir. R = (çıkış − giriş) / (giriş − ilk stop)."
    )


# ------------------------------------------------------------------------------
# 5. Çıkış sebebi ve seans dilimi istatistikleri
# ------------------------------------------------------------------------------

def _count_table(counts: dict, label: str) -> pd.DataFrame:
    total = sum(counts.values()) or 1
    return pd.DataFrame(
        [{label: k, "İşlem": v, "Pay %": round(v / total * 100, 1)} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
    )


def _render_exit_stats(records: list):
    closed = [r for r in records if r.status == STATUS_CLOSED]
    st.markdown("### 🚪 Çıkış ve Seans İstatistikleri")
    if not closed:
        st.caption("Bu kapsamda kapanmış işlem yok.")
        return
    t1, t2 = st.columns(2)
    with t1:
        st.markdown("**Çıkış sebebi**")
        _table(_count_table(count_by(closed, "exit_reason"), "Sebep"))
    with t2:
        st.markdown("**Çıkış seans dilimi**")
        _table(_count_table(count_by(closed, "exit_session"), "Dilim"))
        st.markdown("**Giriş seans dilimi**")
        _table(_count_table(count_by(closed, "entry_session"), "Dilim"))
    st.caption(
        f"Kapanmış işlemler üzerinden. '{SESSION_OPENING}': 09:30-09:45 ET · '{SESSION_REGULAR}': 09:45-16:00 ET · "
        f"'{SESSION_EXTENDED}': pre-market / after-hours. 28.09.2026 öncesi stop emirleri etiketsiz olduğu için "
        "sebepleri 'Stop (etiketsiz)' görünür."
    )


# ------------------------------------------------------------------------------
# Sayfa
# ------------------------------------------------------------------------------

def _render_algo_analiz(username: str):
    key_id, secret_key, trading_url = _credentials(username)
    if not key_id or not secret_key:
        return

    try:
        account = _fetch_account(key_id, secret_key, trading_url)
        positions = _fetch_positions(key_id, secret_key, trading_url)
        open_orders = _fetch_open_orders(key_id, secret_key, trading_url)
    except Exception as e:
        st.error(f"Alpaca hesap verisi alınamadı: {e}")
        return

    snap = portfolio_snapshot(account, _initial_capital(username))
    _render_portfolio(snap, len(positions))
    module_cash_slot = st.container()
    freshness_caption(f"Veri güncelliği: {datetime.now(TR_TZ):%d.%m.%Y %H:%M:%S} TRT "
                      "(hesap/pozisyon/stoplar 1 dk, emir geçmişi 5 dk önbellek).")

    rules_since, rules_since_raw = _rules_since(username)
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([1, 2, 1, 1])
        days = c1.selectbox("Kapanan işlemler: geriye dönük gün", JOURNAL_DAYS_OPTIONS, index=2, key="aa_days",
                            help="Kapanmış işlemler için pencere. Açık pozisyonlar her zaman dahildir.")
        scope_options = ["all"] + (["rules"] if rules_since else [])
        scope = c2.radio(
            "Kapsam", scope_options, horizontal=True, key="aa_scope",
            format_func=lambda v: "Tüm işlemler" if v == "all" else f"Mevcut kural sürümü ({rules_since_raw[:10]} sonrası)",
        )
        split_tf = c3.toggle("Periyodu ayrı göster", value=False, key="aa_split",
                             help="Algoritmayı giriş periyoduyla birlikte gösterir (ör. 1Day / 1Hour).")
        include_manual = c4.toggle("Elle işlemler dahil", value=True, key="aa_manual",
                                   help="Elle açılan ya da giriş emri bulunamayan işlemler.")

    try:
        orders = _fetch_orders(key_id, secret_key, trading_url, int(days))
    except Exception as e:
        st.error(f"Alpaca emir geçmişi alınamadı: {e}")
        return

    trips, lots = walk_fills(orders)
    for sym in [p["symbol"] for p in positions if p.get("symbol") not in lots]:
        try:
            _, sym_lots = walk_fills(_fetch_symbol_orders(key_id, secret_key, trading_url, sym))
        except Exception:
            continue
        if sym in sym_lots:
            lots[sym] = sym_lots[sym]

    all_records = closed_records(trips) + open_records(positions, lots)
    apply_stop_scenario(all_records, open_stop_levels(open_orders))
    pbp_config, module_algos, module_holdings = _stop_configs(username)
    for r in all_records:
        try:
            r.stop_algorithm = resolve_stop_algorithm_id(r, pbp_config, module_algos, module_holdings)
        except Exception:
            r.stop_algorithm = None
    all_open = [r for r in all_records if r.status == STATUS_OPEN]
    records = filter_records(all_records, since=rules_since if scope == "rules" else None,
                             include_manual=include_manual)

    if rules_since:
        n_rules = sum(1 for r in all_records if r.status == STATUS_CLOSED and r.entry_time and r.entry_time >= rules_since)
        if n_rules < MIN_TRADES_FOR_EVALUATION:
            st.info(
                f"🧊 Mevcut kural sürümü ({rules_since_raw[:10]}) ile **{n_rules}** işlem kapandı. Sonuçlar "
                f"{MIN_TRADES_FOR_EVALUATION} işlem birikmeden istatistiksel olarak anlamlı değil - bu süre "
                "zarfında algoritma/stop/risk ayarlarını değiştirmemeniz önerilir."
            )
        else:
            st.success(f"🧊 Mevcut kural sürümü ile {n_rules} işlem kapandı - kurallar değerlendirilebilir.")

    with module_cash_slot:
        try:
            closed_in_scope = [r for r in records if r.status != STATUS_OPEN]
            _render_module_cash(module_cash_rows(account, positions, _module_allocations(username),
                                                 all_open + closed_in_scope))
        except Exception as e:
            st.warning(f"Algoritmalara ayrılan nakit hesaplanamadı: {e}")

    _render_profitability(snap, all_open, records)
    st.divider()
    if not records:
        st.info("Bu filtrelerle gösterilecek işlem ya da pozisyon yok.")
        return
    _render_algo_stop(records, split_tf, snap["initial_capital"])
    st.divider()
    _render_movements(records, split_tf)
    st.divider()
    _render_exit_stats(records)


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


def render_algo_analiz(username: str):
    tab_analysis, tab_changes = st.tabs([PAGE_TITLE, "📝 Değişiklik Günlüğü"])
    with tab_analysis:
        _render_algo_analiz(username)
    with tab_changes:
        _render_changelog()
