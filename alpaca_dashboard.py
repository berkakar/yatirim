from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from alpaca_account_ui import get_user_alpaca, missing_keys_warning
from alpaca_client import AlpacaClient
from backtest import TIMEFRAME_LABELS
from buy_algorithms import ALGORITHMS
from config import load_initial_capital, save_initial_capital
from ui_style import zebra_style, freshness_caption

TR_TZ = ZoneInfo("Europe/Istanbul")
HISTORY_DAYS = 30

STATUS_TR = {
    "new": "Aktif (Bekliyor)",
    "accepted": "Aktif (Bekliyor)",
    # "held": bracket (order_class "oto") emrinin stop-loss ayağı - ana (giriş)
    # emri henüz dolmadığı sürece Alpaca'da BORSADA BEKLEMİYOR, sadece kayıtlı
    # duruyor; ana emir dolduğu an otomatik "new"e geçip gerçek bir resting
    # emre dönüşür. "new"/"accepted" ile aynı etiketi kullanmak, henüz hiç
    # pozisyon açılmamışken (dolayısıyla Canlı Pozisyonlar'da o hisse hiç
    # görünmezken) sanki aktif bir stop emri varmış izlenimi veriyordu.
    "held": "Pasif (Ana Emrin Dolmasını Bekliyor)",
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


REBUY_NOTE = "Alım-Stop-Alım: stop sonrası otomatik yeniden alım"


RELATIVE_STRENGTH_LABEL = "Relative Strength Rotasyonu"
ORB_SCAN_LABEL = "Açılış Aralığı Kırılımı (ORB)"
HA_INTRADAY_LABEL = "Heikin Ashi Gün İçi"


def _parse_order_tag(order: dict) -> tuple[str, str, bool]:
    """alpaca_buy_points.py tags buy-limit entries with client_order_id
    "algo-<algorithm_id>-<timeframe>-<symbol>-<epoch>" so historical orders
    stay attributed to whichever algorithm/mum periyodu was active when each
    was placed, even after the portfolio's settings later change. Orders
    placed before the timeframe was added to this tag have the older
    4-part "algo-<algorithm_id>-<symbol>-<epoch>" form (mum periyodu
    unknown). buy_stop_rebuy.py tags its yeniden alım (rebuy) market emirlerini
    aynı şekilde ama "rebuy-" öneki ile - bkz. o modülün docstring'i.
    relative_strength_core.py kendi emirlerini "rs-buy-<symbol>-<epoch>" /
    "rs-exit-<symbol>-<epoch>" ile, orb_core.py ise "orb-buy-<symbol>-<epoch>"
    ile, heikin_ashi_intraday_core.py "hai-buy/exit/eod-<symbol>-<epoch>" ile
    etiketler - bunlar da buy_algorithms.ALGORITHMS'a bağlı olmadığından
    (RS kesitsel bir strateji; ORB günlük bir tarama, ikisinin de tek bir
    "algoritma id"si yok) sabit bir etiketle gösterilir. Orders that aren't
    a tagged buy-limit/rebuy/rs/orb entry show "—" for both.
    Returns (algoritma_etiketi, mum_periyodu_etiketi, alım_stop_alım_mı)."""
    parts = (order.get("client_order_id") or "").split("-")
    if not parts or parts[0] not in ("algo", "rebuy", "rs", "orb", "hai"):
        return "—", "—", False
    if parts[0] == "rs":
        return RELATIVE_STRENGTH_LABEL, "—", False
    if parts[0] == "orb":
        return ORB_SCAN_LABEL, "—", False
    if parts[0] == "hai":
        return HA_INTRADAY_LABEL, "30 Dakika", False
    is_rebuy = parts[0] == "rebuy"
    if len(parts) == 5:
        algo_id, timeframe = parts[1], parts[2]
    elif len(parts) == 4:
        algo_id, timeframe = parts[1], None
    else:
        return "—", "—", False

    algo = ALGORITHMS.get(algo_id)
    algo_label = algo[0] if algo else "—"
    timeframe_label = TIMEFRAME_LABELS.get(timeframe, timeframe) if timeframe else "—"
    return algo_label, timeframe_label, is_rebuy


def format_order_row(order: dict) -> dict:
    created = _to_tr_time(order["created_at"])
    price = _order_price(order)
    algo_label, timeframe_label, is_rebuy = _parse_order_tag(order)

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
        "Açıklama": REBUY_NOTE if is_rebuy else "",
    }


