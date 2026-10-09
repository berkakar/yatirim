"""Analiz > "Yapay Zeka Analiz Modülü" - NASDAQ 100 hissesi için transformer
eğitim veri setini hazırlar, tabloda gösterir ve veritabanına kaydeder
(hesaplama ve kayıt: ai_dataset.py)."""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import streamlit as st

import ai_dataset as ad
from ui_style import freshness_caption

TR_TZ = ZoneInfo("Europe/Istanbul")

# Görünen sütunları seçmek için gruplar (sıra tablodaki sırayla aynı).
_GROUPS = (
    ("Fiyat & Hacim", lambda c: c in ("open", "high", "low", "close", "volume", "vwap", "ret_1d_pct")
     or c in ad.RELATIVE_COLS),
    ("EMA", lambda c: c.startswith("ema") or c.startswith("dist_ema")),
    ("Hisse Duyarlılığı", lambda c: c.startswith("sent_") or c == "rsi14"),
    ("Direnç", lambda c: c.startswith("resistance_")),
    ("Değerleme", lambda c: c.startswith("valuation_") and c not in ad.META_COLS),
    ("NASDAQ 100 Parametreleri", lambda c: c.startswith(f"{ad.MARKET_PREFIX}__")),
    ("Tüm Sektör ETF'leri", lambda c: c.partition("__")[0].upper() in ad.sector_etf_symbols()),
    ("Zaman (Temporal Embedding)", lambda c: c in ad.TEMPORAL_COLS),
    ("Meta (eğitime girmez)", lambda c: c in ad.META_COLS),
)
_DEFAULT_GROUPS = ("Fiyat & Hacim", "EMA", "Hisse Duyarlılığı", "Direnç", "Değerleme",
                   "NASDAQ 100 Parametreleri", "Tüm Sektör ETF'leri")

