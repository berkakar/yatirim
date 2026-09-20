from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from alpaca_client import AlpacaClient
from alpaca_dashboard import format_order_row, TR_TZ
from alpaca_trailing_stop import get_bars_for_timeframe, TIMEFRAME
from backtest import TIMEFRAME_LABELS
from backtest_data import best_per_symbol_combo, load_results
from buy_algorithms import ALGORITHMS, DEFAULT_ALGORITHM, compute_all_signals, reject_if_marketable
from github_config import read_portfolio_config, write_portfolio_config
from stop_algorithms import DEFAULT_STOP_ALGORITHM, STOP_ALGORITHMS
from ui_style import zebra_style

GITHUB_REPO = "berkakar/yatirim"
BUY_LOOKBACK_DAYS = 60
DAILY_LOOKBACK_DAYS = 400  # SMA(200) icin yeterli gunluk bar (hafta sonu/tatil payi ile)
PRICE_REFRESH_SECONDS = 30


@st.fragment(run_every=PRICE_REFRESH_SECONDS)
def _render_buy_point_table(
    client: AlpacaClient, current_symbols: list[str], symbol_settings: dict[str, dict], default_algorithm: str,
    default_stop_algorithm: str, weights: dict[str, float], budget: float, stop_loss_enabled: bool,
    max_loss_pct: float | None,
):
    daily_start = datetime.now(timezone.utc) - timedelta(days=DAILY_LOOKBACK_DAYS)
    rows = []
    for symbol in current_symbols:
        settings = symbol_settings.get(symbol) or {}
        active_algorithm = settings.get("algorithm") or default_algorithm
        active_stop_algorithm = settings.get("stop_algorithm") or default_stop_algorithm
        if active_stop_algorithm not in STOP_ALGORITHMS:
            active_stop_algorithm = default_stop_algorithm
        timeframe = settings.get("timeframe") or TIMEFRAME

        start = datetime.now(timezone.utc) - timedelta(days=BUY_LOOKBACK_DAYS)
        bars = get_bars_for_timeframe(client, symbol, timeframe, start, exclude_forming=True)
        if not bars:
            continue
        try:
            live_price = client.get_latest_trade_price(symbol)
        except Exception:
            live_price = None
        current_price = live_price if live_price is not None else bars[-1].c

        try:
            daily_closes = [b["c"] for b in client.get_raw_bars(symbol, "1Day", daily_start.isoformat())]
        except Exception:
            daily_closes = []

        signals = compute_all_signals(bars, daily_closes)
        signals = {algo_id: reject_if_marketable(sig, current_price) for algo_id, sig in signals.items()}
        active_signal = signals.get(active_algorithm)
        has_position = client.get_position(symbol) is not None

        stopped_out = False
        if stop_loss_enabled and max_loss_pct and not has_position:
            dollar_amount = budget * (weights.get(symbol, 0) / 100)
            if dollar_amount > 0:
                try:
                    realized_loss = client.compute_realized_loss(symbol)
                except Exception:
                    realized_loss = 0.0
                stopped_out = (realized_loss / dollar_amount * 100) >= max_loss_pct

        row = {"Hisse": symbol, "Güncel Fiyat": round(current_price, 2), "Mum Periyodu": TIMEFRAME_LABELS.get(timeframe, timeframe)}
        for algo_id, (label, _) in ALGORITHMS.items():
            sig = signals.get(algo_id)
            row[label] = sig.price if sig else "—"
        row["Kullanılan Algoritma"] = ALGORITHMS[active_algorithm][0]
        row["Stop Loss Algoritması"] = STOP_ALGORITHMS[active_stop_algorithm].label
        row["Kullanılacak Fiyat"] = "—" if stopped_out else (active_signal.price if active_signal else "—")
        if stopped_out:
            row["Durum"] = "Zarar Kesildi"
        else:
            row["Durum"] = "Pozisyon Açık" if has_position else ("Bekleniyor" if active_signal else "Sinyal Yok")
        rows.append(row)

    st.dataframe(zebra_style(pd.DataFrame(rows)), use_container_width=True, hide_index=True)
    st.caption(
        f"Son güncelleme: {datetime.now(TR_TZ).strftime('%H:%M:%S')} TRT "
        f"({PRICE_REFRESH_SECONDS} saniyede bir otomatik yenilenir). "
        "Her algoritma sütunu, o hissenin kendi mum periyodundaki (\"Mum Periyodu\" sütunu) fiyatı gösterir "
        "(\"—\" = sinyal yok). 'Kullanılan Algoritma', 'Stop Loss Algoritması' ve 'Kullanılacak Fiyat', o "
        "hisse için yukarıda (Hisse Bazlı Algoritma Seçimi) seçtiğiniz - yoksa aşağıdaki (Varsayılan "
        "Algoritma / Risk Yönetimi) varsayılanlara göredir - gerçek alım GitHub Action "
        "tarafından 5 dakikalık taramada bu fiyat/algoritma/periyot ile yapılır. \"Zarar Kesildi\", Zarar Kes "
        "etkinken o hissenin kendi bütçesine göre gerçekleşen zararının eşiğe ulaştığı, yeni alım yapılmadığı "
        "anlamına gelir."
    )


