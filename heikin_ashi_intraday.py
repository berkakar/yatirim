"""Heikin Ashi Gün İçi - Streamlit sayfası.

Diğer algoritmik ticaret sayfalarıyla (orb_scan.py, relative_strength.py)
AYNI ilke: bu sayfa GERÇEK bir Alpaca emri YERLEŞTİRMEZ - sadece ayarları
GitHub'a kaydeder ve salt-okunur bir önizleme gösterir. Gerçek alım/satımı,
kullanıcı "otomatik çalıştır" kutusunu işaretlerse, seans boyunca yarım
saatte bir heikin_ashi_intraday_runner.py (GitHub Actions) yürütür - bkz.
heikin_ashi_intraday_core.py'nin modül üstü notu.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from alpaca_client import AlpacaClient
from config import load_group_markets, load_stock_groups
from github_config import read_json_from_github, write_json_to_github
from heikin_ashi_intraday_core import (
    DEFAULT_CASH_ALLOCATION_PCT, DEFAULT_MAX_POSITIONS, EOD_FLATTEN_MINUTES, HA_INTRADAY_DEFAULT_STOP_ALGORITHM,
    NO_NEW_ENTRY_MINUTES, config_path, excluded_symbols, holdings_path, scan_candidates,
)
from otomatik_alim_satim_core import DEFAULT_MIN_AVG_DOLLAR_VOLUME, build_universe, filter_by_liquidity
from stop_algorithms import STOP_ALGORITHMS
from ui_style import zebra_style, freshness_caption

TR_TZ = ZoneInfo("Europe/Istanbul")
GITHUB_REPO = "berkakar/yatirim"

BUY_LOGIC_MD = """
**Alım mantığı** (30 dakikalık kapanmış barlar, *Premium Buy Point / BackTest*'teki
**Heikin Ashi + SMA50 + Stokastik** algoritmasının birebir aynısı):