_GLOSSARY = """
| Sütun | Açıklama |
|---|---|
| `sub_sector` | *Meta.* Hissenin alt sektörü (iş modeli grubu; Değerleme modülündeki "İş Modeli Grubu") |
| `ret_*` | Getiri (return), %: `ret_1d_pct` hissenin günlük getirisi; ETF'lerde `ret_1d / ret_5d / ret_21d` 1 / 5 / 21 işlem günlük getiri |
| `gap_open_pct`, `range_pct`, `close_vs_vwap_pct`, `volume_rel20` | Göreli fiyat / hacim: açılışın önceki kapanışa göre % farkı, gün içi aralık (yüksek − düşük) / kapanış %, kapanışın VWAP'a göre % farkı, hacim / önceki 20 günün ortalama hacmi |
| `open, high, low, close, volume` | Yahoo günlük barı (bölünme/temettü düzeltmeli) |
| `vwap` / `vwap_is_proxy` | Alpaca günlük VWAP; *meta* `vwap_is_proxy`=1 ise o gün Alpaca verisi yok, tipik fiyat (Y+D+K)/3 kullanıldı |
| `ema20/50/200`, `dist_emaN_pct` | Üssel hareketli ortalamalar ve kapanışın onlara % uzaklığı |
| `sent_momentum`, `sent_volatility`, `rsi14` | Hisse duyarlılığı bileşenleri 0-100: 50 günlük ortalamaya göre momentumun 126 günlük yüzdelik sırası, oynaklığın ters yüzdelik sırası, RSI(14) |
| `sent_momentum_raw` | Kapanışın 50 günlük basit ortalamaya göre % uzaklığı (`sent_momentum` bunun 126 günlük yüzdelik sırası). Tabloda var, eğitim verisinde yok: `dist_ema50_pct` ile neredeyse aynı |
| `resistance_Nm`, `resistance_Nm_dist_pct` | N = 1/2/3 ay (21/42/63 işlem günü) geriye bakışta, kapanışın üstündeki en yakın tepe (yoksa pencerenin zirvesi) ve kapanışa % uzaklığı |
| `resistance_nearest*` | Üç seviyeden fiyata en yakını, % uzaklığı ve hangi pencereden geldiği (ay). Tabloda var, eğitim verisinde yok: üç pencerenin uzaklığından birebir seçilir |
| `valuation_*` | Ucuzluk Skoru (Nihai Skor) ve bileşenleri: alt sektör F/K iskontosu, F/K, büyüme, kârlılık (ROE, ROA, net/brüt marj), faiz karşılama, borçluluk, cari/likidite oranı, varlık devir hızı - o gün veya öncesindeki son günlük kayıt (`valuation_scores_daily`) |
| *Eğitim verisindeki değerleme sütunları* | `valuation_score`, `valuation_sector_discount_pct`, `valuation_eps_growth_pct`, `valuation_revenue_growth_pct`, `valuation_current_ratio`, `valuation_net_margin_pct`, `valuation_debt_equity`; diğer değerleme sütunları yalnızca tabloda |
| `valuation_is_reconstructed` | *Meta.* 1: skor servisten değil, geçmiş bilanço tablolarından yeniden hesaplandı. Kârlılık çeyreklik tablolardan (açıklama gününden itibaren, basamak); diğer oranlar bilanço noktaları ile bugünkü değer arasında interpolasyonlu; F/K günlük fiyat / son 12 ay EPS; alt sektör ortalama F/K her gün aynı alt sektördeki hisselerin geçmiş F/K'larının medyanı. PEG skora ve veri setine katılmaz |
| `valuation_is_snapshot` | *Meta.* 1: günlük geçmiş o güne uzanmıyor, en eski bilinen skor yazıldı |
| `nasdaq_100__*` | Piyasa Duyarlılığı arşivi (sentiment_daily): skor, 5 bileşen ve ham değerleri, endeks kapanışı |
| `<etf>__*` | 11 sektör ETF'si + SPY (sector_etf_daily): kapanış ve 1 / 5 / 21 günlük getiri. Eğitim verisinde yalnızca `ret_1d` |
| `time_idx, month, day_of_month, day_of_week, is_month_start/end` | Temporal embedding için takvim indeksleri (eğitim verisinde bunlar) |
| `year, day_of_year, week_of_year, quarter`, `*_sin, *_cos` | Tabloda var, eğitim verisinde yok: diğer takvim sütunlarından türer / aynı bilginin döngüsel kodlaması |
| `interpolated_cells` | *Meta.* O satırda interpolasyonla doldurulan hücre sayısı |
| `source` | *Meta.* `backfill`: ilk hazırlama · `daily`: sonradan eklenen gün |
| *Çıkarılanlar* | `sector_etf__*` (kendi ETF'sinin kopyası), `<etf>__rel_5d_vs_benchmark` (= ETF ret_5d − SPY ret_5d), `nasdaq_100__score` (5 bileşenin ortalaması), `stock_sentiment` (3 bileşenin ortalaması) - diğer sütunlardan türetilebildikleri için veri setinde yok |
| *Eğitim verisinde olmayan seviyeler* | `open, high, low, vwap, volume`, `ema20/50/200`, direnç seviyeleri, ETF kapanışları, `nasdaq_100__index_close` - zamanla kayan ve kapanışla ~0,99 korelasyonlu fiyat seviyeleri; yerlerine yüzde / getiri karşılıkları girer. `close` kalır (hedef değişken ve fiyata geri çevirmek için) |
| *Meta sütunlar* | Tabloda "Meta (eğitime girmez)" grubunda; eğitim verisi CSV'sine ve `ai_dataset.py export`'a girmez (`--all-columns` ile girer) |
"""


def _fmt_time(iso_text):
    if not iso_text:
        return "—"
    try:
        dt = datetime.strptime(iso_text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=ZoneInfo("UTC"))
    except ValueError:
        return iso_text
    return f"{dt.astimezone(TR_TZ):%d.%m.%Y %H:%M} TRT"


