from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from alpaca_client import AlpacaClient
from backtest import TIMEFRAME_LABELS
from buy_algorithms import ALGORITHMS
from config import load_initial_capital, save_initial_capital
from ui_style import zebra_style

TR_TZ = ZoneInfo("Europe/Istanbul")
HISTORY_DAYS = 30

STATUS_TR = {
    "new": "Aktif (Bekliyor)",
    "held": "Aktif (Bekliyor)",
    "accepted": "Aktif (Bekliyor)",
    "replaced": "Trail Edildi",
    "filled": "Tetiklendi",
    "canceled": "İptal Edildi",
    "expired": "Süresi Doldu",
    "rejected": "Reddedildi",
}

TYPE_TR = {
    "market": "Piyasa Emri",
    "stop": "Stop",
    "stop_limit": "Stop-Limit",
    "limit": "Limit",
}


def _to_tr_time(iso_ts: str) -> datetime:
    return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).astimezone(TR_TZ)


def _order_price(order: dict) -> float | None:
    for field in ("stop_price", "filled_avg_price", "limit_price"):
        if order.get(field):
            return float(order[field])
    return None


def _parse_order_tag(order: dict) -> tuple[str, str]:
    """alpaca_buy_points.py tags buy-limit entries with client_order_id
    "algo-<algorithm_id>-<timeframe>-<symbol>-<epoch>" so historical orders
    stay attributed to whichever algorithm/mum periyodu was active when each
    was placed, even after the portfolio's settings later change. Orders
    placed before the timeframe was added to this tag have the older
    4-part "algo-<algorithm_id>-<symbol>-<epoch>" form (mum periyodu
    unknown). Orders that aren't a tagged buy-limit entry show "—" for both.
    Returns (algoritma_etiketi, mum_periyodu_etiketi)."""
    parts = (order.get("client_order_id") or "").split("-")
    if not parts or parts[0] != "algo":
        return "—", "—"
    if len(parts) == 5:
        algo_id, timeframe = parts[1], parts[2]
    elif len(parts) == 4:
        algo_id, timeframe = parts[1], None
    else:
        return "—", "—"

    algo = ALGORITHMS.get(algo_id)
    algo_label = algo[0] if algo else "—"
    timeframe_label = TIMEFRAME_LABELS.get(timeframe, timeframe) if timeframe else "—"
    return algo_label, timeframe_label


def format_order_row(order: dict) -> dict:
    created = _to_tr_time(order["created_at"])
    price = _order_price(order)
    algo_label, timeframe_label = _parse_order_tag(order)

    if order.get("qty"):
        amount = f"{float(order['qty']):g} adet"
    elif order.get("notional"):
        amount = f"${float(order['notional']):.2f}"
    else:
        amount = f"{float(order.get('filled_qty') or 0):g} adet"

    return {
        "_sort_ts": created,
        "Tarih (TRT)": created.strftime("%d.%m.%Y %H:%M:%S"),
        "Hisse": order["symbol"],
        "Tip": TYPE_TR.get(order["type"], order["type"]),
        "Algoritma": algo_label,
        "Mum Periyodu": timeframe_label,
        "Yön": "Satış" if order["side"] == "sell" else "Alış",
        "Fiyat": round(price, 2) if price is not None else "—",
        "Adet/Tutar": amount,
        "Durum": STATUS_TR.get(order["status"], order["status"]),
    }


