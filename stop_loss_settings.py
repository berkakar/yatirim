"""Stop Loss Ayarları modülü - altı stop-loss algoritmasının (stop_algorithms.py)
parametrelerini kullanıcı bazında ayarlamayı ve GitHub'a kalıcı olarak
kaydetmeyi sağlar (bkz. github_config.py - premium_buy_portfolio.py'nin
portföy config'i için kullandığı aynı okuma/yazma deseni,
stop_loss_settings_<kullanıcı>.json dosyasına).

Kaydedilen değerler hem BackTest modülü (backtest.py, bu sayfayla aynı
GitHub okuma yoluyla) hem canlı sistem (alpaca_trailing_stop.py,
alpaca_buy_points.py - GitHub Action'lar kendi checkout'undan aynı dosyayı
yerel olarak okur) tarafından kullanılır: stop_algorithms.resolve_kwargs,
seçili algoritmanın initial_stop()/trail() fonksiyonunun imzasını inceleyerek
burada kaydedilmiş değerleri otomatik eşler - hiç kaydedilmemiş bir alan
için ilgili fonksiyonun kod-varsayılanı (aşağıdaki kutuların ilk açılıştaki
değerleri, doğrudan stop_algorithms.py'den okunur) geçerli olur.
"""
import streamlit as st

import storage
from github_config import read_json_from_github, write_json_to_github
from alpaca_trailing_stop import EXECUTION_DEFAULTS
from stop_algorithms import (
    ADAPTIVE_BREAKEVEN_R, ADAPTIVE_ER_WEIGHT, ADAPTIVE_INITIAL_ATR_MULT, ADAPTIVE_MAX_ATR_MULT,
    ADAPTIVE_MIN_ATR_MULT, ADAPTIVE_MIN_TRAIL_ATR_MULT, ADAPTIVE_STRUCTURE_BUFFER_ATR, ADAPTIVE_TIGHTEN_PER_R,
    ADAPTIVE_TRAIL_ATR_MULT, ADAPTIVE_TRAIL_START_R, ATR_MULTIPLIER, ATR_PERIOD, ATR_VOL_BREAKEVEN_BUFFER_ATR, ATR_VOL_BREAKEVEN_R, ATR_VOL_FALLBACK_PCT,
    ATR_VOL_INITIAL_ATR_MULT, ATR_VOL_MAX_STOP_PCT, ATR_VOL_DAILY_TRAIL_ATR_MULT, ATR_VOL_DAILY_TRAIL_START_R, ATR_VOL_TRAIL_ATR_MULT, ATR_VOL_TRAIL_START_R,
    BREAKEVEN_BUFFER_PCT, BREAKEVEN_TRIGGER_PCT, FALLBACK_BUFFER_PCT, INITIAL_STOP_PCT,
    HEIKIN_ASHI_EXIT_BUFFER_PCT, HEIKIN_ASHI_MIN_ATR_MULT, HEIKIN_ASHI_STOP_BUFFER_PCT, ORB_STOP_BUFFER_PCT, ORB_TREND_EMA_PERIOD,
    DEFAULT_STOP_ALGORITHM, STALE_REFERENCE_DAYS, STOP_ALGORITHMS, SWING_ORDER, TREND_EMA_PERIOD,
    WAIT_THEN_TRAIL_BREAKEVEN_TRIGGER_PCT, WAIT_THEN_TRAIL_INITIAL_STOP_PCT,
    WAIT_THEN_TRAIL_PROFIT_LOCK_PCT, WAIT_THEN_TRAIL_PROFIT_LOCK_TRIGGER_PCT,
)

GITHUB_REPO = "berkakar/yatirim"


def _settings_path(username: str) -> str:
    return f"stop_loss_settings_{username}.json"


def load_stop_loss_settings(username: str) -> dict:
    """Kullanıcının kaydettiği stop-loss parametre override'ları - GitHub'da
    yoksa (ya da GITHUB_TOKEN tanımlı değilse) boş dict döner, bu durumda
    tüm alanlar kod-varsayılanına düşer."""
    if storage.enabled():
        return storage.read("stop_loss_settings", username, {})
    token = st.secrets.get("GITHUB_TOKEN")
    if not token:
        return {}
    try:
        return read_json_from_github(GITHUB_REPO, token, _settings_path(username), {})
    except Exception:
        return {}


def save_stop_loss_settings(username: str, settings: dict) -> None:
    if storage.enabled():
        storage.write("stop_loss_settings", username, settings)
        return
    token = st.secrets["GITHUB_TOKEN"]
    write_json_to_github(
        GITHUB_REPO, token, _settings_path(username), settings, f"Update stop-loss settings ({username})",
    )


def _resolve_algo(algo_id: str | None, default: str) -> str:
    algo = algo_id or default
    return algo if algo in STOP_ALGORITHMS else default