def _alpaca_vwap_fetcher(username):
    """Kullanıcının Alpaca anahtarlarıyla (Hesabım; yoksa ortam değişkenleri)
    VWAP çekici - anahtar yoksa None."""
    try:
        import alpaca_account
        from alpaca_client import DEFAULT_DATA_URL, AlpacaClient

        mode = alpaca_account.load_account_mode(username)
        key_id, secret = alpaca_account.user_keys(username, mode)
        if not (key_id and secret):
            key_id, secret = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
        if not (key_id and secret):
            return None
        client = AlpacaClient(key_id, secret, alpaca_account.trading_url(mode),
                              os.environ.get("APCA_API_DATA_URL", DEFAULT_DATA_URL))
        return ad.alpaca_vwap_fetcher(client)
    except Exception:
        return None


def _archive_coverage():
    """Günlük arşivdeki NASDAQ 100 ve sektör satırlarının tarih aralığı."""
    import market_archive

    try:
        rows = market_archive.summary()
    except Exception:
        return None, None
    sent = next((r for r in rows if r["table"] == market_archive.SENTIMENT_TABLE and r["key"] == ad.MARKET), None)
    sectors = [r for r in rows if r["table"] == market_archive.SECTOR_TABLE]
    sec = None
    if sectors:
        sec = {"first": max(r["first"] for r in sectors), "last": min(r["last"] for r in sectors),
               "rows": sum(r["rows"] for r in sectors)}
    return sent, sec


def _render_archive_status(keep_years):
    sent, sec = _archive_coverage()
    need = (pd.Timestamp.now().normalize() - pd.DateOffset(years=keep_years)).strftime("%Y-%m-%d")
    c1, c2 = st.columns(2)
    c1.caption(f"NASDAQ 100 duyarlılık arşivi: **{sent['first']} → {sent['last']}** ({sent['rows']} gün)"
               if sent else "NASDAQ 100 duyarlılık arşivi: **boş**")
    c2.caption(f"Sektör ETF arşivi: **{sec['first']} → {sec['last']}**" if sec else "Sektör ETF arşivi: **boş**")
    short = (not sent or sent["first"] > need) or (not sec or sec["first"] > need)
    if short:
        st.warning(f"Günlük arşiv {keep_years} yıllık veri setini kapsamıyor ({need} öncesi eksik). Eksik "
                   "günler interpolasyon/taşımayla doldurulur; doğru veri için önce arşivi doldurun.")
        if st.button(f"Arşivi {keep_years} yıllık doldur (NASDAQ 100 + sektör ETF'leri)", key="ai_archive_backfill",
                     help="Yahoo'dan NASDAQ 100 hisseleri ve sektör ETF'leri indirilir - birkaç dakika sürebilir."):
            import market_sentiment as ms

            with st.spinner("Arşiv dolduruluyor..."):
                failures = ms.backfill(["nasdaq100"], years=keep_years, sectors=True)
            if failures:
                st.error("Arşivin bir kısmı doldurulamadı - Yahoo'ya ulaşılamamış olabilir.")
            else:
                st.success("Arşiv dolduruldu.")
                st.rerun()


