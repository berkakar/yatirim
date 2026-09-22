"""BackTest modülü - premium buy-point algoritmalarını (buy_algorithms.py)
ve Alpaca'daki structure-based trailing stop'u (alpaca_trailing_stop.py)
tek bir hisse üzerinde geçmiş veriyle yeniden oynatır (bkz.
backtest_engine.py - aynı karar fonksiyonlarını, aynı parametrelerle
kullanır, böylece backtest sonucu canlı sistemin gerçekte ne yapacağını
yansıtır). Sonuçlar backtest_data.py ile kalıcı olarak saklanır ve
algoritma bazlı sekmelerde, önceki çalıştırmalarla birlikte gösterilir -
her yeni çalıştırma eklenir, öncekiler hiç silinmez.
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from alpaca_client import AlpacaClient
from alpaca_trailing_stop import get_bars_for_timeframe
from backtest_data import append_results, group_by_algorithm, load_results, new_run_id
from backtest_engine import run_backtest
from bicak_kanali import find_kilavuz
from bicak_kanali_test import render_bicak_kanali_chart
from buy_algorithms import ALGORITHMS
from stop_algorithms import DEFAULT_STOP_ALGORITHM, STOP_ALGORITHMS
from stop_loss_settings import load_stop_loss_settings
from structure import Bar
from theme import get_plotly_template, negative_color, positive_color
from ui_style import zebra_style, freshness_caption

TIMEFRAMES = ["15Min", "30Min", "1Hour", "1Day"]
TIMEFRAME_LABELS = {"15Min": "15 Dakika", "30Min": "30 Dakika", "1Hour": "1 Saat", "1Day": "1 Gün"}
STOP_TIMEFRAME_SAME_AS_ENTRY = "__same__"  # "Alım mumuyla aynı" - run_backtest'e stop_bars/stop_timeframe hiç geçirilmez
DAILY_TREND_LOOKBACK_DAYS = 400  # trend_pullback SMA200 + trend filtresi için yeterli pay

# Grafik üzerindeki mum/işaretçi renkleri kasıtlı olarak sabit: gerçek alım-satım
# terminallerinde (TradingView vb.) yükseliş/düşüş rengi gündüz/gece temasından
# bağımsızdır. valuation.py / tefas_fonlari.py ile aynı palet.
_POSITIVE_HEX = "#2ec4b6"
_NEGATIVE_HEX = "#e63946"
_BUY_MARKER_COLOR = "#FFFFFF"   # işlem detay grafiğinde alım zamanı
_SELL_MARKER_COLOR = "#800000"  # işlem detay grafiğinde satım zamanı (bordo)


# Tablo hücrelerindeki K/Z metin rengi ise sayfa arka planı üzerinde okunurluk
# için gündüz/gece moduna göre değişir - çağrı anında hesaplanır.
def _positive_text_style():
    return f"color: {positive_color()}; font-weight: bold;"


def _negative_text_style():
    return f"color: {negative_color()}; font-weight: bold;"


def _side_style(side):
    return {"Alış": _positive_text_style(), "Satış": _negative_text_style()}.get(side, "")

_SUMMARY_COLUMN_CONFIG = {
    "Başlangıç Bütçe": st.column_config.NumberColumn(format="localized"),
    "Bitiş Değeri": st.column_config.NumberColumn(format="localized"),
    "K/Z": st.column_config.NumberColumn(format="localized"),
    "K/Z %": st.column_config.NumberColumn(format="%.2f%%"),
    "Veri (gün)": st.column_config.NumberColumn(format="%d"),
    "İşlem Başlangıcı (gün)": st.column_config.NumberColumn(format="%d"),
    "İşlem Sayısı": st.column_config.NumberColumn(format="%d"),
}
_TRADES_COLUMN_CONFIG = {
    "Fiyat": st.column_config.NumberColumn(format="%.4f"),
    "Adet": st.column_config.NumberColumn(format="localized"),
}


def _format_stop_loss(r: dict) -> str:
    if not r.get("stop_loss_enabled"):
        return "Kapalı"
    limit = r.get("max_loss_pct")
    if r.get("stop_loss_triggered"):
        return f"Tetiklendi (%{limit:g}) · {r.get('stop_loss_triggered_at') or ''}"
    return f"Açık (%{limit:g})"


def _stop_algorithm_label(r: dict) -> str:
    # Eski kayıtlarda stop_algorithm alanı yok - o alan eklenmeden önce
    # üretilen tüm sonuçlar zaten DEFAULT_STOP_ALGORITHM ile koşmuştu (bkz.
    # backtest_engine.run_backtest'in aynı varsayılanı).
    algo_id = r.get("stop_algorithm") or DEFAULT_STOP_ALGORITHM
    algo = STOP_ALGORITHMS.get(algo_id)
    return algo.label if algo else algo_id


def _style_summary(df: pd.DataFrame):
    """K/Z, K/Z % ve Zarar Kes sütunlarını renklendirir, üzerine
    zebra_style'ın satır bandını uygular."""
    def apply_styles(data):
        style_df = pd.DataFrame("", index=data.index, columns=data.columns)
        for col in ("K/Z", "K/Z %"):
            if col not in data.columns:
                continue
            for idx in data.index:
                v = data.loc[idx, col]
                if pd.notna(v):
                    style_df.loc[idx, col] = _positive_text_style() if v > 0 else (_negative_text_style() if v < 0 else "")
        if "Zarar Kes" in data.columns:
            for idx in data.index:
                if str(data.loc[idx, "Zarar Kes"]).startswith("Tetiklendi"):
                    style_df.loc[idx, "Zarar Kes"] = _negative_text_style()
        return style_df

    return zebra_style(df, extra_style_fn=apply_styles)