@st.cache_data(ttl=60, show_spinner=False)
def _live_stop_usage(username: str) -> dict[str, list[str]]:
    """Hangi stop algoritmasını hangi canlı modülün kullandığı - modüllerin
    kaydedilmiş config'lerinden, canlı botların kullandığı aynı çözümlemeyle
    (bkz. alpaca_trailing_stop.resolve_stop_algorithm_for_position). Bir
    config okunamazsa o modül listede yer almaz."""
    # Fonksiyon içi importlar: bkz. alpaca_trailing_stop.resolve_stop_algorithm_for_position'daki döngü notu.
    from alpaca_trailing_stop import resolve_stop_algorithm
    import heikin_ashi_intraday_core
    import orb_core
    import relative_strength_core

    token = st.secrets.get("GITHUB_TOKEN")

    def _read(path: str) -> dict | None:
        try:
            return read_json_from_github(GITHUB_REPO, token, path, {})
        except Exception:
            return None

    usage: dict[str, list[str]] = {algo_id: [] for algo_id in STOP_ALGORITHMS}
    pbp = _read(f"portfolio_config_{username}.json")
    if pbp is not None:
        pbp_default = _resolve_algo(pbp.get("stop_algorithm"), DEFAULT_STOP_ALGORITHM)
        usage[pbp_default].append("Premium Buy Point (varsayılan)")
        overrides: dict[str, int] = {}
        for symbol in pbp.get("symbol_settings") or {}:
            algo = resolve_stop_algorithm(pbp, symbol)
            if algo != pbp_default:
                overrides[algo] = overrides.get(algo, 0) + 1
        for algo, n in overrides.items():
            usage[algo].append(f"Premium Buy Point ({n} hisseye özel)")
    for label, core in (
        ("Relative Strength Rotasyonu", relative_strength_core),
        ("ORB", orb_core),
        ("Heikin Ashi Gün İçi", heikin_ashi_intraday_core),
    ):
        cfg = _read(core.config_path(username))
        if cfg and cfg.get("enabled"):
            usage[core.resolve_stop_algorithm(cfg)].append(label)
    return usage


def _tab_label(algo_id: str, emoji: str, usage: dict[str, list[str]]) -> str:
    live = "🟢 " if usage.get(algo_id) else ""
    return f"{live}{emoji} {STOP_ALGORITHMS[algo_id].label}"


def _live_note(algo_id: str, usage: dict[str, list[str]]) -> None:
    users = usage.get(algo_id) or []
    if users:
        st.success("🟢 **Canlı botları etkiler** - kullanan: " + ", ".join(users) + ". Kaydedilen değişiklik "
                   "bir sonraki bot çalışmasında açık pozisyonlara uygulanır.")
    else:
        st.caption("⚪ Şu an hiçbir canlı modül bu algoritmayı kullanmıyor - değişiklik yalnızca BackTest'i "
                   "ve ileride bu algoritmayı seçecek modülleri etkiler.")