def render_account_summary(client: AlpacaClient, username: str, positions: list[dict]):
    """Nakit/toplam hesap değeri özeti ve ilk sermayeye göre anlık kâr -
    Genel Bakış (app.py) ve Alpaca Canlı Pozisyonlar sayfalarında ortak
    gösterilir, tek bir yerden hesaplanır."""
    account = client.get_account()
    cash = float(account["cash"])
    equity = float(account["equity"])  # nakit + tüm pozisyonların güncel piyasa değeri

    if 'initial_capital' not in st.session_state:
        st.session_state.initial_capital = load_initial_capital(username)

    with st.expander(
        "💵 İlk Sermaye Ayarı",
        expanded=st.session_state.initial_capital is None,
    ):
        st.caption(
            "Alım/satım sayısı arttıkça 'açık pozisyonların gerçekleşmemiş K/Z toplamı' kavramı "
            "portföyün gerçek performansını yansıtmaz hale gelir. Bunun yerine, hesaba ilk "
            "yatırdığınız sermayeyi bir kez girin - kâr, o andaki toplam hesap değeri (nakit + "
            "pozisyonlar) ile bu sermaye karşılaştırılarak hesaplanır; yapılan tüm alım/satımların "
            "net etkisini (gerçekleşmiş ve gerçekleşmemiş birlikte) kapsar."
        )
        new_capital = st.number_input(
            "İlk yatırılan sermaye ($)", min_value=0.0,
            value=float(st.session_state.initial_capital or 0.0), step=100.0,
            key="initial_capital_input",
        )
        if st.button("Kaydet", key="save_initial_capital_btn"):
            save_initial_capital(new_capital, username)
            st.session_state.initial_capital = new_capital
            st.success("İlk sermaye kaydedildi.")
            st.rerun()

    initial_capital = st.session_state.initial_capital

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Açık Pozisyon", len(positions))
    c2.metric("Nakit", f"${cash:,.2f}")
    c3.metric("Toplam Portföy Değeri", f"${equity:,.2f}")
    if initial_capital:
        total_pl = equity - initial_capital
        total_pl_pct = total_pl / initial_capital * 100
        c4.metric("Portföyün Anlık Kârı", f"${total_pl:,.2f}", f"{total_pl_pct:+.2f}%")
    else:
        c4.metric("Portföyün Anlık Kârı", "—")


def render_realized_pnl_table(client: AlpacaClient, orders: list[dict], history_days: int):
    """Son `history_days` gün içindeki dolan emirleri sembole göre eşleştirip
    (bkz. AlpacaClient.compute_realized_pnl_by_symbol) her sembolün kapanmış
    (alış+satış tamamlanmış) işlemlerinin kümülatif gerçekleşen kâr/zararını
    gösterir - bir hisse artık açık pozisyon olarak görünmese (tamamen
    satılmış olsa) bile burada listelenmeye devam eder."""
    realized = client.compute_realized_pnl_by_symbol(orders)
    if not realized:
        st.info(f"Son {history_days} günde kapanmış (alış+satış eşleşen) işlem yok.")
        return

    rows = []
    for symbol, data in sorted(realized.items(), key=lambda kv: kv[1]["pnl"]):
        pnl_pct = (data["pnl"] / data["cost_basis"] * 100) if data["cost_basis"] else None
        rows.append({
            "Hisse": symbol,
            "Kapanan İşlem": data["trades"],
            "Kapanan Adet": f"{data['qty']:g}",
            "Gerçekleşen K/Z ($)": round(data["pnl"], 2),
            "Gerçekleşen K/Z %": round(pnl_pct, 2) if pnl_pct is not None else "—",
        })

    st.dataframe(zebra_style(pd.DataFrame(rows)), use_container_width=True, hide_index=True)
    total_pnl = sum(d["pnl"] for d in realized.values())
    st.caption(
        f"Toplam gerçekleşen K/Z: ${total_pnl:,.2f}. Son {history_days} gün içinde alınıp satılan "
        "(kapanmış) pozisyonlar için, alış/satış dolum fiyatları zaman sırasına göre eşleştirilerek "
        "hesaplanır (aynı anda tek pozisyon açıldığı varsayımıyla). Halen açık olan pozisyonların "
        "gerçekleşmemiş kâr/zararı yukarıdaki tabloda ayrıca gösterilir."
    )


