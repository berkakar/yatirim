"""📒 İşlem Günlüğü sayfası - [2026-09-28 · Öneri 6].

İki sekme:
  - 📒 İşlem Günlüğü: Alpaca emir geçmişinden kapanmış işlemler, R çarpanı,
    çıkış sebebi, seans dilimi ve kural sürümüne göre özet (trade_journal.py).
  - 📝 Değişiklik Günlüğü: 2026-09-28 emir analizi ve ondan çıkan
    değişikliklerin gerekçeleri, kod yerleri, ayarları ve takip ölçütleri
    (changelog.py).
"""

from datetime import datetime

import pandas as pd
import streamlit as st

from alpaca_client import AlpacaClient
from alpaca_dashboard import TR_TZ
from changelog import ANALYSIS_SUMMARY, CHANGES, VERIFICATION_NOTES
from github_config import read_portfolio_config
from rules_version import MIN_TRADES_FOR_EVALUATION
from trade_journal import SESSION_EXTENDED, SESSION_OPENING, SESSION_REGULAR, build_round_trips, summarize
from ui_style import freshness_caption, zebra_style

GITHUB_REPO = "berkakar/yatirim"
JOURNAL_DAYS_OPTIONS = [30, 60, 90, 180]


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_orders(key_id: str, secret_key: str, days: int) -> list[dict]:
    return AlpacaClient(key_id, secret_key).get_recent_orders(days=days, limit=500, nested=True)


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
            "İlk Stop $": round(t.initial_stop, 2) if t.initial_stop is not None else "—",
            "K/Z $": round(t.pnl, 2),
            "K/Z %": round(t.pnl_pct, 2),
            "R": round(r, 2) if r is not None else "—",
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
    user_alpaca = st.secrets.get("alpaca", {}).get(username, {})
    key_id, secret_key = user_alpaca.get("key_id"), user_alpaca.get("secret_key")
    if not key_id or not secret_key:
        st.warning(f"'{username}' için Alpaca hesabı tanımlı değil (`.streamlit/secrets.toml` içinde `[alpaca.{username}]`).")
        return

    config = {}
    token = st.secrets.get("GITHUB_TOKEN")
    if token:
        try:
            config = read_portfolio_config(GITHUB_REPO, token, username)
        except Exception:
            config = {}
    rules_since_raw = config.get("rules_version_since")
    rules_since = datetime.fromisoformat(rules_since_raw) if rules_since_raw else None

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
    tab_journal, tab_changes = st.tabs(["📒 İşlem Günlüğü", "📝 Değişiklik Günlüğü"])
    with tab_journal:
        _render_journal(username)
    with tab_changes:
        _render_changelog()
