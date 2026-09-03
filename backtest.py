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
import streamlit as st

from alpaca_client import AlpacaClient
from alpaca_trailing_stop import get_regular_hours_bars
from backtest_data import append_results, group_by_algorithm, load_results, new_run_id
from backtest_engine import run_backtest
from buy_algorithms import ALGORITHMS
from structure import Bar
from ui_style import zebra_style

TIMEFRAMES = ["30Min", "1Hour", "1Day"]
TIMEFRAME_LABELS = {"30Min": "30 Dakika", "1Hour": "1 Saat", "1Day": "1 Gün"}
DAILY_TREND_LOOKBACK_DAYS = 400  # trend_pullback SMA200 + trend filtresi için yeterli pay


def _fetch_bars_for_timeframe(client: AlpacaClient, symbol: str, timeframe: str, start: datetime) -> list[Bar]:
    # Günlük barlar için get_regular_hours_bars kullanılmaz - "regular hours"
    # (09:30-16:00 ET) penceresi gün içi barlar için anlamlı, günlük barın
    # kendi zaman damgasına uygulanınca barları yanlışlıkla eleyebilir
    # (alpaca_trailing_stop.py'nin check_trend_filter'ı da bu yüzden günlük
    # barları doğrudan client.get_raw_bars ile çeker).
    if timeframe == "1Day":
        raw = client.get_raw_bars(symbol, "1Day", start.isoformat())
        return [Bar(t=b["t"], o=b["o"], h=b["h"], l=b["l"], c=b["c"], v=b["v"]) for b in raw]
    return get_regular_hours_bars(client, symbol, timeframe, start, exclude_forming=True)


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

    st.subheader("🕯️ Mum Periyodu")
    tf_cols = st.columns(len(TIMEFRAMES))
    selected_timeframes = [
        tf for col, tf in zip(tf_cols, TIMEFRAMES)
        if col.checkbox(TIMEFRAME_LABELS[tf], key=f"bt_tf_{tf}")
    ]

    c1, c2, c3 = st.columns(3)
    days_of_data = c1.number_input(
        "Kaç günlük veri ile çalışılacak", min_value=5, max_value=1000, value=180, step=5,
        help="Geriye doğru kaç takvim günü bar çekilecek.",
    )
    days_before_trading = c2.number_input(
        "Kaç günden sonraki veri ile işlem yapılacak", min_value=0, max_value=max(int(days_of_data) - 1, 0),
        value=min(30, max(int(days_of_data) - 1, 0)), step=5,
        help="Çekilen verinin başındaki bu kadar gün, sadece algoritmanın geçmiş bağlamı için kullanılır - alım/satım bu günden sonra başlar.",
    )
    budget = c3.number_input("Portföy büyüklüğü ($)", min_value=0.0, value=10000.0, step=100.0)

    return selected_algorithms, selected_timeframes, int(days_of_data), int(days_before_trading), float(budget)


def _run_backtests(client, symbol, algorithms, timeframes, days_of_data, days_before_trading, budget, username):
    start = datetime.now(timezone.utc) - timedelta(days=days_of_data)
    bars_by_tf = {}
    for tf in timeframes:
        try:
            bars_by_tf[tf] = _fetch_bars_for_timeframe(client, symbol, tf, start)
        except Exception as e:
            st.error(f"{symbol} için {TIMEFRAME_LABELS[tf]} barları çekilemedi: {e}")
            bars_by_tf[tf] = []

    try:
        daily_pairs = _daily_pairs(client, symbol, days_of_data)
    except Exception as e:
        st.error(f"{symbol} için günlük veri çekilemedi (trend filtresi/SMA200 kullanılamayacak): {e}")
        daily_pairs = []

    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new_runs = []
    progress = st.progress(0.0)
    combos = [(a, tf) for a in algorithms for tf in timeframes]
    for i, (algo_id, tf) in enumerate(combos):
        result = run_backtest(
            symbol=symbol, algorithm=algo_id, timeframe=tf, bars=bars_by_tf.get(tf, []),
            daily_pairs=daily_pairs, days_of_data=days_of_data, days_before_trading=days_before_trading,
            starting_budget=budget,
        )
        new_runs.append({
            "run_id": new_run_id(symbol, algo_id, tf),
            "run_at": run_at,
            "symbol": symbol,
            "algorithm": algo_id,
            "timeframe": tf,
            "days_of_data": days_of_data,
            "days_before_trading": days_before_trading,
            "starting_budget": budget,
            "final_value": result.final_value,
            "pnl": result.pnl,
            "pnl_pct": result.pnl_pct,
            "trades": [vars(t) for t in result.trades],
        })
        progress.progress((i + 1) / len(combos))
    progress.empty()

    return append_results(username, new_runs)


def _render_results(all_results: list[dict]):
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
                "Veri (gün)": r.get("days_of_data"),
                "İşlem Başlangıcı (gün)": r.get("days_before_trading"),
                "Başlangıç Bütçe": r.get("starting_budget"),
                "Bitiş Değeri": r.get("final_value"),
                "K/Z": r.get("pnl"),
                "K/Z %": r.get("pnl_pct"),
                "İşlem Sayısı": len(r.get("trades") or []),
            } for r in runs]
            st.dataframe(zebra_style(pd.DataFrame(summary_rows)), use_container_width=True, hide_index=True)

            options = [f"{r.get('run_at')} · {r.get('symbol')} · {TIMEFRAME_LABELS.get(r.get('timeframe'), r.get('timeframe'))}" for r in runs]
            picked = st.selectbox("İşlem detayı için bir çalıştırma seç", options, key=f"bt_detail_pick_{algo_id}")
            picked_run = runs[options.index(picked)]
            trades = picked_run.get("trades") or []
            if trades:
                st.dataframe(zebra_style(pd.DataFrame(trades)), use_container_width=True, hide_index=True)
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
    selected_algorithms, selected_timeframes, days_of_data, days_before_trading, budget = _render_settings()

    st.divider()
    can_run = bool(selected_symbol and selected_algorithms and selected_timeframes)
    if st.button("🚀 Backtest Çalıştır", type="primary", disabled=not can_run):
        with st.spinner(f"{selected_symbol} için {len(selected_algorithms)} algoritma × {len(selected_timeframes)} mum periyodu çalıştırılıyor..."):
            all_results = _run_backtests(
                client, selected_symbol, selected_algorithms, selected_timeframes,
                days_of_data, days_before_trading, budget, username,
            )
        st.success("Backtest tamamlandı ve sonuçlar kaydedildi.")
    else:
        if not can_run:
            st.caption("Çalıştırmak için bir hisse, en az bir algoritma ve en az bir mum periyodu seçmelisin.")
        all_results = load_results(username)

    st.divider()
    _render_results(all_results)