def render_alpaca_dashboard(username):
    user_alpaca = st.secrets.get("alpaca", {}).get(username, {})
    key_id = user_alpaca.get("key_id")
    secret_key = user_alpaca.get("secret_key")
    if not key_id or not secret_key:
        st.warning(f"'{username}' için Alpaca hesabı tanımlı değil (`.streamlit/secrets.toml` içinde `[alpaca.{username}]`).")
        return

    client = AlpacaClient(key_id, secret_key)
    try:
        positions = client.get_all_positions()
        render_account_summary(client, username, positions)
    except Exception as e:
        st.warning(f"⚠️ Alpaca hesap özeti alınamadı: {e}")
        return
    st.divider()

    if not positions:
        st.info("Açık pozisyon yok.")
    else:
        rows = []
        for pos in positions:
            symbol = pos["symbol"]
            stop_order = client.get_open_stop_order(symbol)
            entry = float(pos["avg_entry_price"])
            current = float(pos["current_price"])
            stop_price = float(stop_order["stop_price"]) if stop_order else None

            rows.append({
                "Hisse": symbol,
                "Yön": "Long" if float(pos["qty"]) > 0 else "Short",
                "Adet": abs(float(pos["qty"])),
                "Ortalama Giriş": round(entry, 2),
                "Güncel Fiyat": round(current, 2),
                "Kâr/Zarar %": round(float(pos["unrealized_plpc"]) * 100, 2),
                "Stop Fiyatı": round(stop_price, 2) if stop_price is not None else "—",
                "Stoptan Uzaklık %": round((current - stop_price) / current * 100, 2) if stop_price is not None else "—",
            })

        st.dataframe(zebra_style(pd.DataFrame(rows)), use_container_width=True, hide_index=True)
        st.caption("Stoplar, structure-based trailing-stop GitHub Action tarafından 5 dakikada bir güncellenir.")

        with st.expander("🛡️ Stop-Loss Mantığı Nasıl Çalışır?"):
            st.markdown(
                "Yukarıdaki 'Stop Fiyatı' sütunu, elle değil, aşağıdaki kurallarla otomatik "
                "yönetilen structure-based bir trailing-stop sistemini yansıtır:\n\n"
                "- Pozisyon açıldığında (ya da elle açılmış, hiç stopu olmayan bir pozisyonda) "
                "önce giriş fiyatının %1.5 altına (long) / üstüne (short) sabit bir ilk stop konur.\n"
                "- Fiyat lehe en az %1 hareket ettiğinde stop, en azından giriş fiyatına "
                "(breakeven) çekilir.\n"
                "- Fiyat daha da ilerlerse, stop; kırılma-onaylı (break-of-structure) son swing "
                "noktasının biraz gerisine, ATR ile ölçeklenen bir tampon payıyla taşınır - bu "
                "sadece günlük EMA trend filtresi izin verdiği sürece uygulanır.\n"
                "- O an geçerli adaylardan (breakeven, top-up, structure) hangisi en sıkıysa o "
                "seçilir; stop hiçbir zaman gevşetilmez ve güncel fiyatı geçmez.\n"
                "- Pozisyona ilave alım yapıldığında stopun adedi otomatik güncellenir; tercihe "
                "göre yeni ortalama giriş fiyatına göre ek bir sıkılaştırma adayı da "
                "değerlendirilir.\n"
                "- Normal stop emirleri yalnızca normal seansta (09:30-16:00 ET) tetiklenebildiği "
                "için, pre-market/after-hours'ta fiyat stopu kırarsa ayrı bir *extended-hours "
                "guard* mekanizması devreye girip day+extended-hours limit emriyle koruma sağlar "
                "ve Telegram'dan bildirim gönderir.\n"
                "- Tüm bu kontroller GitHub Actions üzerinden normal seansta 5 dakikada, seans "
                "dışında ~10 dakikada bir otomatik çalışır - manuel müdahale gerekmez."
            )

    orders = client.get_recent_orders(days=HISTORY_DAYS)

    st.subheader("💰 Kapanmış İşlemler - Gerçekleşen Kâr/Zarar")
    render_realized_pnl_table(client, orders, HISTORY_DAYS)

    st.subheader(f"📜 Son {HISTORY_DAYS} Gün İşlem Geçmişi")

    history_rows = [format_order_row(o) for o in orders]

    if not history_rows:
        st.info(f"Son {HISTORY_DAYS} günde işlem yok.")
        return

    history_df = (
        pd.DataFrame(history_rows)
        .sort_values("_sort_ts", ascending=False)
        .drop(columns=["_sort_ts"])
    )
    st.dataframe(zebra_style(history_df), use_container_width=True, hide_index=True)
    st.caption("Sütun başlıklarına tıklayarak sıralayabilirsiniz. Varsayılan sıralama: en yeni işlem en üstte. Kapanmış pozisyonlar da dahildir.")