def _render_create(username, nasdaq_tickers):
    st.subheader("1. Veri Setini Hazırla")
    options = sorted(dict.fromkeys(ad.normalize_ticker(t) for t in nasdaq_tickers if t))
    c1, c2, c3, c4 = st.columns([2, 2, 1, 1])
    sub_sectors = _sub_sectors()
    picked = c1.selectbox("NASDAQ 100 hissesi", options, key="ai_ticker_pick",
                          format_func=lambda t: f"{t} · {sub_sectors[t]}" if sub_sectors.get(t) else t) \
        if options else None
    typed = c2.text_input("veya sembol yazın", key="ai_ticker_typed", placeholder="örn. AAPL").strip()
    fetch_years = c3.number_input("Çekilecek yıl", min_value=3, max_value=10, value=ad.DEFAULT_FETCH_YEARS,
                                  key="ai_fetch_years", help="En az 3 yıl: ilk yıl EMA200 ve göstergelerin ısınması için.")
    keep_years = c4.number_input("Saklanacak yıl", min_value=1, max_value=int(fetch_years) - 1,
                                 value=min(ad.DEFAULT_KEEP_YEARS, int(fetch_years) - 1), key="ai_keep_years")
    ticker = ad.normalize_ticker(typed or picked)

    o1, o2, o3 = st.columns(3)
    use_alpaca = o1.checkbox("VWAP'ı Alpaca'dan al", value=True, key="ai_use_alpaca",
                             help="Alpaca anahtarı yoksa ya da veri gelmezse tipik fiyat (Y+D+K)/3 kullanılır.")
    fetch_val = o2.checkbox("Değerleme skoru yoksa Yahoo'dan çek", value=True, key="ai_fetch_valuation",
                            help="Hissenin NASDAQ 100 Ucuzluk Skoru veritabanında yoksa Değerleme modülündeki "
                                 "gibi anlık çekilir ve kaydedilir.")
    reconstruct = o3.checkbox("Skor geçmişini bilançolardan hesapla", value=True, key="ai_reconstruct",
                              help="Servisin yazmadığı geçmiş günlerin Ucuzluk Skoru Yahoo'nun çeyreklik / yıllık "
                                   "bilanço tablolarından yeniden hesaplanıp valuation_scores_daily'ye yazılır "
                                   "(servis günlerine dokunulmaz).")
    _render_archive_status(int(keep_years))

    existing = ad.get_dataset_info(ticker) if ticker else None
    if existing:
        st.info(f"{ticker} için kayıtlı veri seti var ({existing['start']} → {existing['end']}). Yeniden hazırlamak "
                "kayıtlı günlerin üzerine yazar; elle eklenen alanlar korunur. Yalnızca yeni günler için "
                "aşağıdaki **Yeni günleri ekle** düğmesini kullanın.")
    if not st.button("Veriyi Hazırla ve Kaydet", type="primary", disabled=not ticker, key="ai_build"):
        return
    with st.status(f"{ticker} veri seti hazırlanıyor...", expanded=True) as status:
        try:
            fetcher = _alpaca_vwap_fetcher(username) if use_alpaca else None
            if use_alpaca and fetcher is None:
                st.write("Alpaca anahtarı bulunamadı - VWAP için tipik fiyat kullanılacak.")
            df, meta = ad.create(ticker, int(fetch_years), int(keep_years), vwap_fetcher=fetcher,
                                 fetch_valuation=fetch_val, reconstruct_history=reconstruct, progress=lambda m: st.write(f"• {m}"))
        except Exception as e:
            status.update(label=f"{ticker}: hazırlanamadı", state="error")
            st.error(str(e))
            return
        status.update(label=f"{ticker}: {len(df)} gün × {len(df.columns)} sütun kaydedildi", state="complete")
    st.session_state["ai_view_select"] = ticker
    st.session_state["ai_last_warnings"] = {ticker: meta["warnings"]}


def _sub_sectors() -> dict:
    """{hisse: alt sektör (iş modeli)} - NASDAQ 100 değerleme tablosundan."""
    try:
        import valuation_db

        return {t: r["scored"].get("Alt Sektör (İş Modeli)") for t, r in valuation_db.get_rows(ad.MARKET).items()}
    except Exception:
        return {}


def _select_columns(df):
    present = []
    for name, match in _GROUPS:
        cols = [c for c in df.columns if match(c)]
        if cols:
            present.append((name, cols))
    groups = st.multiselect("Gösterilecek sütun grupları", [n for n, _ in present],
                            default=[n for n, _ in present if n in _DEFAULT_GROUPS], key="ai_col_groups")
    shown = []
    for name, cols in present:
        if name in groups:
            shown += [c for c in cols if c not in shown]
    known = {c for _, cols in present for c in cols}
    # Elle eklenen alanlar vb. gruba girmeyen sütunlar her zaman gösterilir.
    shown += [c for c in df.columns if c not in known and c not in shown]
    return shown