1. **Trend:** Kapanış, 50 barlık basit hareketli ortalamanın (SMA50) üzerinde.
2. **Momentum:** Stokastik (14, 3, 3) %K **30'un altında** (aşırı satım) ve %K, %D'nin **üzerinde** (yukarı dönüş).
3. **Mum yapısı:** Önceki Heikin Ashi mumu **kırmızı**, son HA mumu **yeşil** ve **alt fitilsiz**
   (alt fitil HA mum boyunun en fazla %5'i).

Üç şartın hepsini sağlayan hisseler bir **alım puanıyla** (Stokastik %K−%D farkı + kapanışın
SMA50'nin yüzde kaç üstünde olduğu) sıralanır; boş pozisyon slotu kadar en yüksek puanlı hisse
**market emriyle** alınır. Sinyal barın kapanışında onaylandığı için bekleyen limit emri kullanılmaz.
"""

SELL_LOGIC_MD = f"""
**Satış / stop-loss mantığı:**

1. **İlk stop (koruma):** Alımdan hemen sonra, sinyal mumunun low'unun biraz altına stop emri
   kurulur (*Stop Loss Ayarları → Heikin Ashi Çıkışı*'ndaki tampon; bulunamazsa sabit yüzde).
2. **Strateji çıkışı (yarım saatte bir):** Kapanmış son 30 dakikalık barda **ilk kırmızı Heikin
   Ashi mumu** oluştuysa **ya da** Stokastik %K **80'in üzerindeyken %D'nin altına** indiyse,
   stop iptal edilip pozisyon **market emriyle satılır**.
3. **Trailing stop botu (5 dakikada bir):** Diğer modüllerle aynı `alpaca_trailing_stop.py`
   botu bu modülün pozisyonlarını **Heikin Ashi Çıkışı** algoritmasıyla yönetir - çıkış sinyalinde
   stopu kapanışın hemen altına çeker, sinyal yokken breakeven + yapısal trail uygular.
4. **Gün sonu:** Seans kapanışına **{EOD_FLATTEN_MINUTES} dakikadan** az kala elde kalan tüm pozisyonlar
   market emriyle kapatılır (pozisyon geceye taşınmaz). Kapanışa **{NO_NEW_ENTRY_MINUTES} dakikadan** az
   kala yeni alım yapılmaz.
"""


def _load_config(repo: str, token: str, username: str) -> dict:
    return read_json_from_github(repo, token, config_path(username), {"enabled": False})


def _save_config(repo: str, token: str, config: dict, username: str) -> None:
    write_json_to_github(repo, token, config_path(username), config, f"Update Heikin Ashi Gün İçi config ({username})")


def _load_holdings(repo: str, token: str, username: str) -> dict:
    return read_json_from_github(repo, token, holdings_path(username), {})


def render_heikin_ashi_intraday(username: str):
    user_alpaca = st.secrets.get("alpaca", {}).get(username, {})
    key_id = user_alpaca.get("key_id")
    secret_key = user_alpaca.get("secret_key")
    github_token = st.secrets.get("GITHUB_TOKEN")

    if not key_id or not secret_key:
        st.warning(f"'{username}' için Alpaca hesabı tanımlı değil (`.streamlit/secrets.toml` içinde `[alpaca.{username}]`).")
        return
    if not github_token:
        st.warning("`.streamlit/secrets.toml` içinde GITHUB_TOKEN tanımlı değil - ayarlar kaydedilemez.")
        return

    client = AlpacaClient(key_id, secret_key)
    config = _load_config(GITHUB_REPO, github_token, username)

    with st.expander("📖 Strateji: alım ve satış mantığı", expanded=True):
        c1, c2 = st.columns(2)
        c1.markdown(BUY_LOGIC_MD)
        c2.markdown(SELL_LOGIC_MD)

    try:
        live_cash = float(client.get_account()["cash"])
    except Exception:
        live_cash = None
    if live_cash is not None:
        st.metric("Alpaca'daki kullanılabilir nakit (toplam)", f"${live_cash:,.2f}")
    else:
        st.warning("⚠️ Alpaca'daki güncel nakit bakiye alınamadı.")

    st.info(
        "💰 **Nakit payı nasıl çalışır?** Relative Strength Rotasyonu ve ORB ile aynı ilke: bu modül "
        "hesabınızın TOPLAM canlı nakdinden bir yüzde AYIRIR, Premium Buy Point'in kullanabileceği nakit "
        "buna göre otomatik küçülür. Bu pay, en fazla pozisyon sayısına eşit bölünür. Modül devre dışıyken "
        "pay 0 kabul edilir."
    )
    cash_allocation_pct = st.number_input(
        "Bu modüle ayrılacak nakit payı (%)",
        min_value=0.0, max_value=100.0,
        value=float(config.get("cash_allocation_pct") or DEFAULT_CASH_ALLOCATION_PCT), step=5.0,
        key="hai_cash_allocation_pct",
    )
    max_positions = st.number_input(
        "Aynı anda en fazla kaç pozisyon", min_value=1, max_value=20,
        value=int(config.get("max_positions") or DEFAULT_MAX_POSITIONS), step=1, key="hai_max_positions",
        help="Nakit payı bu sayıya eşit bölünür; her yarım saatlik taramada sadece boş slot kadar yeni alım yapılır.",
    )
    if live_cash is not None and cash_allocation_pct > 0:
        total = live_cash * cash_allocation_pct / 100
        st.caption(f"Bu ayarla modüle düşen tutar: **${total:,.2f}** · pozisyon başına: **${total / max_positions:,.2f}**")

    st.subheader("🌐 Tarama Evreni")
    st.caption(
        "Premium Buy Point'in watchlist'indeki, Relative Strength Rotasyonu'nun ve ORB'un elindeki semboller "
        "bu evrenden HER ZAMAN hariç tutulur - bağımsız sistemlerin aynı hissede çakışmaması için."
    )
    c1, c2, c3 = st.columns(3)
    include_nasdaq = c1.checkbox("NASDAQ 100", value=config.get("include_nasdaq", True), key="hai_include_nasdaq")
    include_nyse = c2.checkbox("NYSE", value=config.get("include_nyse", False), key="hai_include_nyse")
    include_russell = c3.checkbox("Russell 2000", value=config.get("include_russell", False), key="hai_include_russell")

    group_markets = load_group_markets(username)
    stock_groups = load_stock_groups(username)
    eligible_groups = [g for g in stock_groups if group_markets.get(g) in ("NASDAQ 100", "NYSE", "Russell 2000")]
    custom_groups = st.multiselect(
        "NASDAQ/NYSE/Russell 2000'e bağlı özel hisse grupları (varsa)",
        eligible_groups,
        default=[g for g in (config.get("custom_groups") or []) if g in eligible_groups],
        key="hai_custom_groups",
    )
    min_avg_dollar_volume = st.number_input(
        "Likidite eşiği - ortalama günlük ciro ($, son 20 işlem günü)",
        min_value=0.0, value=float(config.get("min_avg_dollar_volume") or DEFAULT_MIN_AVG_DOLLAR_VOLUME),
        step=500_000.0, format="%.0f", key="hai_min_avg_dollar_volume",
        help="Market emriyle giren bu sistemde kayma (slippage) riskini sınırlamak için.",
    )

    stop_algorithm_ids = list(STOP_ALGORITHMS.keys())
    current_stop_algorithm = config.get("stop_algorithm", HA_INTRADAY_DEFAULT_STOP_ALGORITHM)
    if current_stop_algorithm not in stop_algorithm_ids:
        current_stop_algorithm = HA_INTRADAY_DEFAULT_STOP_ALGORITHM
    stop_algorithm = st.selectbox(
        "Stop-Loss Algoritması",
        stop_algorithm_ids, index=stop_algorithm_ids.index(current_stop_algorithm),
        format_func=lambda k: STOP_ALGORITHMS[k].label, key="hai_stop_algorithm",
        help="Varsayılan 'Heikin Ashi Çıkışı' bu strateji için tasarlandı. Başka bir algoritma seçilirse "
             "trailing stop botu onu kullanır; yarım saatlik HA çıkış satışı ve gün sonu kapatma yine çalışır.",
    )

    st.subheader("🕐 Otomatik Çalıştırma (yarım saatte bir)")
    st.caption(
        "Etkinleştirilirse seans boyunca her 30 dakikalık bar kapanışından sonra GitHub Actions üzerinden: "
        "çıkış sinyali veren pozisyonlar satılır, boş slotlar için evren taranıp alım yapılır. GitHub "
        "Actions zamanlaması birkaç dakika gecikebilir. Önizleme butonu HİÇBİR ZAMAN gerçek emir vermez."
    )
    automated = st.checkbox(
        "Bu süreci otomatik çalıştır (seans boyunca yarım saatte bir)", value=bool(config.get("enabled")),
        key="hai_enabled",
    )
    if config.get("last_run_at"):
        summary = config.get("last_run_summary") or {}
        if summary.get("skipped"):
            st.caption(f"Son otomatik koşu: {config['last_run_at']} — atlandı ({summary.get('reason')}).")
        else:
            st.caption(
                f"Son otomatik koşu: {config['last_run_at']} — evren {summary.get('universe_size', '—')}, "
                f"aday {summary.get('candidate_count', '—')}, "
                f"satılan: {', '.join(summary.get('sold') or []) or 'yok'}, "
                f"alınan: {', '.join(summary.get('bought') or []) or 'yok'}."
            )
            if summary.get("errors"):
                st.caption("Hatalar: " + " · ".join(summary["errors"]))

    if st.button("💾 Ayarları Kaydet", type="primary", key="hai_save"):
        new_config = dict(config)
        new_config.update({
            "enabled": automated,
            "cash_allocation_pct": float(cash_allocation_pct),
            "max_positions": int(max_positions),
            "include_nasdaq": include_nasdaq,
            "include_nyse": include_nyse,
            "include_russell": include_russell,
            "custom_groups": custom_groups,
            "min_avg_dollar_volume": float(min_avg_dollar_volume),
            "stop_algorithm": stop_algorithm,
        })
        _save_config(GITHUB_REPO, github_token, new_config, username)
        st.success("Ayarlar kaydedildi.")
        st.rerun()

    st.divider()
    st.subheader("🔎 Önizleme (salt-okunur - gerçek emir vermez)")
    if st.button("🔎 Şimdi Tara ve Önizle", key="hai_preview"):
        with st.spinner("Evren taranıyor ve Heikin Ashi sinyalleri aranıyor..."):
            universe = build_universe(username, include_nasdaq, include_nyse, custom_groups, include_russell)
            own_holdings = _load_holdings(GITHUB_REPO, github_token, username)
            excluded = excluded_symbols(client, username)
            universe = [t for t in universe if t not in excluded and t not in own_holdings]
            universe = filter_by_liquidity(client, universe, float(min_avg_dollar_volume))
            candidates = scan_candidates(client, universe)
            free_slots = max(0, int(max_positions) - len(own_holdings))
        st.session_state["hai_preview_candidates"] = candidates
        st.session_state["hai_preview_selected"] = {c.symbol for c in candidates[:free_slots]}
        st.session_state["hai_preview_universe_size"] = len(universe)
        st.session_state["hai_preview_fetched_at"] = datetime.now(TR_TZ)

    candidates = st.session_state.get("hai_preview_candidates")
    if candidates is not None:
        fetched_at = st.session_state.get("hai_preview_fetched_at")
        if fetched_at:
            freshness_caption(f"Veri güncelliği: {fetched_at:%d.%m.%Y %H:%M:%S} TRT (Alpaca'dan önizleme anında çekildi).")
        st.caption(f"Taranan evren: {st.session_state.get('hai_preview_universe_size', 0)} hisse.")
        selected_symbols = st.session_state.get("hai_preview_selected") or set()
        if candidates:
            rows = [
                {"Hisse": c.symbol, "Fiyat": c.price, "Alım Puanı": c.score,
                 "Alınırdı": c.symbol in selected_symbols, "Neden": c.reason}
                for c in candidates
            ]
            st.dataframe(zebra_style(pd.DataFrame(rows)), use_container_width=True, hide_index=True)
        else:
            st.info("Son kapanmış 30 dakikalık barda alım şartlarını sağlayan hisse yok - bu strateji "
                    "sıkı şartlara sahip olduğundan sık görülen bir durumdur.")

    st.divider()
    st.subheader("📦 Şu Anki Pozisyonlar")
    holdings = _load_holdings(GITHUB_REPO, github_token, username)
    if not holdings:
        st.info("Bu modülün şu an elinde hiçbir pozisyon yok.")
    else:
        rows = [
            {"Hisse": symbol, "Adet": info.get("qty"), "Giriş Fiyatı": info.get("entry_price"),
             "İlk Stop": info.get("stop_price"), "Giriş Puanı": info.get("score"),
             "Giriş Tarihi": info.get("entered_at")}
            for symbol, info in holdings.items()
        ]
        st.dataframe(zebra_style(pd.DataFrame(rows)), use_container_width=True, hide_index=True)