def rebuy_row_style(df: pd.DataFrame) -> pd.DataFrame:
    """zebra_style'ın extra_style_fn'i - Alım-Stop-Alım Ek Yeteneği ile
    verilmiş yeniden alım emirlerinin ("Açıklama" sütunu dolu) satırını
    turuncu yazıyla vurgular. format_order_row kullanan her iki İşlem
    Geçmişi tablosunda da (bu modül ve premium_buy_portfolio.py) kullanılır."""
    return pd.DataFrame(
        [["color: orange;" if row.get("Açıklama") else "" for _ in df.columns] for _, row in df.iterrows()],
        index=df.index, columns=df.columns,
    )


def render_account_summary(client: AlpacaClient, username: str, positions: list[dict], show_initial_capital_setting: bool = True):
    """Nakit/toplam hesap değeri özeti ve ilk sermayeye göre anlık kâr -
    Genel Bakış (app.py) ve Alpaca Canlı Pozisyonlar sayfalarında ortak
    gösterilir, tek bir yerden hesaplanır. `show_initial_capital_setting=False`
    ile Giriş Sayfası'nda ayar bölümü gizlenir (o ayar ayrı bir yerde
    yönetilir), ancak kayıtlı sermayeye göre kâr hesaplaması yine yapılır."""
    account = client.get_account()
    cash = float(account["cash"])
    equity = float(account["equity"])  # nakit + tüm pozisyonların güncel piyasa değeri

    if 'initial_capital' not in st.session_state:
        st.session_state.initial_capital = load_initial_capital(username)

    if show_initial_capital_setting:
        with st.expander(
            "İlk Sermaye Ayarı",
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

    freshness_caption(f"Veri güncelliği: {datetime.now(TR_TZ):%d.%m.%Y %H:%M:%S} TRT (Alpaca'dan anlık çekildi).")
    st.dataframe(zebra_style(pd.DataFrame(rows)), use_container_width=True, hide_index=True)
    total_pnl = sum(d["pnl"] for d in realized.values())
    st.caption(
        f"Toplam gerçekleşen K/Z: ${total_pnl:,.2f}. Son {history_days} gün içinde alınıp satılan "
        "(kapanmış) pozisyonlar için, alış/satış dolum fiyatları zaman sırasına göre eşleştirilerek "
        "hesaplanır (aynı anda tek pozisyon açıldığı varsayımıyla). Halen açık olan pozisyonların "
        "gerçekleşmemiş kâr/zararı yukarıdaki tabloda ayrıca gösterilir."
    )


def render_alpaca_dashboard(username):
    key_id, secret_key, trading_url = get_user_alpaca(username)
    if not key_id or not secret_key:
        missing_keys_warning(username)
        return

    client = AlpacaClient(key_id, secret_key, trading_url)
    try:
        positions = client.get_all_positions()
        render_account_summary(client, username, positions)
    except Exception as e:
        st.warning(f"Alpaca hesap özeti alınamadı: {e}")
        return
    st.divider()

    # Fonksiyon içi import: trade_journal_page bu modülden TR_TZ alıyor (döngüsel import).
    from trade_journal_page import render_live_positions
    render_live_positions(username, positions)

    if positions:
        with st.expander("Stop-Loss Mantığı Nasıl Çalışır?"):
            st.markdown(
                "Yukarıdaki 'Güncel Stop $' sütunu, elle değil, aşağıdaki kurallarla otomatik "
                "yönetilen structure-based bir trailing-stop sistemini yansıtır:\n\n"
                "**[2026-09-28 güncellemesi - ayrıntılar: Algo Analiz > Değişiklik Günlüğü]**\n\n"
                "- İlk stop, hisse için seçili stop-loss algoritmasına göre kurulur (varsayılan: girişin "
                "%1.5 altı). Seçenek olarak eklenen **Oynaklık (ATR) Stop** seçilirse stop, giriş sinyalinin "
                "mum periyodundaki ATR'nin 2 katı aşağıya kurulur.\n"
                "- Breakeven artık fiyat 1R kadar (sabit-% algoritmalarda +%1.5; ATR stopunda bir bar "
                "kapanışı giriş + 1R'yi geçince) lehe gittiğinde devreye girer ve stop tam girişe değil "
                "girişin biraz üstüne çekilir.\n"
                "- Premium Buy Point hisselerinde stop, girişin kendi mum periyodunda izlenir "
                "(günlük sinyalle alınan hisse günlük barlarla).\n"
                "- Fiyat ilerledikçe stop yapısal trail (swing noktaları) ya da ATR stopunda "
                "chandelier trail (en yüksek fiyat - 3xATR) ile sıkılaştırılır; hiçbir zaman "
                "gevşetilmez ve güncel fiyatı geçmez.\n"
                "- Pozisyona ilave alım yapıldığında stopun adedi otomatik güncellenir.\n"
                "- **Açılış kalkanı:** Seans dışında (after-hours/pre-market) extended-hours guard "
                "stopu asıl seviyenin %4 altındaki bir *felaket stopuna* çeker; asıl seviye emrin "
                "etiketinde saklanır ('Güncel Stop $' sütunu bu asıl seviyeyi gösterir). Seans açılışından 15 dakika sonra "
                "stop asıl seviyeye geri döner; fiyat o seviyenin altındaysa pozisyon market "
                "emriyle kapatılır. Böylece açılışın ilk dakikalarındaki oynaklık stopu "
                "tetiklemez.\n"
                "- Guard, felaket stopu seans dışında kırılırsa onu *day + extended-hours limit "
                "emri* ile değiştirir ve Telegram'dan bildirir; bu acil emir dolmadan düşerse bir "
                "sonraki çalışmada koruma otomatik yeniden kurulur.\n"
                "- Seans dışında düşük hacimli barlarla stop sıkılaştırma (eski davranış) varsayılan "
                "olarak kapalı - Stop Loss Ayarları sayfasından açılabilir.\n"
                "- Tüm bu kontroller sunucuda (Droplet) normal seansta 5 dakikada, seans dışında "
                "10 dakikada bir otomatik çalışır - manuel müdahale gerekmez."
            )

    orders = client.get_recent_orders(days=HISTORY_DAYS)

    st.subheader("Kapanmış İşlemler - Gerçekleşen Kâr/Zarar")
    render_realized_pnl_table(client, orders, HISTORY_DAYS)

    st.subheader(f"Son {HISTORY_DAYS} Gün İşlem Geçmişi")

    history_rows = [format_order_row(o) for o in orders]

    if not history_rows:
        st.info(f"Son {HISTORY_DAYS} günde işlem yok.")
        return

    history_df = (
        pd.DataFrame(history_rows)
        .sort_values("_sort_ts", ascending=False)
        .drop(columns=["_sort_ts"])
    )
    freshness_caption(f"Veri güncelliği: {datetime.now(TR_TZ):%d.%m.%Y %H:%M:%S} TRT (Alpaca'dan anlık çekildi).")
    st.dataframe(zebra_style(history_df, extra_style_fn=rebuy_row_style), use_container_width=True, hide_index=True)
    st.caption(
        "Sütun başlıklarına tıklayarak sıralayabilirsiniz. Varsayılan sıralama: en yeni işlem en üstte. "
        "Kapanmış pozisyonlar da dahildir. Turuncu yazılı satırlar, Alım-Stop-Alım Ek Yeteneği ile stop "
        "sonrası otomatik yapılan yeniden alımları gösterir (bkz. 'Açıklama' sütunu)."
    )