def _render_dataset(ticker, username):
    info = ad.get_dataset_info(ticker)
    df = ad.load_dataset(ticker)
    if info is None or df.empty:
        st.info("Kayıtlı veri yok.")
        return
    meta = info["meta"]
    val = meta.get("valuation") or {}
    sub = meta.get("sub_sector") or val.get("sub_sector") or _sub_sectors().get(ticker)
    st.markdown(f"#### {ticker} · {sub or 'alt sektör bilinmiyor'}"
                + (f" <span style='opacity:0.6'>({meta.get('sector') or val.get('sector')})</span>"
                   if (meta.get("sector") or val.get("sector")) else ""), unsafe_allow_html=True)
    m = st.columns(5)
    m[0].metric("Gün", f"{len(df)}")
    m[1].metric("Sütun", f"{len(df.columns) - 1}", help=f"Eğitim verisinde {len(ad.training_frame(df).columns)} sütun")
    last_score = df["valuation_score"].iloc[-1] if "valuation_score" in df else None
    m[2].metric("Ucuzluk Skoru", f"{last_score:.0f}" if pd.notna(last_score) else "—", help="Son günün skoru")
    m[3].metric("Sektör ETF'si", meta.get("sector_etf") or "—", help=val.get("sector"))
    m[4].metric("VWAP", "Alpaca" if meta.get("vwap_source") == "alpaca" else "Tipik fiyat")
    freshness_caption(f"{info['start']} → {info['end']} · son kayıt {_fmt_time(info['updated_at'])} · "
                      f"interpolasyonla doldurulan hücre: {int(df['interpolated_cells'].sum())}")
    for w in (st.session_state.get("ai_last_warnings") or {}).get(ticker) or meta.get("warnings") or []:
        st.warning(w)

    present_etfs, missing_etfs = ad.etf_coverage(df.columns)
    if missing_etfs:
        st.warning(f"Sektör ETF'leri veride eksik: **{', '.join(missing_etfs)}** "
                   f"({len(present_etfs)}/{len(present_etfs) + len(missing_etfs)} var). Günlük arşivi doldurup "
                   "veri setini yeniden hazırlayın.")
    else:
        st.caption(f"Sektör ETF'leri veride: {', '.join(present_etfs)} - her biri için kapanış, 1 / 5 / 21 "
                   "günlük getiri (`ret_*`).")

    missing_rel = ad.missing_relative_cols(df)
    if missing_rel:
        st.info(f"Bu veri seti göreli fiyat / hacim sütunları eklenmeden önce hazırlanmış "
                f"({', '.join(missing_rel)} yok). Eğitim verisinde bu sütunların olması için veri setini "
                "**Veriyi Hazırla ve Kaydet** ile yeniden hazırlayın.")

    train = ad.training_frame(df)
    mode = st.radio("Görünüm", ("Tüm veri", "Eğitim verisi"), horizontal=True, key=f"ai_view_mode_{ticker}",
                    help="Eğitim verisi: modele verilecek tablo - meta sütunlar ve fiyat / hacim seviyeleri "
                         "olmadan (yerlerine yüzde / getiri karşılıkları).")
    if mode == "Eğitim verisi":
        dropped = [c for c in df.columns if c not in train.columns and c not in ad.META_COLS]
        st.caption(f"Eğitim verisi: **{len(train)} gün × {len(train.columns)} sütun**. Eğitime alınmayanlar "
                   f"({len(dropped)}): " + ", ".join(f"`{c}`" for c in dropped)
                   + f" · meta ({len([c for c in df.columns if c in ad.META_COLS])}): "
                   + ", ".join(f"`{c}`" for c in df.columns if c in ad.META_COLS))
        cols = list(train.columns)
        view = train.sort_index(ascending=False).reset_index()
    else:
        cols = _select_columns(df)
        view = df[cols].sort_index(ascending=False).reset_index()
    view["date"] = view["date"].dt.strftime("%Y-%m-%d")
    num_cols = [c for c in view.columns if pd.api.types.is_float_dtype(view[c])]
    view[num_cols] = view[num_cols].round(4)
    st.dataframe(view, hide_index=True, use_container_width=True, height=520,
                 column_config={"date": st.column_config.TextColumn("Tarih")})

    b1, b2, b3 = st.columns(3)
    if b1.button("Yeni günleri ekle", key=f"ai_update_{ticker}",
                 help="Son kayıtlı günden sonraki işlem günlerini hesaplayıp ekler; var olan günler değişmez."):
        with st.spinner("Yeni günler hesaplanıyor..."):
            try:
                fetcher = _alpaca_vwap_fetcher(username) if meta.get("vwap_source") == "alpaca" else None
                added, _ = ad.update(ticker, vwap_fetcher=fetcher)
            except Exception as e:
                st.error(str(e))
            else:
                st.success(f"{added} yeni gün eklendi." if added else "Eklenecek yeni gün yok.")
                if added:
                    st.rerun()
    b2.download_button("Eğitim verisi (CSV)", ad.training_frame(df).to_csv(date_format="%Y-%m-%d").encode("utf-8"),
                       file_name=f"ai_dataset_{ticker}.csv", mime="text/csv", key=f"ai_csv_{ticker}",
                       help="Görünümdeki 'Eğitim verisi' tablosu: meta sütunlar ve fiyat / hacim seviyeleri olmadan.")
    confirm = b3.checkbox("Silmeyi onayla", key=f"ai_del_ok_{ticker}")
    if b3.button("Veri setini sil", disabled=not confirm, key=f"ai_del_{ticker}"):
        ad.delete_dataset(ticker)
        st.rerun()

    _render_valuation_history(ticker)

    _render_redundancy(train)

    with st.expander("Bir güne veri ekle"):
        st.caption("Seçilen güne yeni bir alan (ör. haber duyarlılığı) ekler veya günceller. Bu alanlar veri seti "
                   "yeniden hazırlansa da korunur; boş bırakılan değer alanı siler.")
        e1, e2, e3, e4 = st.columns([1.2, 1.5, 1, 0.8])
        day = e1.selectbox("Gün", list(df.index.strftime("%Y-%m-%d"))[::-1], key=f"ai_extra_day_{ticker}")
        field = e2.text_input("Alan adı", key=f"ai_extra_field_{ticker}", placeholder="örn. news_sentiment").strip()
        value = e3.text_input("Değer", key=f"ai_extra_value_{ticker}")
        e4.write("")
        if e4.button("Kaydet", key=f"ai_extra_save_{ticker}", disabled=not field):
            if field in set(meta.get("columns", [])) | {"source", "date"}:
                st.error(f"'{field}' hesaplanan bir sütun - farklı bir ad seçin.")
            else:
                parsed = None
                if value.strip():
                    try:
                        parsed = float(value.replace(",", "."))
                    except ValueError:
                        parsed = value.strip()
                ad.set_extra(ticker, day, {field: parsed})
                st.rerun()

    with st.expander("Sütun açıklamaları ve eğitimde dikkat edilecekler"):
        st.markdown(_GLOSSARY)
        st.caption("Değerler gün kapanışıyla hesaplanır - ertesi günü tahmin ederken hedef değişkeni bir gün ileri "
                   "kaydırın. Direnç seviyeleri yalnızca o güne kadar oluşmuş tepeleri kullanır (sızıntı yok). "
                   "İç boşluklarda doğrusal interpolasyon bir sonraki bilinen değeri kullanır; "
                   "`interpolated_cells` > 0 olan satırları gerekirse ayıklayın.")