def render_premium_buy_portfolio(target_list: list[str], username: str):
    user_alpaca = st.secrets.get("alpaca", {}).get(username, {})
    key_id = user_alpaca.get("key_id")
    secret_key = user_alpaca.get("secret_key")
    github_token = st.secrets.get("GITHUB_TOKEN")

    if not key_id or not secret_key:
        st.warning(f"'{username}' için Alpaca hesabı tanımlı değil (`.streamlit/secrets.toml` içinde `[alpaca.{username}]`).")
        return
    if not github_token:
        st.warning("`.streamlit/secrets.toml` içinde GITHUB_TOKEN tanımlı değil - portföy ayarları kaydedilemez.")
        return

    watchlist_name = f"premium-buy-portfolio-{username}"
    client = AlpacaClient(key_id, secret_key)
    watchlist = client.get_or_create_watchlist(watchlist_name)
    current_symbols = [a["symbol"] for a in watchlist.get("assets", [])]
    config = read_portfolio_config(GITHUB_REPO, github_token, username)

    # Hisse bazlı seçim (aşağıda) ve portföy geneli varsayılan (Risk Yönetimi
    # bölümündeki selectbox) aynı kaydedilmiş değeri paylaşır - burada bir kez
    # okunur.
    stop_algorithm_ids = list(STOP_ALGORITHMS.keys())
    current_stop_algorithm = config.get("stop_algorithm", DEFAULT_STOP_ALGORITHM)
    if current_stop_algorithm not in stop_algorithm_ids:
        current_stop_algorithm = DEFAULT_STOP_ALGORITHM

    with st.expander("📈 Alım Yaklaşımı Nasıl Çalışır?"):
        st.markdown(
            "- **Normal seansta (09:30-16:00 ET):** Watchlist'teki, pozisyonu olmayan her hisse için "
            "seçili algoritma taranır; sinyal varsa fiyatından bekleyen (GTC) bir limit-buy emri "
            "konur/güncellenir - **bracket (OTO)** olarak, yani emrin stop-loss bacağı (giriş fiyatının "
            "%1.5 altı) dolduğu anda Alpaca tarafında otomatik aktif olur, hiçbir gecikme olmaz.\n"
            "- **Pre-market (04:00-09:30 ET) / after-hours (16:00-20:00 ET):** Alpaca bu saatlerde "
            "bracket emirlere izin vermediği için, aynı sinyal fiyatından düz (bracket'sız) bir "
            "*day + extended-hours limit-buy* gönderilir. Ayrı bir mekanizma, kendi içinde birkaç "
            "dakika boyunca sıkı bir döngüyle dolup dolmadığını kontrol eder ve dolduğu anda naif "
            "%1.5'lik bir koruma stopu hemen kurar - bracket'ın anlık korumasına göre birkaç dakikalık "
            "bir gecikme olabilir, ama bir sonraki normal seans açılışını beklemekten çok daha hızlı.\n"
            "- Normal seans başladığında, extended hours'ta yerleştirilmiş ama henüz dolmamış bir "
            "emir varsa sistem onu otomatik olarak bracket'a **yükseltir** - böylece daha sonra ne "
            "zaman dolarsa dolsun korumasız kalmaz.\n"
            "- **İlave alım (top-up)** şu an yalnızca normal seansta çalışıyor (aşağıdaki kutuda "
            "anlatıldığı gibi market emriyle) - extended hours'ta henüz desteklenmiyor, market "
            "emirleri de o saatlerde çalışmıyor.\n"
            "- Zarar Kes (portföy bazlı) ve nakit/bütçe sınırlamaları her iki modda da aynı şekilde "
            "uygulanır."
        )

    st.subheader("🎯 Portföy Seçimi")
    st.caption("Bu listedeki hisseler için premium buy point (demand zone) taranır ve fiyat oraya ulaştığında otomatik alım yapılır.")

    # Alım Bölgesi Tarama sayfasından "Aktar" ile gelen hisseler - tek seferlik
    # olarak tüketilir, tekrar bu sayfaya dönüldüğünde normal (kayıtlı) seçim
    # davranışı bozulmasın diye. Aşağıdaki iki data_editor'ün key'i bu sayaca
    # bağlı: sayaç arttıkça Streamlit onları SIFIRDAN bir widget sayar (eski
    # satır-indeksine bağlı düzenleme/seçim durumunu miras almaz) - sadece
    # session_state'teki eski durumu silmeye güvenmek, satır sırası/sayısı
    # değiştiğinde yanlış satıra eski bir değerin yapışmasına yol açabiliyordu.
    if "premium_buy_picker_token" not in st.session_state:
        st.session_state["premium_buy_picker_token"] = 0
    # Aktarılan hisseler kaydedilene kadar (yani gerçekten watchlist'e yazılana
    # kadar) burada "taşınır" - session_state.pop ile TEK SEFERLİK tüketilseydi,
    # kullanıcı Kaydet'e basmadan önce sayfada başka bir widget'la etkileşime
    # girdiğinde (örn. ağırlığı düzenlerken - ki sayfa tam olarak bunu istiyor)
    # bu, current_symbols/target_list'te henüz olmayan yeni hisseleri
    # picker_symbols'tan sessizce düşürüyordu ("aktarıldı ama kaydedilince hiçbir
    # şey olmadı" hatasının kök nedeni buydu).
    if "premium_buy_transfer_carry" not in st.session_state:
        st.session_state["premium_buy_transfer_carry"] = []

    new_transfer = st.session_state.pop("premium_buy_pending_transfer", None) or []
    # Otomatik Alım/Satım modülünden aktarılan hisseler için önerilen algoritma/mum
    # periyodu - Alım Bölgesi Tarama'nın Aktar akışı bunu hiç set etmediği için
    # (boş dict), aşağıdaki varsayılan hesaplama mantığı o akış için değişmeden
    # kalır. Bu, transfer_carry'nin aksine sadece widget'ın İLK oluşturulduğu
    # anda okunuyor, o yüzden tek seferlik tüketimi sorun değil.
    pending_symbol_settings = st.session_state.pop("premium_buy_pending_symbol_settings", None) or {}

    if new_transfer:
        st.session_state["premium_buy_transfer_carry"] = list(
            dict.fromkeys(st.session_state["premium_buy_transfer_carry"] + new_transfer)
        )
        st.session_state["premium_buy_picker_token"] += 1
        st.success(
            f"✅ {len(new_transfer)} hisse aktarıldı: {', '.join(new_transfer)} — aşağıda seçili "
            "olarak işaretlendi. **Bu henüz kaydedilmedi**: portföye eklemek için ağırlıkları/algoritmaları "
            "gözden geçirip sayfanın altındaki 💾 Portföyü Kaydet butonuna basmanız gerekiyor."
        )
    pending_transfer = st.session_state["premium_buy_transfer_carry"]

    # current_symbols (Alpaca'daki gerçek watchlist) her zaman satır listesine
    # dahil edilir - aksi halde, "Piyasa Seçimi" değişik bir piyasadayken
    # (veya Aktar ile gelen bir hisse target_list'te hiç yoksa) kaydedilmiş bir
    # hisse tabloda görünmeyip, bir sonraki "Portföyü Kaydet" tıklamasında
    # (o an ekranda olmadığı için seçili sayılmayıp) watchlist'ten sessizce
    # düşerdi - "aktarıyor ama kaydedince kayboluyor" hatasının kök nedeni buydu.
    picker_symbols = list(dict.fromkeys(target_list + current_symbols + pending_transfer))
    picker_token = st.session_state["premium_buy_picker_token"]

    picker_df = pd.DataFrame({"Hisse": picker_symbols})
    picker_df["Seçili"] = picker_df["Hisse"].isin(current_symbols) | picker_df["Hisse"].isin(pending_transfer)
    edited_picker = st.data_editor(
        picker_df,
        column_config={"Seçili": st.column_config.CheckboxColumn(required=True)},
        hide_index=True,
        use_container_width=True,
        key=f"premium_buy_symbol_picker_{picker_token}",
    )
    selected_symbols = edited_picker[edited_picker["Seçili"]]["Hisse"].tolist()

    st.subheader("💰 Bütçe ve Hisse Ağırlıkları")
    try:
        live_cash = float(client.get_account()["cash"])
    except Exception:
        live_cash = None

    budget = st.number_input(
        "Toplam portföy bütçesi ($)", min_value=0.0,
        value=float(live_cash if live_cash is not None else (config.get("budget") or 0)), step=100.0,
        key="pbp_budget",
        help="Sayfa her açıldığında Alpaca'daki güncel nakit bakiyeyle önceden doldurulur - isterseniz "
             "aşağı çekip bir kısmını nakitte tutabilirsiniz, ama bu tutar hesaptaki nakti aşamaz.",
    )
    if live_cash is None:
        st.caption("⚠️ Alpaca'daki güncel nakit bakiye alınamadı - bütçe sınırı bu sayfada kontrol edilemiyor.")
    elif budget > live_cash:
        st.warning(
            f"Girdiğiniz bütçe (${budget:,.2f}), Alpaca'daki güncel nakit bakiyeyi (${live_cash:,.2f}) "
            "aşıyor. Bu haliyle kaydedilemez - lütfen bütçeyi bu tutarın altına indirin."
        )
    else:
        st.caption(f"Alpaca'daki güncel nakit bakiye: ${live_cash:,.2f}.")

    edited_weights = pd.DataFrame(columns=["Hisse", "Ağırlık %"])
    if selected_symbols:
        existing_weights = config.get("weights") or {}

        # Yeni aktarılan hisselerin ağırlığı: toplam bütçenin, DAHA ÖNCE
        # portföy yüzdesi belirlenmiş (bu aktarımdaki semboller HARİÇ)
        # hisselere ayrılan kısmı düşüldükten sonra kalan payı, aktarılan
        # hisse sayısına eşit bölerek (toplam bütçeye göre yüzde olarak)
        # hesaplanır - örn. mevcut hisseler zaten %70 kullanıyorsa ve 3 yeni
        # hisse aktarıldıysa, her biri kalan %30'un üçte birini (%10) alır.
        already_allocated_pct = sum(
            existing_weights.get(s, 0.0) for s in selected_symbols if s not in pending_transfer
        )
        remaining_pct = max(0.0, 100.0 - already_allocated_pct)
        new_transfer_weight_pct = round(remaining_pct / len(pending_transfer), 2) if pending_transfer else 0.0

        def _default_weight_pct(symbol: str) -> float:
            # Alpaca'da hâlâ açık bir pozisyonu olan hisseler için GERÇEK güncel
            # ağırlığı (yatırılan tutar = adet × ortalama giriş / bütçe) gösterir -
            # böylece bu alan, bütçe veya pozisyon değiştikçe gerçeği yansıtır ve
            # top-up için ne kadar yer kaldığını doğru gösterir. Pozisyonu olmayan
            # ama daha önce kaydedilmiş bir ağırlığı olan hisseler o kayıtlı
            # değeri korur. Yeni aktarılan (henüz pozisyonu/kayıtlı ağırlığı
            # olmayan) hisseler kalan payın eşit bölüşümünü alır.
            try:
                position = client.get_position(symbol)
            except Exception:
                position = None
            if position is not None and budget > 0:
                invested = float(position["qty"]) * float(position["avg_entry_price"])
                return round(invested / budget * 100, 2)
            if symbol in pending_transfer:
                return new_transfer_weight_pct
            return float(existing_weights.get(symbol, 0.0))

        weight_df = pd.DataFrame({
            "Hisse": selected_symbols,
            "Ağırlık %": [_default_weight_pct(s) for s in selected_symbols],
        })
        edited_weights = st.data_editor(
            weight_df,
            hide_index=True,
            use_container_width=True,
            key=f"premium_buy_weight_editor_{picker_token}",
            column_config={"Ağırlık %": st.column_config.NumberColumn(min_value=0.0, max_value=100.0, step=1.0)},
        )
        st.caption(
            "Alpaca'da hâlâ açık pozisyonu olan hisseler için ağırlık, o hisseye şu ana kadar yatırılmış "
            "tutarın (adet × ortalama giriş) toplam bütçeye oranı olarak otomatik gelir - kaydetmeden önce "
            "istediğiniz gibi değiştirebilirsiniz."
        )
        st.caption(
            "ℹ️ Zaten açık bir pozisyonu olan hissede ağırlığı düşürmek Alpaca'da otomatik satış/azaltma "
            "yapmaz - sistem mevcut hisseleri satıp portföyü yeni ağırlığa küçültmez. Tek etkisi, o hisseye "
            "artık ilave alım yapılmamasıdır (yatırılan tutar zaten yeni hedefin üzerinde kaldığı sürece); "
            "pozisyon, kendi stop-loss'u (Trailing Stop modülü) tetiklenene ya da elle kapatılana kadar "
            "olduğu gibi açık kalır."
        )
        total_weight = edited_weights["Ağırlık %"].sum()
        if abs(total_weight - 100) < 0.01:
            st.caption(f"Toplam ağırlık: %{total_weight:.1f}")
        else:
            st.warning(f"Toplam ağırlık: %{total_weight:.1f} — %100 olması önerilir, aksi halde bütçe tam kullanılmaz veya aşılır.")
    else:
        st.info("Portföye en az bir hisse seçin.")

    st.subheader("🧠 Hisse Bazlı Algoritma Seçimi")
    st.caption(
        "Her hisse için, o hissede daha önce BackTest modülünde ya da Alım Bölgesi Tarama'da "
        "çalıştırılmış algoritma + mum periyodu + veri kaynağı kombinasyonları K/Z %'ye göre en "
        "yüksekten başlayarak listelenir. Seçtiğiniz kombinasyon, o hisse için otomatik alım/satımda "
        "kullanılır (canlı alım her durumda Alpaca üzerinden yapılır - kaynak sadece o kombinasyonun "
        "hangi veriyle geriye dönük test edildiğini gösterir). BackTest sonucu olmayan hisseler, "
        "aşağıdaki varsayılan algoritma ve mum periyoduyla (30 Dakika) taranır. Her hisse için ayrıca "
        "hangi stop-loss algoritmasının kullanılacağı da seçilebilir - seçilmezse Risk Yönetimi "
        "bölümündeki portföy geneli varsayılan uygulanır."
    )
    existing_symbol_settings = config.get("symbol_settings") or {}
    symbol_settings: dict[str, dict] = {}
    if selected_symbols:
        all_backtest_results = load_results(username)
        head_sym, head_algo, head_stop = st.columns([1, 2.6, 1.8])
        head_sym.markdown("**Hisse**")
        head_algo.markdown("**Buy-Point Algoritması**")
        head_stop.markdown("**Stop-Loss Algoritması**")
        for symbol in selected_symbols:
            combos = best_per_symbol_combo(all_backtest_results, symbol)
            col_sym, col_combo, col_stop = st.columns([1, 2.6, 1.8])
            col_sym.markdown(f"**{symbol}**")
            saved = pending_symbol_settings.get(symbol) or existing_symbol_settings.get(symbol) or {}
            settings_for_symbol: dict = {}

            if combos:
                options = [
                    f"{ALGORITHMS[c['algorithm']][0]} · {TIMEFRAME_LABELS.get(c['timeframe'], c['timeframe'])} · "
                    f"K/Z %{(c.get('pnl_pct') or 0):.2f} · Kaynak: {c.get('source') or 'Alpaca'}"
                    for c in combos
                ]
                default_idx = 0
                for i, c in enumerate(combos):
                    if c["algorithm"] == saved.get("algorithm") and c["timeframe"] == saved.get("timeframe"):
                        default_idx = i
                        break
                picked_label = col_combo.selectbox(
                    f"{symbol} için algoritma seçimi", options, index=default_idx,
                    key=f"symbol_algo_{symbol}", label_visibility="collapsed",
                )
                picked = combos[options.index(picked_label)]
                settings_for_symbol["algorithm"] = picked["algorithm"]
                settings_for_symbol["timeframe"] = picked["timeframe"]
            else:
                col_combo.caption("BackTest sonucu yok — varsayılan algoritma kullanılacak.")

            saved_stop_algorithm = saved.get("stop_algorithm")
            stop_default_idx = (
                stop_algorithm_ids.index(saved_stop_algorithm) if saved_stop_algorithm in stop_algorithm_ids
                else stop_algorithm_ids.index(current_stop_algorithm)
            )
            picked_stop_algorithm = col_stop.selectbox(
                f"{symbol} için stop-loss algoritması", stop_algorithm_ids, index=stop_default_idx,
                format_func=lambda k: STOP_ALGORITHMS[k].label,
                key=f"symbol_stop_algo_{symbol}", label_visibility="collapsed",
            )
            settings_for_symbol["stop_algorithm"] = picked_stop_algorithm

            symbol_settings[symbol] = settings_for_symbol

    st.subheader("⚙️ Varsayılan Algoritma")
    st.caption("BackTest sonucu olmayan hisseler için kullanılan varsayılan algoritmadır.")
    algorithm_ids = list(ALGORITHMS.keys())
    current_algorithm = config.get("algorithm", DEFAULT_ALGORITHM)
    if current_algorithm not in algorithm_ids:
        current_algorithm = DEFAULT_ALGORITHM
    selected_algorithm = st.selectbox(
        "Varsayılan algoritma:",
        algorithm_ids,
        index=algorithm_ids.index(current_algorithm),
        format_func=lambda k: ALGORITHMS[k][0],
    )

    st.subheader("🛑 Risk Yönetimi")
    selected_stop_algorithm = st.selectbox(
        "Stop-Loss Algoritması",
        stop_algorithm_ids,
        index=stop_algorithm_ids.index(current_stop_algorithm),
        format_func=lambda k: STOP_ALGORITHMS[k].label,
        key="pbp_stop_algorithm",
        help="Bir pozisyon açıldığında ilk stop'un nereye kurulacağını ve zamanla nasıl "
             "sıkılaştırılacağını (trailing) belirler - hem canlı alım (bu modül + Trailing Stop "
             "modülü) hem BackTest için geçerlidir, ikisi de aynı algoritmayı kullanır. Yukarıdaki "
             "Hisse Bazlı Algoritma Seçimi'nde bir hisse için ayrı bir stop-loss algoritması "
             "seçilmediyse, o hisse için buradaki varsayılan kullanılır.",
    )

    sl1, sl2 = st.columns([1, 2])
    stop_loss_enabled = sl1.checkbox(
        "Zarar Kes", value=bool(config.get("stop_loss_enabled")), key="pbp_stop_loss_enabled",
        help="Etkinleştirilirse, bir hissenin kendi bütçesine (ağırlığına göre ayrılan tutara) göre "
             "gerçekleşen (kapanmış işlemlerdeki) toplam zararı girilen yüzdeye ulaştığında, o hisse için "
             "yeni alım yapılmaz - nakitte kalınır. Açık pozisyon varsa kendi stop'uyla yönetilmeye devam eder.",
    )
    max_loss_pct = sl2.number_input(
        "Maksimum zarar yüzdesi", min_value=0.1, max_value=100.0,
        value=float(config.get("max_loss_pct") or 10.0), step=0.5,
        disabled=not stop_loss_enabled, key="pbp_max_loss_pct",
        help="Hissenin kendi bütçesine göre gerçekleşen zararı bu yüzdeye ulaştığında, o hisse için yeni "
             "alım durdurulur.",
    )

    st.info(
        "💡 **İlave Alım (Top-up) nasıl çalışır?** Bir hissede pozisyon zaten açıkken bütçe artırılıp "
        "ağırlık sabit bırakılırsa ve algoritmanın sinyal fiyatı güncel fiyata yakınsa (%0.5 içinde), "
        "sistem aradaki farkı otomatik tamamlar. Bu, **taze bir girişten farklı** işler: taze giriş "
        "bekleyen (resting) bir limit emridir, ama pozisyonu koruyan stop-sell emri zaten resting "
        "durumdayken aynı sembolde zıt yönde ikinci bir resting emir Alpaca tarafından \"wash trade\" "
        "sayılıp reddedilir. Bu yüzden ilave alım **market emriyle anında** dolduruluyor: stop birkaç "
        "saniyeliğine iptal edilip, alım gerçekleşir gerçekleşmez yeni toplam adede göre hemen yeniden "
        "kuruluyor (alım başarısız olursa da stop eski haliyle geri kuruluyor - pozisyon hiçbir adımda "
        "korumasız kalmıyor)."
    )
    top_up_stop_options = ["keep", "tighten_to_new_entry"]
    top_up_stop_labels = {
        "keep": "Mevcut haliyle bırak - sadece adet genişler, stop seviyesi değişmez",
        "tighten_to_new_entry": "Yeni ortalama girişe göre nefes payı ekle - sadece stop'u sıkılaştırırsa uygulanır",
    }
    top_up_stop_mode = st.radio(
        "İlave Alım (Top-up) sonrası stop davranışı",
        top_up_stop_options,
        index=top_up_stop_options.index(config.get("top_up_stop_mode") or "keep"),
        format_func=lambda v: top_up_stop_labels[v],
        key="pbp_top_up_stop_mode",
        help="İlave alım market emriyle dolduktan hemen sonra, tek resting stop yeni toplam adede göre "
             "yeniden kuruluyor - bu seçenek sadece o yeniden kurulan stop'un FİYATINI belirler: aynı "
             "seviyede mi kalsın, yoksa yeni (top-up ile harmanlanmış) ortalama girişe göre sıkılaştırılsın "
             "mı - hiçbir durumda mevcut korumayı gevşetmez.",
    )

    weights_map = {row["Hisse"]: float(row["Ağırlık %"]) for _, row in edited_weights.iterrows()}

    if st.button("💾 Portföyü Kaydet", type="primary"):
        if live_cash is not None and budget > live_cash:
            st.error(
                f"Bütçe (${budget:,.2f}), Alpaca'daki nakit bakiyeyi (${live_cash:,.2f}) aşıyor - kaydedilmedi. "
                "Lütfen bütçeyi indirin ve tekrar deneyin."
            )
        else:
            client.set_watchlist_symbols(watchlist["id"], selected_symbols)
            st.session_state["premium_buy_transfer_carry"] = []
            new_config = {
                "budget": float(budget),
                "weights": weights_map,
                "algorithm": selected_algorithm,
                "stop_algorithm": selected_stop_algorithm,
                "symbol_settings": symbol_settings,
                "stop_loss_enabled": bool(stop_loss_enabled),
                "max_loss_pct": float(max_loss_pct) if stop_loss_enabled else None,
                "top_up_stop_mode": top_up_stop_mode,
            }
            write_portfolio_config(GITHUB_REPO, github_token, new_config, username)
            st.success("Portföy kaydedildi.")
            st.rerun()

    if not current_symbols:
        return

    st.subheader("📍 Premium Buy Point Karşılaştırması")
    _render_buy_point_table(
        client, current_symbols, symbol_settings, selected_algorithm, selected_stop_algorithm,
        weights_map, float(budget), bool(stop_loss_enabled), float(max_loss_pct) if stop_loss_enabled else None,
    )

    st.subheader("📜 Son 30 Gün Alım/Satım Emirleri")
    history_rows = [
        format_order_row(o) for o in client.get_recent_orders(days=30) if o["symbol"] in current_symbols
    ]
    if not history_rows:
        st.info("Son 30 günde bu portföy için işlem yok.")
        return

    history_df = (
        pd.DataFrame(history_rows)
        .sort_values("_sort_ts", ascending=False)
        .drop(columns=["_sort_ts"])
    )
    st.dataframe(zebra_style(history_df), use_container_width=True, hide_index=True)
