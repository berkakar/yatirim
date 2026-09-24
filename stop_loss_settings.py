"""Stop Loss Ayarları modülü - üç stop-loss algoritmasının (stop_algorithms.py)
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

from github_config import read_json_from_github, write_json_to_github
from stop_algorithms import (
    ATR_MULTIPLIER, ATR_PERIOD, BREAKEVEN_TRIGGER_PCT, FALLBACK_BUFFER_PCT, INITIAL_STOP_PCT,
    ORB_STOP_BUFFER_PCT, STALE_REFERENCE_DAYS, STOP_ALGORITHMS, SWING_ORDER, TREND_EMA_PERIOD,
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
    token = st.secrets.get("GITHUB_TOKEN")
    if not token:
        return {}
    try:
        return read_json_from_github(GITHUB_REPO, token, _settings_path(username), {})
    except Exception:
        return {}


def save_stop_loss_settings(username: str, settings: dict) -> None:
    token = st.secrets["GITHUB_TOKEN"]
    write_json_to_github(
        GITHUB_REPO, token, _settings_path(username), settings, f"Update stop-loss settings ({username})",
    )


def render_stop_loss_settings(username: str):
    st.caption(
        "Trailing Stop modülünün, Premium Buy Point'in bracket girişlerinin ve BackTest'in kullandığı "
        "üç stop-loss algoritmasının parametrelerini burada ayarlayabilirsiniz. Aşağıdaki kutular kod "
        "içindeki varsayılan değerlerle dolu geliyor - hiç değiştirmeden kaydetseniz bile mevcut davranış "
        "aynen korunur. Bir kutuyu boşaltıp tekrar kod-varsayılanına dönmek isterseniz, değeri elle "
        "yukarıdaki varsayılana geri yazmanız yeterli."
    )

    if not st.secrets.get("GITHUB_TOKEN"):
        st.warning("`.streamlit/secrets.toml` içinde GITHUB_TOKEN tanımlı değil - ayarlar kaydedilemez.")
        return

    existing = load_stop_loss_settings(username)
    shared = existing.get("shared") or {}
    algo1 = existing.get("breakeven_atr_structure") or {}
    algo2 = existing.get("wait_then_trail") or {}
    algo3 = existing.get("opening_range") or {}

    st.subheader(f"🎯 {STOP_ALGORITHMS['breakeven_atr_structure'].label}")
    st.caption(
        "Sabit yüzdelik ilk stop, ardından breakeven'e çekilme ve ATR-tamponlu break-of-structure "
        "trail. Buradaki yapısal trail ayarları, Beklemeli ve İz Süren Stop'un kâr kilidinden sonra "
        "devreye giren trail için de geçerlidir - o algoritma bu bölümdeki yapısal trail'i aynen kullanır."
    )
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

    st.divider()
    st.subheader(f"⏳ {STOP_ALGORITHMS['wait_then_trail'].label}")
    st.caption(
        "Sabit yüzdelik ilk stop + breakeven, sonra kâr eşiğine ulaşana kadar (yapısal trail olmadan) "
        "bekleme; eşik bir kez aşıldığında (kalıcı olarak) kâr kilidi ve yukarıdaki Breakeven + Yapısal "
        "Trail bölümünde ayarladığınız yapısal trail devreye girer - bu algoritmanın kendi ayrı bir "
        "yapısal trail ayarı yoktur."
    )
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

    st.divider()
    st.subheader(f"🔓 {STOP_ALGORITHMS['opening_range'].label}")
    st.caption(
        "buy_algorithms.orb_signal (Açılış Aralığı Kırılımı) ile birlikte kullanılmak üzere tasarlandı - "
        "ilk stop, sabit bir yüzde yerine pozisyonun açıldığı seansın açılış barının ters ucuna (long için "
        "low) küçük bir tamponla kurulur. Bu bölümün kendi ayrı bir trail ayarı yoktur - eşik aşıldıktan "
        "sonra yukarıdaki Breakeven + Yapısal Trail bölümündeki (paylaşılan ATR/trend/swing) aynı yapısal "
        "trail devreye girer. Açılış barı bulunamazsa (ör. ORB dışı bir algoritmanın sinyaliyle bu stop "
        "seçildiyse) aşağıdaki Yedek Stop % kullanılır."
    )
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
            },
            "wait_then_trail": {
                "initial_stop_pct": float(algo2_initial_stop_pct),
                "breakeven_trigger_pct": float(algo2_breakeven_trigger_pct),
                "profit_lock_trigger_pct": float(algo2_profit_lock_trigger_pct),
                "profit_lock_pct": float(algo2_profit_lock_pct),
            },
            "opening_range": {
                "buffer_pct": float(algo3_buffer_pct),
                "fallback_pct": float(algo3_fallback_pct),
            },
        }
        try:
            save_stop_loss_settings(username, new_settings)
            st.success("Stop loss ayarları kaydedildi.")
            st.rerun()
        except Exception as e:
            st.error(f"Kaydedilemedi: {e}")