def _render_valuation_history(ticker):
    history = ad.load_valuation_history(ticker)
    label = (f"Ucuzluk skoru günlük geçmişi - {len(history)} gün "
             f"({history.index.min():%d.%m.%Y} → {history.index.max():%d.%m.%Y})" if len(history)
             else "Ucuzluk skoru günlük geçmişi - henüz kayıt yok")
    with st.expander(label):
        st.caption(f"{ad.MARKET} değerleme servisi her çalıştığında hissenin skoru o günün satırı olarak "
                   "`valuation_scores_daily` tablosuna yazılır (aynı gün tekrar skorlanırsa son skor kalır). "
                   "Servisin yazmadığı geçmiş günler, veri seti hazırlanırken bilanço tablolarından yeniden "
                   "hesaplanır (Kaynak: Yeniden hesaplandı). Veri setindeki her gün o gün veya öncesindeki son "
                   "kaydı alır. Yeni günler **Yeni günleri ekle** ile eklenir.")
        if history.empty:
            return
        view = history.rename(columns={col: key for col, key in ad.VALUATION_FIELDS.items()})
        view = view.rename(columns={"Nihai Skor": "Ucuzluk Skoru"})
        view["Kaynak"] = np.where(view.pop("valuation_is_reconstructed") == 1, "Yeniden hesaplandı", "Servis")
        if len(view) > 1:
            st.line_chart(view["Ucuzluk Skoru"], height=180)
        out = view.sort_index(ascending=False).reset_index()
        out["date"] = out["date"].dt.strftime("%Y-%m-%d")
        st.dataframe(out.rename(columns={"date": "Tarih"}), hide_index=True, use_container_width=True)