def _style_trades(df: pd.DataFrame):
    """'Yön' sütununu alış/satışa göre renklendirir, üzerine zebra_style'ın
    satır bandını uygular."""
    def apply_styles(data):
        style_df = pd.DataFrame("", index=data.index, columns=data.columns)
        if "Yön" in data.columns:
            for idx in data.index:
                style_df.loc[idx, "Yön"] = _side_style(data.loc[idx, "Yön"])
        return style_df

    return zebra_style(df, extra_style_fn=apply_styles)


def _fetch_bars_for_timeframe(client: AlpacaClient, symbol: str, timeframe: str, start: datetime) -> list[Bar]:
    return get_bars_for_timeframe(client, symbol, timeframe, start, exclude_forming=True)


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_chart_bars(key_id: str, secret_key: str, symbol: str, timeframe: str, start_date: str) -> list[Bar]:
    """İşlem detay grafiği için mum verisi çeker. start_date (YYYY-MM-DD) günlük
    çözünürlükte önbelleklenir, böylece grafik açıkken sayfa her yeniden
    çalıştığında Alpaca'ya tekrar istek atılmaz."""
    client = AlpacaClient(key_id, secret_key)
    start = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
    return _fetch_bars_for_timeframe(client, symbol, timeframe, start)


def _render_trade_detail_chart(bars: list[Bar], trades: list[dict], symbol: str, timeframe_label: str):
    if not bars:
        st.warning("Grafik için mum verisi bulunamadı.")
        return

    times = [pd.to_datetime(b.t) for b in bars]
    fig = go.Figure(data=[go.Candlestick(
        x=times, open=[b.o for b in bars], high=[b.h for b in bars],
        low=[b.l for b in bars], close=[b.c for b in bars], name="Fiyat",
        increasing=dict(line=dict(color=_POSITIVE_HEX), fillcolor=_POSITIVE_HEX),
        decreasing=dict(line=dict(color=_NEGATIVE_HEX), fillcolor=_NEGATIVE_HEX),
    )])

    buys = [t for t in trades if t.get("side") == "buy"]
    sells = [t for t in trades if t.get("side") == "sell"]
    if buys:
        fig.add_trace(go.Scatter(
            x=[pd.to_datetime(t["time"]) for t in buys], y=[t["price"] for t in buys],
            mode="markers", name="Alım",
            marker=dict(symbol="triangle-up", size=14, color=_BUY_MARKER_COLOR, line=dict(color="#000000", width=1)),
        ))
    if sells:
        fig.add_trace(go.Scatter(
            x=[pd.to_datetime(t["time"]) for t in sells], y=[t["price"] for t in sells],
            mode="markers", name="Satım",
            marker=dict(symbol="triangle-down", size=14, color=_SELL_MARKER_COLOR, line=dict(color="#000000", width=1)),
        ))

    fig.update_layout(
        title=f"{symbol} - İşlem Detay Grafiği ({timeframe_label})",
        template=get_plotly_template(), height=600, xaxis_rangeslider_visible=False,
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_bicak_kanali_chart(bars: list[Bar], symbol: str, timeframe: str, pencere: int = 30):
    """Bıçak Kanalı algoritmasının kurduğu yapıyı - kılavuz/bıçak/sıfır/yeşil
    çizgi, dip kesişim mumu ve (varsa) yeşil çizginin fiyatla en son kesiştiği
    "en yakın alım noktası" - tek bir grafikte gösterir. Grafik çizimi
    bicak_kanali_test.py'deki (🔪 Bıçak Kanalı Testi modülü) ile aynı
    fonksiyonu (render_bicak_kanali_chart) kullanır, böylece iki modülde de
    birebir aynı yapı görselleştirilir. pencere, o backtest çalıştırmasının
    BackTest arayüzünde seçtiği aynı değerdir (bkz. buy_algorithms.
    bicak_kanali_signal) - eski (bicak_pencere alanı olmayan) kayıtlarda
    kod-varsayılanına (30) düşer. bkz. bicak_kanali.py."""
    if not bars:
        st.warning("Grafik için mum verisi bulunamadı.")
        return

    result = find_kilavuz(bars, pencere=pencere)
    if result is None:
        st.info("Bu veri için Bıçak Kanalı yapısı kurulamadı (uygun bir düşüş bacağı/kılavuz bulunamadı).")
        return

    render_bicak_kanali_chart(bars, symbol, timeframe, result)
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


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_1d_volatility(key_id: str, secret_key: str, symbol: str) -> float | None:
    """Son kapanan günün (Yüksek-Düşük)/Kapanış yüzdesi - basit, standart
    bir gün-içi volatilite ölçütü."""
    client = AlpacaClient(key_id, secret_key)
    try:
        start = datetime.now(timezone.utc) - timedelta(days=10)
        raw_bars = client.get_raw_bars(symbol, "1Day", start.isoformat())
    except Exception:
        return None
    if not raw_bars:
        return None
    last = raw_bars[-1]
    if not last.get("c"):
        return None
    return round((last["h"] - last["l"]) / last["c"] * 100, 2)


def _daily_pairs(client: AlpacaClient, symbol: str, days_of_data: int) -> list[tuple]:
    start = datetime.now(timezone.utc) - timedelta(days=days_of_data + DAILY_TREND_LOOKBACK_DAYS)
    raw = client.get_raw_bars(symbol, "1Day", start.isoformat())
    pairs = [(datetime.fromisoformat(b["t"].replace("Z", "+00:00")).date(), b["c"]) for b in raw]
    pairs.sort(key=lambda p: p[0])
    return pairs


def _render_symbol_picker(client: AlpacaClient, key_id: str, secret_key: str, target_list: list[str]) -> str | None:
    st.subheader("📋 Hisse Seçimi")
    st.caption("Backtest için listeden tek bir hisse seç (ilk sütun). '1G Volatilite %', son kapanan günün (Yüksek-Düşük)/Kapanış oranıdır.")

    state_key = "backtest_selected_symbol"
    if state_key not in st.session_state:
        st.session_state[state_key] = None
    if st.session_state[state_key] not in target_list:
        st.session_state[state_key] = None

    with st.spinner("Volatilite verileri yükleniyor (1 saatlik önbellek)..."):
        volatility = {s: _fetch_1d_volatility(key_id, secret_key, s) for s in target_list}

    picker_df = pd.DataFrame({
        "Seçili": [s == st.session_state[state_key] for s in target_list],
        "Hisse": target_list,
        "1G Volatilite %": [volatility.get(s) for s in target_list],
    })

    edited = st.data_editor(
        picker_df,
        column_config={
            "Seçili": st.column_config.CheckboxColumn(required=True),
            "1G Volatilite %": st.column_config.NumberColumn(format="%.2f%%"),
        },
        disabled=["Hisse", "1G Volatilite %"],
        hide_index=True,
        use_container_width=True,
        key="backtest_picker_editor",
    )

    checked = edited[edited["Seçili"]]["Hisse"].tolist()
    if len(checked) > 1:
        newly_checked = [s for s in checked if s != st.session_state[state_key]]
        st.session_state[state_key] = (newly_checked or checked)[0]
        st.rerun()
    elif len(checked) == 1:
        st.session_state[state_key] = checked[0]
    else:
        st.session_state[state_key] = None

    if st.session_state[state_key]:
        st.success(f"Seçili hisse: **{st.session_state[state_key]}**")
    return st.session_state[state_key]


def _render_settings():
    st.subheader("🧠 Buy-Point Algoritmaları")
    algo_cols = st.columns(len(ALGORITHMS))
    selected_algorithms = [
        algo_id for col, (algo_id, (label, _fn)) in zip(algo_cols, ALGORITHMS.items())
        if col.checkbox(label, key=f"bt_algo_{algo_id}")
    ]

    bicak_pencere = 30
    if "bicak_kanali" in selected_algorithms:
        bicak_pencere = st.number_input(
            "Bıçak Kanalı - Pencere (bar)", min_value=1, value=30, step=5, key="bt_bicak_pencere",
            help="Düşüş bacağı taraması sadece en güncel bu kadar bar içindeki pivotlarla sınırlanır - "
                 "Bıçak Kanalı Test modülündeki (🔪) aynı ayar.",
        )

    st.subheader("🛡️ Stop-Loss Algoritmaları")
    st.caption("Her seçili buy-point algoritması × mum periyodu kombinasyonu, aşağıda seçtiğiniz her stop-loss algoritmasıyla ayrı ayrı koşulur.")
    stop_algo_cols = st.columns(len(STOP_ALGORITHMS))
    selected_stop_algorithms = [
        algo_id for col, (algo_id, stop_algo) in zip(stop_algo_cols, STOP_ALGORITHMS.items())
        if col.checkbox(stop_algo.label, key=f"bt_stop_algo_{algo_id}")
    ]

    st.subheader("🕯️ Mum Periyodu")
    tf_cols = st.columns(len(TIMEFRAMES))
    selected_timeframes = [
        tf for col, tf in zip(tf_cols, TIMEFRAMES)
        if col.checkbox(TIMEFRAME_LABELS[tf], key=f"bt_tf_{tf}")
    ]

    stop_timeframe_choice = st.selectbox(
        "Stop-Loss Mum Periyodu", options=[STOP_TIMEFRAME_SAME_AS_ENTRY] + TIMEFRAMES,
        format_func=lambda tf: "Alım mumuyla aynı" if tf == STOP_TIMEFRAME_SAME_AS_ENTRY else TIMEFRAME_LABELS[tf],
        key="bt_stop_timeframe",
        help="Alım sinyali yukarıda seçilen mum periyodu/periyotlarıyla üretilir; bu seçenek sadece stop-loss "
             "tetiklenmesinin ve iz sürmenin (trailing) hangi mum periyoduyla kontrol edileceğini belirler - "
             "canlı sistemde de alım (alpaca_buy_points.py, sembole özel periyot) ve stop/trail "
             "(alpaca_trailing_stop.py, ayrı TRADE_TIMEFRAME) zaten farklı periyotlarda çalışıyor. Örn. alımı "
             "'1 Gün' ile üretip stop'u '30 Dakika' ile daha sık kontrol edebilirsin.",
    )

    with st.expander("📅 Mum verisi hangi saatleri kapsıyor?"):
        st.markdown(
            "- **15 Dakika / 30 Dakika / 1 Saat:** Sadece normal seans (09:30-16:00 ET) "
            "mumları kullanılır - canlı trailing-stop'un (`alpaca_trailing_stop.py`) "
            "kullandığı aynı filtre burada da geçerli, pre-market/after-hours mumları hiç "
            "dahil edilmez. Bu, backtest sonucunun canlı sistemin gerçekte ne yapacağını "
            "yansıtmasını sağlamak için bilinçli bir tercih.\n"
            "- **1 Gün** (ve trend/SMA200 filtresi için kullanılan günlük kapanışlar): "
            "Alpaca'nın döndürdüğü günlük bar, ek bir seans filtresi uygulanmadan olduğu "
            "gibi kullanılır - günlük barın kendi zaman damgasına 09:30-16:00 filtresini "
            "uygulamak, barların tamamını yanlışlıkla eleyebilir. Alpaca'nın günlük barı "
            "kendi tarafında pre-market/after-hours işlemlerini OHLC'ye dahil edip "
            "etmediği doğrulanmadı; kod sadece geleni olduğu gibi aktarıyor."
        )

    c1, c2, c3 = st.columns(3)
    days_of_data = c1.number_input(
        "Kaç günlük veri ile çalışılacak", min_value=5, max_value=1000, value=180, step=5,
        help="Geriye doğru kaç takvim günü bar çekilecek.",
    )
    days_before_trading = c2.number_input(
        "Kaç günden sonraki veri ile işlem yapılacak", min_value=0, max_value=max(int(days_of_data) - 1, 0),
        value=0, step=5,
        help="Çekilen verinin başındaki bu kadar gün, sadece algoritmanın geçmiş bağlamı için kullanılır - alım/satım bu günden sonra başlar.",
    )
    budget = c3.number_input("Portföy büyüklüğü ($)", min_value=0.0, value=10000.0, step=100.0)

    st.subheader("🛑 Risk Yönetimi")
    sl1, sl2 = st.columns([1, 2])
    stop_loss_enabled = sl1.checkbox(
        "Zarar Kes", key="bt_stop_loss_enabled",
        help="Etkinleştirilirse, çalıştırma boyunca gerçekleşen toplam zarar başlangıç bütçesine göre girilen yüzdeye ulaştığında, o çalıştırma için yeni alım/satım işlemi yapılmaz.",
    )
    max_loss_pct = sl2.number_input(
        "Maksimum zarar yüzdesi", min_value=0.1, max_value=100.0, value=10.0, step=0.5,
        disabled=not stop_loss_enabled, key="bt_max_loss_pct",
        help="Başlangıç bütçesine göre toplam zarar bu yüzdeye ulaştığında, o çalıştırma için yeni alım/satım işlemleri durdurulur.",
    )

    return (selected_algorithms, selected_stop_algorithms, selected_timeframes, stop_timeframe_choice,
            int(days_of_data), int(days_before_trading), float(budget), bool(stop_loss_enabled),
            float(max_loss_pct), int(bicak_pencere))


def _run_backtests(client, symbol, algorithms, stop_algorithms, timeframes, stop_timeframe_choice, days_of_data,
                    days_before_trading, budget, stop_loss_enabled, max_loss_pct, username, bicak_pencere):
    start = datetime.now(timezone.utc) - timedelta(days=days_of_data)
    bars_by_tf = {}
    for tf in timeframes:
        try:
            bars_by_tf[tf] = _fetch_bars_for_timeframe(client, symbol, tf, start)
        except Exception as e:
            st.error(f"{symbol} için {TIMEFRAME_LABELS[tf]} barları çekilemedi: {e}")
            bars_by_tf[tf] = []

    # Stop-Loss Mum Periyodu "Alım mumuyla aynı" dışında bir şey seçildiyse,
    # o periyodun barları BİR KEZ çekilir ve tüm kombinasyonlarda stop-loss
    # tetiklenmesi/trail için (alım sinyalinin kendi periyodundan bağımsız
    # olarak) kullanılır - bkz. run_backtest'in stop_bars/stop_timeframe
    # parametreleri.
    stop_bars_override = None
    if stop_timeframe_choice != STOP_TIMEFRAME_SAME_AS_ENTRY:
        if stop_timeframe_choice in bars_by_tf:
            stop_bars_override = bars_by_tf[stop_timeframe_choice]
        else:
            try:
                stop_bars_override = _fetch_bars_for_timeframe(client, symbol, stop_timeframe_choice, start)
            except Exception as e:
                st.error(f"{symbol} için {TIMEFRAME_LABELS[stop_timeframe_choice]} stop-loss barları çekilemedi: {e}")
                stop_bars_override = []

    try:
        daily_pairs = _daily_pairs(client, symbol, days_of_data)
    except Exception as e:
        st.error(f"{symbol} için günlük veri çekilemedi (trend filtresi/SMA200 kullanılamayacak): {e}")
        daily_pairs = []

    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    effective_max_loss_pct = max_loss_pct if stop_loss_enabled else None
    # Stop Loss Ayarları sayfasında kullanıcının kaydettiği parametreler - böylece
    # backtest, canlı sistemin şu an gerçekte kullandığı değerlerle çalışır
    # (bkz. run_backtest'in stop_settings docstring'i).
    stop_settings = load_stop_loss_settings(username)
    new_runs = []
    progress = st.progress(0.0)
    effective_stop_timeframe = None if stop_timeframe_choice == STOP_TIMEFRAME_SAME_AS_ENTRY else stop_timeframe_choice
    combos = [(a, tf, sa) for a in algorithms for tf in timeframes for sa in stop_algorithms]
    for i, (algo_id, tf, stop_algo_id) in enumerate(combos):
        # stop_bars_override, tf ile AYNI periyot seçildiyse (bars_by_tf[tf] ile
        # aynı liste) zaten stop_bars=bars ile birebir eşdeğer olur - run_backtest
        # yine de doğru çalışır, sadece gereksiz yere "ayrı" bir liste geçilmiş olur.
        result = run_backtest(
            symbol=symbol, algorithm=algo_id, timeframe=tf, bars=bars_by_tf.get(tf, []),
            daily_pairs=daily_pairs, days_of_data=days_of_data, days_before_trading=days_before_trading,
            starting_budget=budget, max_loss_pct=effective_max_loss_pct, stop_algorithm=stop_algo_id,
            stop_settings=stop_settings, bicak_pencere=bicak_pencere,
            stop_bars=stop_bars_override, stop_timeframe=effective_stop_timeframe,
        )
        new_runs.append({
            "run_id": new_run_id(symbol, algo_id, tf, stop_algo_id),
            "run_at": run_at,
            "symbol": symbol,
            "algorithm": algo_id,
            "timeframe": tf,
            "stop_timeframe": effective_stop_timeframe or tf,
            "stop_algorithm": stop_algo_id,
            "days_of_data": days_of_data,
            "days_before_trading": days_before_trading,
            "starting_budget": budget,
            "final_value": result.final_value,
            "pnl": result.pnl,
            "pnl_pct": result.pnl_pct,
            "stop_loss_enabled": stop_loss_enabled,
            "max_loss_pct": effective_max_loss_pct,
            "stop_loss_triggered": result.stop_loss_triggered,
            "stop_loss_triggered_at": result.stop_loss_triggered_at,
            "trades": [vars(t) for t in result.trades],
            "source": "Alpaca",
            "bicak_pencere": bicak_pencere if algo_id == "bicak_kanali" else None,
        })
        progress.progress((i + 1) / len(combos))
    progress.empty()

    return append_results(username, new_runs)


def _render_results(all_results: list[dict], key_id: str, secret_key: str):
    st.subheader("📊 Sonuçlar")
    if not all_results:
        st.info("Henüz kaydedilmiş bir backtest çalıştırması yok.")
        return

    grouped = group_by_algorithm(all_results)
    tab_ids = list(grouped.keys())
    tab_labels = [ALGORITHMS.get(a, (a, None))[0] for a in tab_ids]
    tabs = st.tabs(tab_labels)

    for tab, algo_id in zip(tabs, tab_ids):
        with tab:
            runs = grouped[algo_id]
            summary_rows = [{
                "Çalıştırma (UTC)": r.get("run_at", ""),
                "Hisse": r.get("symbol", ""),
                "Mum Periyodu": TIMEFRAME_LABELS.get(r.get("timeframe"), r.get("timeframe")),
                "Stop-Loss Mum Periyodu": TIMEFRAME_LABELS.get(
                    r.get("stop_timeframe") or r.get("timeframe"), r.get("stop_timeframe") or r.get("timeframe")
                ),
                "Stop-Loss Algoritması": _stop_algorithm_label(r),
                "Kaynak": r.get("source") or "Alpaca",
                "Veri (gün)": r.get("days_of_data"),
                "İşlem Başlangıcı (gün)": r.get("days_before_trading"),
                "Başlangıç Bütçe": r.get("starting_budget"),
                "Bitiş Değeri": r.get("final_value"),
                "K/Z": r.get("pnl"),
                "K/Z %": r.get("pnl_pct"),
                "İşlem Sayısı": len(r.get("trades") or []),
                "Zarar Kes": _format_stop_loss(r),
            } for r in runs]
            latest_run_at = max((r.get("run_at") or "" for r in runs), default="")
            if latest_run_at:
                freshness_caption(f"En son çalıştırma: {latest_run_at} UTC (her satırın kendi zamanı 'Çalıştırma (UTC)' sütununda).")
            st.dataframe(
                _style_summary(pd.DataFrame(summary_rows)), column_config=_SUMMARY_COLUMN_CONFIG,
                use_container_width=True, hide_index=True,
            )

            options = [
                f"{r.get('run_at')} · {r.get('symbol')} · "
                f"{TIMEFRAME_LABELS.get(r.get('timeframe'), r.get('timeframe'))} · {_stop_algorithm_label(r)}"
                for r in runs
            ]
            picked = st.selectbox("İşlem detayı için bir çalıştırma seç", options, key=f"bt_detail_pick_{algo_id}")
            picked_run = runs[options.index(picked)]

            if algo_id == "bicak_kanali":
                bicak_chart_key = f"bt_show_bicak_chart_{algo_id}"
                if bicak_chart_key not in st.session_state:
                    st.session_state[bicak_chart_key] = False
                if st.button("🔪 Bıçak Kanalı Analiz Grafiği", key=f"bt_bicak_chart_btn_{algo_id}"):
                    st.session_state[bicak_chart_key] = not st.session_state[bicak_chart_key]

                if st.session_state[bicak_chart_key]:
                    fallback_start = datetime.now(timezone.utc) - timedelta(days=(picked_run.get("days_of_data") or 180) + 5)
                    with st.spinner("Bıçak Kanalı grafiği için mum verileri çekiliyor..."):
                        try:
                            bicak_bars = _fetch_chart_bars(
                                key_id, secret_key, picked_run.get("symbol"), picked_run.get("timeframe"),
                                fallback_start.date().isoformat(),
                            )
                        except Exception as e:
                            st.error(f"Mum verileri çekilemedi: {e}")
                            bicak_bars = []
                    _render_bicak_kanali_chart(
                        bicak_bars, picked_run.get("symbol"), picked_run.get("timeframe"),
                        pencere=picked_run.get("bicak_pencere") or 30,
                    )

            trades = picked_run.get("trades") or []
            if trades:
                trade_rows = [{
                    "Yön": "Alış" if t.get("side") == "buy" else "Satış",
                    "Zaman (UTC)": t.get("time"),
                    "Fiyat": t.get("price"),
                    "Adet": t.get("qty"),
                    "Sebep": t.get("reason"),
                } for t in trades]
                if picked_run.get("run_at"):
                    freshness_caption(f"Bu çalıştırma tarihi: {picked_run['run_at']} UTC.")
                st.dataframe(
                    _style_trades(pd.DataFrame(trade_rows)), column_config=_TRADES_COLUMN_CONFIG,
                    use_container_width=True, hide_index=True,
                )

                chart_state_key = f"bt_show_chart_{algo_id}"
                if chart_state_key not in st.session_state:
                    st.session_state[chart_state_key] = False
                if st.button("İşlem Detay Grafiği", key=f"bt_chart_btn_{algo_id}"):
                    st.session_state[chart_state_key] = not st.session_state[chart_state_key]

                if st.session_state[chart_state_key]:
                    if (picked_run.get("source") or "Alpaca") != "Alpaca":
                        st.caption(
                            "Bu çalıştırma Alpaca dışı bir kaynaktan (örn. Alım Bölgesi Tarama - Yahoo "
                            "Finance) geldiği için işlem detay grafiği burada gösterilemiyor - mum verisi "
                            "Alpaca'dan çekiliyor ve o çalıştırmanın kullandığı veriyle eşleşmeyebilir."
                        )
                    else:
                        trade_times = [_parse_ts(t["time"]) for t in trades if t.get("time")]
                        fallback_start = datetime.now(timezone.utc) - timedelta(days=picked_run.get("days_of_data") or 180)
                        start_dt = min(trade_times + [fallback_start]) - timedelta(days=2)
                        with st.spinner("Grafik için mum verileri çekiliyor..."):
                            try:
                                bars = _fetch_chart_bars(
                                    key_id, secret_key, picked_run.get("symbol"), picked_run.get("timeframe"),
                                    start_dt.date().isoformat(),
                                )
                            except Exception as e:
                                st.error(f"Mum verileri çekilemedi: {e}")
                                bars = []
                        _render_trade_detail_chart(
                            bars, trades, picked_run.get("symbol"),
                            TIMEFRAME_LABELS.get(picked_run.get("timeframe"), picked_run.get("timeframe")),
                        )
            else:
                st.caption("Bu çalıştırmada hiç işlem gerçekleşmedi.")


def render_backtest(target_list: list[str], username: str):
    user_alpaca = st.secrets.get("alpaca", {}).get(username, {})
    key_id = user_alpaca.get("key_id")
    secret_key = user_alpaca.get("secret_key")
    if not key_id or not secret_key:
        st.warning(f"'{username}' için Alpaca hesabı tanımlı değil (`.streamlit/secrets.toml` içinde `[alpaca.{username}]`). Backtest, geçmiş fiyat verisi için Alpaca'nın veri API'sini kullanır.")
        return

    client = AlpacaClient(key_id, secret_key)

    selected_symbol = _render_symbol_picker(client, key_id, secret_key, target_list)
    st.divider()
    (selected_algorithms, selected_stop_algorithms, selected_timeframes, stop_timeframe_choice, days_of_data,
     days_before_trading, budget, stop_loss_enabled, max_loss_pct, bicak_pencere) = _render_settings()

    st.divider()
    can_run = bool(selected_symbol and selected_algorithms and selected_stop_algorithms and selected_timeframes)
    if st.button("🚀 Backtest Çalıştır", type="primary", disabled=not can_run):
        with st.spinner(
            f"{selected_symbol} için {len(selected_algorithms)} algoritma × {len(selected_stop_algorithms)} "
            f"stop-loss algoritması × {len(selected_timeframes)} mum periyodu çalıştırılıyor..."
        ):
            all_results = _run_backtests(
                client, selected_symbol, selected_algorithms, selected_stop_algorithms, selected_timeframes,
                stop_timeframe_choice, days_of_data, days_before_trading, budget, stop_loss_enabled, max_loss_pct,
                username, bicak_pencere,
            )
        st.success("Backtest tamamlandı ve sonuçlar kaydedildi.")
    else:
        if not can_run:
            st.caption("Çalıştırmak için bir hisse, en az bir buy-point algoritması, en az bir stop-loss "
                       "algoritması ve en az bir mum periyodu seçmelisin.")
        all_results = load_results(username)

    st.divider()
    _render_results(all_results, key_id, secret_key)