def render_stop_loss_settings(username: str):
    st.caption(
        "Altı stop-loss algoritmasının ve emir yürütme ayarlarının parametreleri. Kutular kod-varsayılanlarıyla "
        "dolu gelir; değiştirmeden kaydetmek davranışı değiştirmez. 🟢 işaretli sekmeler şu an canlı "
        "botlarda kullanılan algoritmalardır. Kaydet düğmesi tüm sekmelerdeki değerleri birlikte kaydeder."
    )

    if not storage.enabled() and not st.secrets.get("GITHUB_TOKEN"):
        st.warning("`.streamlit/secrets.toml` içinde GITHUB_TOKEN tanımlı değil - ayarlar kaydedilemez.")
        return

    existing = load_stop_loss_settings(username)
    shared = existing.get("shared") or {}
    algo1 = existing.get("breakeven_atr_structure") or {}
    algo2 = existing.get("wait_then_trail") or {}
    algo3 = existing.get("opening_range") or {}
    algo4 = existing.get("heikin_ashi_exit") or {}
    algo5 = existing.get("atr_volatility") or {}
    algo6 = existing.get("adaptive_dynamic") or {}
    execution = {**EXECUTION_DEFAULTS, **(existing.get("execution") or {})}

    usage = _live_stop_usage(username)
    live_lines = [f"- **{STOP_ALGORITHMS[a].label}:** {', '.join(u)}" for a, u in usage.items() if u]
    st.markdown("**🟢 Canlıda kullanılan stop algoritmaları**")
    st.markdown("\n".join(live_lines) if live_lines else "- Kaydedilmiş modül ayarı bulunamadı.")
    st.caption("🌅 Emir Yürütme (Açılış Kalkanı) ayarları seçilen algoritmadan bağımsız olarak tüm canlı "
               "modüllere uygulanır.")

    # ATR/swing/trend ayarlarını kullanan algoritmalar - paylaşılan sekme bunlardan biri canlıysa 🟢.
    shared_users = sorted({u for a in ("breakeven_atr_structure", "wait_then_trail", "opening_range",
                                       "heikin_ashi_exit", "atr_volatility", "adaptive_dynamic") for u in usage.get(a, [])})
    (tab_algo1, tab_shared, tab_algo2, tab_algo3, tab_algo4, tab_algo5, tab_algo6, tab_exec) = st.tabs([
        _tab_label("breakeven_atr_structure", "🎯", usage),
        f"{'🟢 ' if shared_users else ''}🔧 Paylaşılan Trail Ayarları",
        _tab_label("wait_then_trail", "⏳", usage),
        _tab_label("opening_range", "🔓", usage),
        _tab_label("heikin_ashi_exit", "🕯️", usage),
        _tab_label("atr_volatility", "📏", usage),
        _tab_label("adaptive_dynamic", "🧠", usage),
        "🟢 🌅 Emir Yürütme",
    ])

    with tab_algo1:
        _live_note("breakeven_atr_structure", usage)
        st.caption("Sabit yüzdelik ilk stop → breakeven → ATR-tamponlu yapısal trail (ayarları "
                   "🔧 Paylaşılan Trail Ayarları sekmesinde).")
        a1c1, a1c2 = st.columns(2)
        algo1_initial_stop_pct = a1c1.number_input(
            "İlk Stop %", min_value=0.1, max_value=50.0,
            value=float(algo1.get("initial_stop_pct", INITIAL_STOP_PCT * 100)), step=0.1, format="%.2f",
            key="sls_algo1_initial_stop_pct",
            help="Pozisyon açıldığında ilk stop, ortalama maliyetin bu yüzde kadar altına (long) / "
                 "üstüne (short) kurulur.",
        )
        algo1_breakeven_trigger_pct = a1c2.number_input(
            "Breakeven Tetik %", min_value=0.0, max_value=50.0,
            value=float(algo1.get("breakeven_trigger_pct", BREAKEVEN_TRIGGER_PCT * 100)), step=0.1, format="%.2f",
            key="sls_algo1_breakeven_trigger_pct",
            help="Fiyat ortalama maliyetin bu yüzde kadar lehine hareket ettiğinde, stop ortalama "
                 "maliyete (breakeven) çekilir.",
        )

        algo1_breakeven_buffer_pct = a1c1.number_input(
            "Breakeven Tamponu %", min_value=0.0, max_value=5.0,
            value=float(algo1.get("breakeven_buffer_pct", BREAKEVEN_BUFFER_PCT * 100)), step=0.05, format="%.2f",
            key="sls_algo1_breakeven_buffer_pct",
            help="[2026-09-28] Breakeven stopu tam giriş fiyatına değil, girişin bu yüzde kadar üstüne "
                 "kurulur - açılış boşluğunda piyasa fiyatından dolan stop 'başa baş'ı zarara çevirmesin. "
                 "Beklemeli ve İz Süren Stop ile Heikin Ashi Çıkışı da bu değeri kullanır.",
        )

    with tab_shared:
        if shared_users:
            st.success("🟢 **Canlı botları etkiler** - bu ayarlar yapısal trail kullanan tüm algoritmalarda "
                       "geçerli (kullanan modüller: " + ", ".join(shared_users) + ").")
        st.caption("Breakeven + Yapısal Trail'in trail aşaması; Beklemeli, ORB ve Heikin Ashi stopları da aynı "
                   "yapısal trail'i, Oynaklık (ATR) Stop ise yalnızca ATR Periyodu'nu kullanır.")
        sc1, sc2, sc3 = st.columns(3)
        atr_period = sc1.number_input(
            "ATR Periyodu (bar)", min_value=2, max_value=200,
            value=int(shared.get("atr_period", ATR_PERIOD)), step=1, key="sls_atr_period",
            help="Average True Range hesaplanırken geriye kaç bar bakılacağı.",
        )
        atr_multiplier = sc2.number_input(
            "ATR Çarpanı", min_value=0.0, max_value=10.0,
            value=float(shared.get("atr_multiplier", ATR_MULTIPLIER)), step=0.05, format="%.2f",
            key="sls_atr_multiplier",
            help="Yapısal trail seviyesine eklenen tampon = ATR × bu çarpan.",
        )
        swing_order = sc3.number_input(
            "Swing/Pivot Derinliği (bar)", min_value=1, max_value=20,
            value=int(shared.get("swing_order", SWING_ORDER)), step=1, key="sls_swing_order",
            help="Bir tepe/dibin pivot sayılması için her iki yanında en az bu kadar bar gerekir.",
        )

        sc4, sc5, sc6 = st.columns(3)
        stale_reference_days = sc4.number_input(
            "Referans Geçerlilik Süresi (gün)", min_value=0.0, max_value=365.0,
            value=float(shared.get("stale_reference_days", STALE_REFERENCE_DAYS)), step=1.0,
            key="sls_stale_reference_days",
            help="Yapısal referans noktası bu kadar gündür kırılmadıysa, analiz sadece son bu kadar "
                 "günlük pencereye daraltılarak yeniden yapılır.",
        )
        trend_ema_period = sc5.number_input(
            "Trend EMA Periyodu (0 = kapalı)", min_value=0, max_value=500,
            value=int(shared.get("trend_ema_period", TREND_EMA_PERIOD)), step=1, key="sls_trend_ema_period",
            help="Günlük EMA trend filtresi periyodu - yapısal trail'in devreye girmesi için fiyatın bu "
                 "EMA'nın doğru tarafında olması gerekir. 0 girilirse filtre tamamen kapanır.",
        )
        fallback_buffer_pct = sc6.number_input(
            "Yedek Tampon % (ATR hesaplanamazsa)", min_value=0.0, max_value=10.0,
            value=float(shared.get("fallback_buffer_pct", FALLBACK_BUFFER_PCT * 100)), step=0.01, format="%.3f",
            key="sls_fallback_buffer_pct",
            help="Henüz yeterli bar yoksa ATR hesaplanamaz - bu durumda tampon, pivot fiyatının bu "
                 "yüzdesi olarak hesaplanır.",
        )

    with tab_algo2:
        _live_note("wait_then_trail", usage)
        st.caption("Sabit yüzdelik ilk stop + breakeven; kâr eşiği bir kez aşılınca kâr kilidi ve paylaşılan "
                   "yapısal trail devreye girer.")
        a2c1, a2c2 = st.columns(2)
        algo2_initial_stop_pct = a2c1.number_input(
            "İlk Stop %", min_value=0.1, max_value=50.0,
            value=float(algo2.get("initial_stop_pct", WAIT_THEN_TRAIL_INITIAL_STOP_PCT * 100)), step=0.1,
            format="%.2f", key="sls_algo2_initial_stop_pct",
            help="Pozisyon açıldığında ilk stop, ortalama maliyetin bu yüzde kadar altına kurulur.",
        )
        algo2_breakeven_trigger_pct = a2c2.number_input(
            "Breakeven Tetik %", min_value=0.0, max_value=50.0,
            value=float(algo2.get("breakeven_trigger_pct", WAIT_THEN_TRAIL_BREAKEVEN_TRIGGER_PCT * 100)), step=0.1,
            format="%.2f", key="sls_algo2_breakeven_trigger_pct",
            help="Fiyat ortalama maliyetin bu yüzde kadar lehine hareket ettiğinde, stop ortalama "
                 "maliyete (breakeven) çekilir.",
        )
        a2c3, a2c4 = st.columns(2)
        algo2_profit_lock_trigger_pct = a2c3.number_input(
            "Kâr Kilidi Tetik %", min_value=0.1, max_value=100.0,
            value=float(algo2.get("profit_lock_trigger_pct", WAIT_THEN_TRAIL_PROFIT_LOCK_TRIGGER_PCT * 100)),
            step=0.1, format="%.2f", key="sls_algo2_profit_lock_trigger_pct",
            help="Fiyat, giriş fiyatının bu yüzde kadar lehine bir kez ulaştığında (sonradan geri çekilse "
                 "bile kalıcı olarak) kâr kilidi ve yapısal trail devreye girer.",
        )
        algo2_profit_lock_pct = a2c4.number_input(
            "Kâr Kilidi Seviyesi %", min_value=0.0, max_value=100.0,
            value=float(algo2.get("profit_lock_pct", WAIT_THEN_TRAIL_PROFIT_LOCK_PCT * 100)), step=0.1,
            format="%.2f", key="sls_algo2_profit_lock_pct",
            help="Kâr kilidi tetiklendiğinde stop, ortalama maliyetin bu yüzde kadar üzerine (kâr) kilitlenir.",
        )

    with tab_algo3:
        _live_note("opening_range", usage)
        st.caption("ORB alım sinyaliyle kullanılır: ilk stop açılış barının low'unun biraz altına kurulur, trail "
                   "paylaşılan yapısal trail'dir ama kendi trend EMA ayarıyla (varsayılan kapalı).")
        a3c1, a3c2 = st.columns(2)
        algo3_buffer_pct = a3c1.number_input(
            "Açılış Aralığı Tamponu %", min_value=0.0, max_value=10.0,
            value=float(algo3.get("buffer_pct", ORB_STOP_BUFFER_PCT * 100)), step=0.05, format="%.3f",
            key="sls_algo3_buffer_pct",
            help="Stop, açılış barının low'unun (long için) bu yüzde kadar altına kurulur - tam seviyede "
                 "kalmak yerine küçük bir nefes payı bırakır.",
        )
        algo3_fallback_pct = a3c2.number_input(
            "Yedek Stop % (açılış barı bulunamazsa)", min_value=0.1, max_value=50.0,
            value=float(algo3.get("fallback_pct", INITIAL_STOP_PCT * 100)), step=0.1, format="%.2f",
            key="sls_algo3_fallback_pct",
            help="`bars` verilmediği bazı çağrı yollarında (ör. bazı fallback/top-up senaryoları) ya da "
                 "hesaplanan seviye girişin yanlış tarafında kaldığında, bu sabit yüzdeye güvenli şekilde "
                 "geri düşülür.",
        )
        algo3_trend_ema_period = a3c1.number_input(
            "Trend EMA Periyodu (0 = kapalı, ORB varsayılanı)", min_value=0, max_value=500,
            value=int(algo3.get("trend_ema_period", ORB_TREND_EMA_PERIOD)), step=1,
            key="sls_algo3_trend_ema_period",
            help="ORB'un yapısal trail'i için AYRI günlük EMA trend filtresi - yukarıdaki paylaşılan Trend EMA "
                 "Periyodu'ndan bağımsızdır. Varsayılan olarak 0 (kapalı): ORB gün-içi bir kırılım stratejisi "
                 "olduğu için günlük trendin doğru tarafında olma şartı aranmaz, sadece 15dk'lık swing-low "
                 "yapısı temel alınır. Açık bırakılırsa (>0), trend uygun olmadığında yapısal trail tamamen "
                 "devre dışı kalıp pozisyon sadece breakeven'de korunur.",
        )

    with tab_algo4:
        _live_note("heikin_ashi_exit", usage)
        st.caption("İlk stop sinyal barının low'unun altında; ilk kırmızı HA mumunda ya da Stokastik dönüşünde "
                   "stop son kapanışın altına çekilir. Sinyal yokken paylaşılan yapısal trail geçerlidir.")
        a4c1, a4c2, a4c3 = st.columns(3)
        algo4_buffer_pct = a4c1.number_input(
            "Sinyal Barı Low Tamponu %", min_value=0.0, max_value=10.0,
            value=float(algo4.get("buffer_pct", HEIKIN_ASHI_STOP_BUFFER_PCT * 100)), step=0.05, format="%.3f",
            key="sls_algo4_buffer_pct",
        )
        algo4_exit_buffer_pct = a4c2.number_input(
            "Çıkış Tamponu % (kapanışın altı)", min_value=0.0, max_value=10.0,
            value=float(algo4.get("exit_buffer_pct", HEIKIN_ASHI_EXIT_BUFFER_PCT * 100)), step=0.05, format="%.3f",
            key="sls_algo4_exit_buffer_pct",
        )
        algo4_fallback_pct = a4c3.number_input(
            "Yedek Stop % (sinyal barı yoksa)", min_value=0.1, max_value=50.0,
            value=float(algo4.get("fallback_pct", INITIAL_STOP_PCT * 100)), step=0.1, format="%.2f",
            key="sls_algo4_fallback_pct",
        )
        algo4_min_atr_mult = st.number_input(
            "İlk Stop Oynaklık Tabanı (ATR çarpanı)", min_value=0.0, max_value=5.0,
            value=float(algo4.get("min_atr_mult", HEIKIN_ASHI_MIN_ATR_MULT)), step=0.25, format="%.2f",
            key="sls_algo4_min_atr_mult",
            help="İlk stop girişten en az bu kadar ATR uzakta olur (ATR, sinyalin kendi mum periyodundan ve "
                 "yukarıdaki paylaşılan ATR Periyodu ile hesaplanır). Sinyal barının low'u bundan daha uzaksa "
                 "o kullanılır - taban sadece genişletir. 0 = kapalı (sadece sinyal barının low'u; eski davranış).",
        )

    with tab_algo5:
        _live_note("atr_volatility", usage)
        st.caption("İlk stop = giriş − (ATR Çarpanı × ATR); 1R = giriş − ilk stop. Kapanış giriş + Breakeven R'yi "
                   "geçince stop breakeven'e, en yüksek fiyat Trail Başlangıcı R'ye ulaşınca chandelier trail "
                   "başlar. ATR Periyodu paylaşılan sekmeden gelir.")
        a5c1, a5c2, a5c3 = st.columns(3)
        algo5_initial_atr_mult = a5c1.number_input(
            "İlk Stop ATR Çarpanı", min_value=0.5, max_value=10.0,
            value=float(algo5.get("initial_atr_mult", ATR_VOL_INITIAL_ATR_MULT)), step=0.25, format="%.2f",
            key="sls_algo5_initial_atr_mult",
            help="İlk stop = giriş − bu çarpan × ATR. Varsayılan 1.5. Premium Buy Point'te ATR, hissenin giriş periyodundan; Relative Strength Rotasyonu'nda günlük barlardan hesaplanır.",
        )
        algo5_max_stop_pct = a5c2.number_input(
            "Maksimum Stop Mesafesi %", min_value=1.0, max_value=50.0,
            value=float(algo5.get("max_stop_pct", ATR_VOL_MAX_STOP_PCT * 100)), step=0.5, format="%.1f",
            key="sls_algo5_max_stop_pct",
            help="Çok oynak hisselerde ATR stopu bu yüzdeden daha uzağa kurulmaz.",
        )
        algo5_fallback_pct = a5c3.number_input(
            "Yedek Stop % (ATR hesaplanamazsa)", min_value=0.1, max_value=50.0,
            value=float(algo5.get("fallback_pct", ATR_VOL_FALLBACK_PCT * 100)), step=0.1, format="%.2f",
            key="sls_algo5_fallback_pct",
        )
        a5c4, a5c5, a5c6, a5c7 = st.columns(4)
        algo5_breakeven_r = a5c4.number_input(
            "Breakeven Tetiği (R)", min_value=0.25, max_value=10.0,
            value=float(algo5.get("breakeven_r", ATR_VOL_BREAKEVEN_R)), step=0.25, format="%.2f",
            key="sls_algo5_breakeven_r",
            help="Bir bar KAPANIŞI giriş + bu kadar R'yi geçince stop breakeven'e çekilir.",
        )
        algo5_breakeven_buffer_atr = a5c5.number_input(
            "Breakeven Tamponu (×ATR)", min_value=0.0, max_value=2.0,
            value=float(algo5.get("breakeven_buffer_atr", ATR_VOL_BREAKEVEN_BUFFER_ATR)), step=0.05, format="%.2f",
            key="sls_algo5_breakeven_buffer_atr",
        )
        algo5_trail_start_r = a5c6.number_input(
            "Trail Başlangıcı (R)", min_value=0.5, max_value=20.0,
            value=float(algo5.get("trail_start_r", ATR_VOL_TRAIL_START_R)), step=0.25, format="%.2f",
            key="sls_algo5_trail_start_r",
        )
        algo5_trail_atr_mult = a5c7.number_input(
            "Trail ATR Çarpanı", min_value=0.5, max_value=10.0,
            value=float(algo5.get("trail_atr_mult", ATR_VOL_TRAIL_ATR_MULT)), step=0.25, format="%.2f",
            key="sls_algo5_trail_atr_mult",
            help="Chandelier trail: girişten beri en yüksek fiyat − bu çarpan × ATR.",
        )
        st.caption("Stop GÜNLÜK barlarda izleniyorsa (ör. 1 Gün periyotlu Premium Buy Point hisseleri) "
                   "yukarıdaki iki trail ayarı yerine aşağıdakiler kullanılır.")
        a5c8, a5c9 = st.columns(2)
        algo5_daily_trail_start_r = a5c8.number_input(
            "Günlük: Trail Başlangıcı (R)", min_value=0.5, max_value=20.0,
            value=float(algo5.get("daily_trail_start_r", ATR_VOL_DAILY_TRAIL_START_R)), step=0.25, format="%.2f",
            key="sls_algo5_daily_trail_start_r",
        )
        algo5_daily_trail_atr_mult = a5c9.number_input(
            "Günlük: Trail ATR Çarpanı", min_value=0.5, max_value=10.0,
            value=float(algo5.get("daily_trail_atr_mult", ATR_VOL_DAILY_TRAIL_ATR_MULT)), step=0.25, format="%.2f",
            key="sls_algo5_daily_trail_atr_mult",
        )

    with tab_algo6:
        _live_note("adaptive_dynamic", usage)
        st.caption("İlk stop girişin altındaki son swing low'a (tampon payıyla) kurulur, mesafe Min-Maks ATR "
                   "bandına sıkıştırılır (swing low yoksa İlk Stop ATR Çarpanı). En yüksek fiyat Trail "
                   "Başlangıcı R'ye ulaşınca chandelier trail başlar; çarpanı ötesindeki her R için Daralma kadar "
                   "azalır (Min. Trail Çarpanı'na kadar) ve trend verimliliğine (ER) göre ayarlanır. ATR "
                   "Periyodu ve Swing Order paylaşılan sekmeden gelir. Doğrulama: "
                   "`scripts/backtest_adaptive_stop.py`.")
        a6c1, a6c2, a6c3, a6c4 = st.columns(4)
        algo6_initial_atr_mult = a6c1.number_input(
            "İlk Stop ATR Çarpanı (swing yoksa)", min_value=0.5, max_value=10.0,
            value=float(algo6.get("initial_atr_mult", ADAPTIVE_INITIAL_ATR_MULT)), step=0.25, format="%.2f",
            key="sls_algo6_initial_atr_mult",
        )
        algo6_min_atr_mult = a6c2.number_input(
            "İlk Stop Min. Mesafe (×ATR)", min_value=0.25, max_value=10.0,
            value=float(algo6.get("min_atr_mult", ADAPTIVE_MIN_ATR_MULT)), step=0.25, format="%.2f",
            key="sls_algo6_min_atr_mult",
            help="Swing low girişe bundan yakınsa stop bu mesafeye genişletilir (gürültüde patlamasın).",
        )
        algo6_max_atr_mult = a6c3.number_input(
            "İlk Stop Maks. Mesafe (×ATR)", min_value=0.5, max_value=10.0,
            value=float(algo6.get("max_atr_mult", ADAPTIVE_MAX_ATR_MULT)), step=0.25, format="%.2f",
            key="sls_algo6_max_atr_mult",
            help="Swing low bundan uzaksa stop bu mesafeye daraltılır (risk şişmesin).",
        )
        algo6_structure_buffer_atr = a6c4.number_input(
            "Swing Low Tamponu (×ATR)", min_value=0.0, max_value=2.0,
            value=float(algo6.get("structure_buffer_atr", ADAPTIVE_STRUCTURE_BUFFER_ATR)), step=0.05,
            format="%.2f", key="sls_algo6_structure_buffer_atr",
        )
        a6c5, a6c6, a6c7, a6c8 = st.columns(4)
        algo6_trail_start_r = a6c5.number_input(
            "Trail Başlangıcı (R)", min_value=0.25, max_value=20.0,
            value=float(algo6.get("trail_start_r", ADAPTIVE_TRAIL_START_R)), step=0.25, format="%.2f",
            key="sls_algo6_trail_start_r",
        )
        algo6_trail_atr_mult = a6c6.number_input(
            "Trail ATR Çarpanı (başlangıç)", min_value=0.5, max_value=15.0,
            value=float(algo6.get("trail_atr_mult", ADAPTIVE_TRAIL_ATR_MULT)), step=0.25, format="%.2f",
            key="sls_algo6_trail_atr_mult",
        )
        algo6_tighten_per_r = a6c7.number_input(
            "Daralma (her R için ×ATR)", min_value=0.0, max_value=3.0,
            value=float(algo6.get("tighten_per_r", ADAPTIVE_TIGHTEN_PER_R)), step=0.05, format="%.2f",
            key="sls_algo6_tighten_per_r",
            help="Kâr trail başlangıcının her 1R ötesine geçtiğinde chandelier çarpanı bu kadar azalır.",
        )
        algo6_min_trail_atr_mult = a6c8.number_input(
            "Min. Trail Çarpanı", min_value=0.5, max_value=10.0,
            value=float(algo6.get("min_trail_atr_mult", ADAPTIVE_MIN_TRAIL_ATR_MULT)), step=0.25, format="%.2f",
            key="sls_algo6_min_trail_atr_mult",
        )
        a6c9, a6c10 = st.columns(2)
        algo6_er_weight = a6c9.number_input(
            "Trend Verimliliği Ağırlığı (ER)", min_value=0.0, max_value=2.0,
            value=float(algo6.get("er_weight", ADAPTIVE_ER_WEIGHT)), step=0.1, format="%.2f",
            key="sls_algo6_er_weight",
            help="Çarpan × (1 + ağırlık × (ER − 0.5)): temiz trendde geniş, testerede dar. 0 = kapalı "
                 "(doğrulama taramasında katkısı ölçülemedi).",
        )
        algo6_breakeven_r = a6c10.number_input(
            "Breakeven Tetiği (R, 0 = kapalı)", min_value=0.0, max_value=10.0,
            value=float(algo6.get("breakeven_r", ADAPTIVE_BREAKEVEN_R)), step=0.25, format="%.2f",
            key="sls_algo6_breakeven_r",
            help="Doğrulama taramasında 1R/2R breakeven test döneminde sonucu kötüleştirdi - varsayılan kapalı.",
        )

    with tab_exec:
        st.success("🟢 **Canlı botları etkiler** - seçilen stop algoritmasından bağımsız olarak tüm modüllerde "
                   "(PBP, ORB, RS, HA) geçerlidir.")
        st.caption("Kalkan açıkken seans dışında stop, asıl seviyenin altındaki bir felaket stopuna çekilir; "
                   "açılıştan belirtilen dakika sonra asıl seviyeye döner (fiyat altındaysa market emriyle çıkılır).")
        e1, e2, e3, e4 = st.columns(4)
        exec_shield_enabled = e1.checkbox(
            "Açılış kalkanı açık", value=bool(execution["opening_shield_enabled"]), key="sls_exec_shield_enabled",
        )
        exec_shield_minutes = e2.number_input(
            "Kalkan süresi (dk)", min_value=1, max_value=120, value=int(execution["opening_shield_minutes"]),
            step=1, key="sls_exec_shield_minutes",
        )
        exec_disaster_pct = e3.number_input(
            "Felaket stopu mesafesi %", min_value=0.5, max_value=30.0,
            value=float(execution["shield_disaster_pct"]), step=0.5, format="%.1f", key="sls_exec_disaster_pct",
            help="Seans dışında stop, asıl seviyenin bu yüzde kadar altına çekilir.",
        )
        exec_ext_trail = e4.checkbox(
            "Seans dışında trail yap", value=bool(execution["extended_hours_trail_enabled"]), key="sls_exec_ext_trail",
            help="Eski davranış: düşük hacimli pre-market/after-hours barlarıyla stopu sıkılaştırır. Analizde "
                 "bu, stopun açılışta tetiklenmesine yol açtığı için varsayılan olarak kapalı. Kalkan açıkken "
                 "etkisizdir.",
        )

    with st.expander("📝 Sürüm notları"):
        st.markdown(
            "**2026-09-28 (emir analizi önerileri 1-3):** Breakeven tetiği %1'den %1.5'e (=1R) çıkarıldı, "
            "breakeven stopu girişin %0.2 üstüne kuruluyor; seans dışında açılış kalkanı devrede (emir "
            "analizinde 17 çıkışın 7'si açılışın ilk 5 dakikasında, 3'ü seans dışında gerçekleşmişti). "
            "Oynaklık (ATR) Stop seçenek olarak eklendi. Hangi algoritmanın şu an canlıda kullanıldığı için "
            "yukarıdaki 🟢 listeye bakın. Gerekçeler ve takip ölçütleri: **🧠 Algo Analiz > 📝 Değişiklik "
            "Günlüğü**."
        )

    st.divider()
    if st.button("💾 Kaydet", type="primary", key="sls_save_btn"):
        new_settings = {
            "shared": {
                "atr_period": int(atr_period),
                "atr_multiplier": float(atr_multiplier),
                "stale_reference_days": float(stale_reference_days),
                "trend_ema_period": int(trend_ema_period),
                "swing_order": int(swing_order),
                "fallback_buffer_pct": float(fallback_buffer_pct),
            },
            "breakeven_atr_structure": {
                "initial_stop_pct": float(algo1_initial_stop_pct),
                "breakeven_trigger_pct": float(algo1_breakeven_trigger_pct),
                "breakeven_buffer_pct": float(algo1_breakeven_buffer_pct),
            },
            "wait_then_trail": {
                "breakeven_buffer_pct": float(algo1_breakeven_buffer_pct),
                "initial_stop_pct": float(algo2_initial_stop_pct),
                "breakeven_trigger_pct": float(algo2_breakeven_trigger_pct),
                "profit_lock_trigger_pct": float(algo2_profit_lock_trigger_pct),
                "profit_lock_pct": float(algo2_profit_lock_pct),
            },
            "opening_range": {
                "buffer_pct": float(algo3_buffer_pct),
                "fallback_pct": float(algo3_fallback_pct),
                "trend_ema_period": int(algo3_trend_ema_period),
            },
            "heikin_ashi_exit": {
                "buffer_pct": float(algo4_buffer_pct),
                "exit_buffer_pct": float(algo4_exit_buffer_pct),
                "fallback_pct": float(algo4_fallback_pct),
                "min_atr_mult": float(algo4_min_atr_mult),
                "breakeven_buffer_pct": float(algo1_breakeven_buffer_pct),
            },
            "atr_volatility": {
                "initial_atr_mult": float(algo5_initial_atr_mult),
                "max_stop_pct": float(algo5_max_stop_pct),
                "fallback_pct": float(algo5_fallback_pct),
                "breakeven_r": float(algo5_breakeven_r),
                "breakeven_buffer_atr": float(algo5_breakeven_buffer_atr),
                "trail_start_r": float(algo5_trail_start_r),
                "trail_atr_mult": float(algo5_trail_atr_mult),
                "daily_trail_start_r": float(algo5_daily_trail_start_r),
                "daily_trail_atr_mult": float(algo5_daily_trail_atr_mult),
            },
            "adaptive_dynamic": {
                "initial_atr_mult": float(algo6_initial_atr_mult),
                "min_atr_mult": float(algo6_min_atr_mult),
                "max_atr_mult": float(algo6_max_atr_mult),
                "structure_buffer_atr": float(algo6_structure_buffer_atr),
                "trail_start_r": float(algo6_trail_start_r),
                "trail_atr_mult": float(algo6_trail_atr_mult),
                "tighten_per_r": float(algo6_tighten_per_r),
                "min_trail_atr_mult": float(algo6_min_trail_atr_mult),
                "er_weight": float(algo6_er_weight),
                "breakeven_r": float(algo6_breakeven_r),
            },
            "execution": {
                "opening_shield_enabled": bool(exec_shield_enabled),
                "opening_shield_minutes": int(exec_shield_minutes),
                "shield_disaster_pct": float(exec_disaster_pct),
                "extended_hours_trail_enabled": bool(exec_ext_trail),
            },
        }
        try:
            save_stop_loss_settings(username, new_settings)
            st.success("Stop loss ayarları kaydedildi.")
            st.rerun()
        except Exception as e:
            st.error(f"Kaydedilemedi: {e}")