def _render_redundancy(df):
    with st.expander("Birbirinin yerine geçebilecek sütunlar (eğitim verisinde)"):
        st.caption("Eğitim verisindeki sabit sütunlar, birebir aynı sütunlar ve mutlak korelasyonu eşiğin üstünde "
                   "olan çiftler. Fiyat / hacim seviyeleri eğitim verisinde olmadığı için burada görünmez.")
        threshold = st.slider("Korelasyon eşiği", 0.80, 0.99, 0.95, 0.01, key="ai_corr_threshold")
        rep = ad.redundancy_report(df, threshold)
        c1, c2, c3 = st.columns(3)
        c1.metric("Sabit sütun", len(rep["constant"]))
        c2.metric("Birebir aynı çift", len(rep["identical"]))
        c3.metric(f"|r| ≥ {threshold:.2f} çift", len(rep["pairs"]))
        if rep["constant"]:
            st.markdown("**Sabit (bilgi taşımıyor):** " + ", ".join(f"`{c}`" for c in rep["constant"]))
        if rep["identical"]:
            st.markdown("**Birebir aynı:** " + ", ".join(f"`{a}` = `{b}`" for a, b in rep["identical"]))
        if rep["pairs"]:
            st.dataframe(pd.DataFrame(rep["pairs"], columns=["Sütun 1", "Sütun 2", "|r|"]).round(3),
                         hide_index=True, use_container_width=True, height=360)


def _render_saved(username):
    st.subheader("2. Kayıtlı Veri Setleri")
    datasets = ad.list_datasets()
    if not datasets:
        st.info("Henüz kayıtlı veri seti yok - yukarıdan bir hisse seçip hazırlayın.")
        return
    subs = _sub_sectors()
    st.dataframe(pd.DataFrame([{
        "Hisse": d["ticker"],
        "Alt Sektör": d["meta"].get("sub_sector") or (d["meta"].get("valuation") or {}).get("sub_sector")
        or subs.get(d["ticker"]) or "—",
        "Gün": d["rows"], "Başlangıç": d["start"], "Bitiş": d["end"],
        "Sütun": len(d["meta"].get("columns", [])), "Son Güncelleme": _fmt_time(d["updated_at"]),
    } for d in datasets]), hide_index=True, use_container_width=True)
    tickers = [d["ticker"] for d in datasets]
    if st.session_state.get("ai_view_select") not in tickers:
        st.session_state.pop("ai_view_select", None)
    ticker = st.selectbox("Görüntülenecek veri seti", tickers, key="ai_view_select")
    _render_dataset(ticker, username)


def render_ai_dataset(username, nasdaq_tickers):
    _render_create(username, nasdaq_tickers)
    st.divider()
    _render_saved(username)
