# valuation.py
import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
from datetime import datetime, timezone

from theme import negative_color
from ui_style import zebra_style
from yf_data_quality import is_info_meaningful

# Veri çekimi ve saklama artık piyasa servislerinde (valuation_service.py) ve
# veritabanı tablosunda (valuation_db.py); bu modülde yalnızca tek hissenin ham
# verisinin çıkarılması, skor hesabı ve tablo stili kaldı.

SUB_SECTOR_FILE = "sub_sectors.json"

# Alt sektörde (iş modelinde) bu kadar veya daha az hisse varsa sektör medyanı/iskontosu
# anlamsız sayılır: hücrede "U" gösterilir, iskonto hesaba SMALL_SECTOR_DISCOUNT olarak girer.
from valuation_rules import SMALL_SECTOR_DISCOUNT, SMALL_SECTOR_MAX, score_row  # noqa: F401 (dışarıya da sunulur)
MARK_SMALL_SECTOR = "U"
MARK_NO_DATA = "Y"
# Değerleme analizine alınmayan Yahoo menkul türleri (quoteType). ETF'lerin bilanço/kârlılık
# verisi yok, F/K ve iskonto kıyası anlamsız - çekilir ama skorlanmaz, tabloda gösterilmez.
EXCLUDED_QUOTE_TYPES = {"ETF": "ETF"}
EXCLUDED_KEY = "_excluded"
SECTOR_COLUMNS = ("Alt Sektör Ort. F/K", "Alt Sektör İskontosu %")
# "Y" (veri yok) gösterilecek veri sütunları - eksik değer bu kriterden puan almaz.
DATA_COLUMNS = (
    "Alt Sektör İskontosu %", "Alt Sektör Ort. F/K", "F/K", "PEG", "EPS Büyümesi %",
    "Gelir Büyümesi %", "Öz Sermaye Getirisi (ROE) %", "Net Kar Marjı %", "Brüt Kar Marjı %",
    "Faiz Karşılama Oranı", "Varlık Getirisi (ROA) %", "Borç / Özsermaye", "Borç / Varlık %",
    "Cari Oran", "Likidite Oranı", "Varlık Devir Hızı", "Bilanço Tarihi", "Sonraki Bilanço",
)


class YahooRateLimited(Exception):
    """Yahoo Finance isteği "çok fazla istek" (HTTP 429) ile reddetti."""


def is_rate_limit_error(exc) -> bool:
    """yfinance'in sürümüne göre YFRateLimitError ya da içinde 429 geçen bir HTTP
    hatası gelir - ikisini de yakalamak için sınıf adına ve mesaja bakılır."""
    if "RateLimit" in type(exc).__name__:
        return True
    text = str(exc)
    return "429" in text or "Too Many Requests" in text or "Rate limited" in text


def load_sub_sectors():
    """sub_sectors.json dosyasından özel alt sektör haritasını yükler."""
    if os.path.exists(SUB_SECTOR_FILE):
        try:
            with open(SUB_SECTOR_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def fetch_single_ticker_raw(ticker, sub_sectors_map=None, raise_on_rate_limit=False):
    """yfinance üzerinden verileri çeker ve mikro iş modeli alt sektörünü atar.

    Veri yoksa/anlamsızsa None döner. `raise_on_rate_limit` True ise Yahoo'nun
    429 (çok fazla istek) yanıtı None yerine YahooRateLimited olarak fırlatılır;
    paketler halinde çeken servis bu durumda bekleyip tekrar dener.
    `sub_sectors_map` verilmezse sub_sectors.json her çağrıda yeniden okunur."""
    try:
        info = yf.Ticker(ticker).info
    except Exception as e:
        if raise_on_rate_limit and is_rate_limit_error(e):
            raise YahooRateLimited(str(e)) from e
        return None
    try:
        return _raw_from_info(ticker, info, sub_sectors_map)
    except Exception:
        return None


def _epoch_to_date(ts):
    """Yahoo'nun saniye cinsinden zaman damgasını 'YYYY-MM-DD' metnine çevirir; yoksa None."""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
    except Exception:
        return None


def is_excluded(raw) -> bool:
    """Ham kayıt değerleme dışı bir menkule (ETF) mi ait?"""
    return bool(raw and raw.get(EXCLUDED_KEY))


def _raw_from_info(ticker, info, sub_sectors_map=None):
    # ETF'ler değerleme dışı: skorlanmaz ama "ETF olduğu için hariç" diye işaretli
    # bir kayıt döner ki her seferinde yeniden çekilmesin ve kullanıcıya söylensin.
    quote_type = (info or {}).get('quoteType')
    if quote_type in EXCLUDED_QUOTE_TYPES:
        return {"Hisse": ticker, EXCLUDED_KEY: EXCLUDED_QUOTE_TYPES[quote_type]}

    # Delisted/durdurulmuş/geçersiz bir sembol için Yahoo neredeyse boş
    # bir .info sözlüğü döndürebilir - bunu, tüm oranları None olan
    # "sahte" bir satır olarak tabloya sokmak yerine baştan reddet.
    if not is_info_meaningful(info):
        return None

    main_sector = info.get('sector', 'Diğer')
    industry = info.get('industry', 'Diğer')

    # JSON dosyasından özel iş modeli alt sektörünü al
    if sub_sectors_map is None:
        sub_sectors_map = load_sub_sectors()
    alt_sek = sub_sectors_map.get(ticker, industry) # JSON'da yoksa yfinance industry kullan

    # 1. Çarpanlar & Büyümeler
    pe = info.get('trailingPE', None)
    pb = info.get('priceToBook', None)
    ev_ebitda = info.get('enterpriseToEbitda', None)

    peg = info.get('pegRatio') or info.get('trailingPegRatio')
    eps_growth = info.get('earningsGrowth', None)
    if eps_growth is not None: eps_growth = round(eps_growth * 100, 2)
    
    rev_growth = info.get('revenueGrowth', None)
    if rev_growth is not None: rev_growth = round(rev_growth * 100, 2)

    # 2. Karlılıklar
    roe = info.get('returnOnEquity', None)
    if roe is not None: roe = round(roe * 100, 2)

    net_margin = info.get('profitMargins', None)
    if net_margin is not None: net_margin = round(net_margin * 100, 2)

    gross_margin = info.get('grossMargins', None)
    if gross_margin is not None: gross_margin = round(gross_margin * 100, 2)

    roa = info.get('returnOnAssets', None)
    if roa is not None: roa = round(roa * 100, 2)

    # 3. Borçluluk ve Sağlık
    debt_to_equity = info.get('debtToEquity', None)
    if debt_to_equity is not None: debt_to_equity = round(debt_to_equity / 100, 2)

    total_debt = info.get('totalDebt', None)
    total_assets = info.get('totalAssets', None)
    debt_to_assets = None
    if total_debt and total_assets and total_assets > 0:
        debt_to_assets = round((total_debt / total_assets) * 100, 2)

    ebitda = info.get('ebitda', None)
    interest_exp = info.get('interestExpense', None)
    interest_coverage = None
    if ebitda and interest_exp and interest_exp > 0:
        interest_coverage = round(ebitda / interest_exp, 2)
    else:
        interest_coverage = info.get('interestCoverage', None)
        if interest_coverage: interest_coverage = round(interest_coverage, 2)

    # 4. Likidite & Operasyonel
    current_ratio = info.get('currentRatio', None)
    if current_ratio: current_ratio = round(current_ratio, 2)

    quick_ratio = info.get('quickRatio', None)
    if quick_ratio: quick_ratio = round(quick_ratio, 2)

    total_revenue = info.get('totalRevenue', None)
    asset_turnover = None
    if total_revenue and total_assets and total_assets > 0:
        asset_turnover = round(total_revenue / total_assets, 2)

    # Bilanço tarihleri (Yahoo'da varsa): son açıklanan bilançonun dönem sonu
    # (mostRecentQuarter) ve bir sonraki bilanço açıklama tarihi (earningsTimestampStart,
    # yoksa earningsTimestamp). Tabloda "Bilanço Tarihi" / "Sonraki Bilanço" olarak görünür.
    most_recent_quarter = _epoch_to_date(info.get('mostRecentQuarter'))
    next_earnings = _epoch_to_date(info.get('earningsTimestampStart') or info.get('earningsTimestamp'))

    return {
        "Hisse": ticker,
        "Alt Sektör (İş Modeli)": alt_sek,
        "Ana Sektör": main_sector,
        "F/K": round(pe, 2) if pe and pe > 0 else None,
        "PD/DD": round(pb, 2) if pb and pb > 0 else None,
        "FD/FAVÖK": round(ev_ebitda, 2) if ev_ebitda and ev_ebitda > 0 else None,
        # PEG yalnızca F/K > 0 olan hisselerde anlamlı (negatif/eksik F/K'da PEG yanıltıcı).
        "PEG": round(peg, 2) if peg and peg > 0 and pe and pe > 0 else None,
        "EPS Büyümesi %": eps_growth,
        "Gelir Büyümesi %": rev_growth,
        "Öz Sermaye Getirisi (ROE) %": roe,
        "Net Kar Marjı %": net_margin,
        "Brüt Kar Marjı %": gross_margin,
        "Faiz Karşılama Oranı": interest_coverage,
        "Varlık Getirisi (ROA) %": roa,
        "Borç / Özsermaye": debt_to_equity,
        "Borç / Varlık %": debt_to_assets,
        "Cari Oran": current_ratio,
        "Likidite Oranı": quick_ratio,
        "Varlık Devir Hızı": asset_turnover,
        "_most_recent_quarter": most_recent_quarter,
        "_next_earnings": next_earnings,
    }


def calculate_sector_relative_scores(raw_data_list):
    """
    İskontoyu GENEL SEKTÖR yerine MİKRO ALT SEKTÖR (İş Modeli) medyanına göre hesaplar.
    """
    if not raw_data_list:
        return []

    # ETF'ler skorlanmaz ve akranların medyanına katılmaz.
    raw_data_list = [r for r in raw_data_list if not is_excluded(r)]
    if not raw_data_list:
        return []
    df = pd.DataFrame(raw_data_list)
    # PEG yalnızca F/K > 0 olanlarda (bu kural eklenmeden önce kaydedilmiş satırlar için de).
    df['F/K'] = pd.to_numeric(df['F/K'], errors='coerce')
    df['PEG'] = pd.to_numeric(df['PEG'], errors='coerce').where(df['F/K'] > 0)
    # Bilanço tarihleri bu alanlar eklenmeden önce kaydedilmiş satırlarda yok.
    for col in ("_most_recent_quarter", "_next_earnings"):
        if col not in df.columns:
            df[col] = None
    df['Bilanço Tarihi'] = df['_most_recent_quarter']
    df['Sonraki Bilanço'] = df['_next_earnings']

    # 1. Alt Sektöre (İş Modeline) Göre F/K Medyanını Hesapla (eksik F/K'lar medyana katılmaz)
    group = df.groupby('Alt Sektör (İş Modeli)')
    sub_sector_medians = group['F/K'].transform('median')
    df['Alt Sektör Ort. F/K'] = sub_sector_medians
    # Alt sektörde SMALL_SECTOR_MAX veya daha az hisse varsa kıyas anlamsız ("U").
    df['_az_hisseli'] = group['Hisse'].transform('count') <= SMALL_SECTOR_MAX

    # 2. İş Modeli Grubu İskontosu % Hesapla
    # - Hissenin F/K'sı ya da sektör medyanı yoksa: veri yok ("Y"), puan almaz.
    # - Alt sektör az hisseliyse ("U"): diğer hesaplar bozulmasın diye 1 kabul edilir.
    has_data = df['Alt Sektör Ort. F/K'].notna() & df['F/K'].notna() & (df['Alt Sektör Ort. F/K'] > 0)
    discount = ((df['Alt Sektör Ort. F/K'] - df['F/K']) / df['Alt Sektör Ort. F/K']) * 100
    df['Alt Sektör İskontosu %'] = np.where(
        has_data & df['_az_hisseli'], SMALL_SECTOR_DISCOUNT,
        np.where(has_data, discount.round(1), np.nan),
    )

    # 3. 100 Puanlık Skorlama Algoritması (score_row)
    scores = [score_row(row) for _, row in df.iterrows()]

    df['Nihai Skor'] = scores

    # AĞIRLIĞA GÖRE SIRALANMIŞ EN YÜKSEKTEN EN DÜŞÜĞE SÜTUN DİZİLİMİ
    output_cols = [
        "Hisse", "Alt Sektör (İş Modeli)", "Ana Sektör", "Nihai Skor",
        "Alt Sektör İskontosu %",       # 15 Puan
        "Alt Sektör Ort. F/K",
        "F/K",
        "PEG",                         # Bilgi (puan yok - 2026-10-08)
        "EPS Büyümesi %",              # 10 Puan
        "Gelir Büyümesi %",            # 10 Puan
        "Öz Sermaye Getirisi (ROE) %",  # 10 Puan
        "Net Kar Marjı %",             # 8 Puan
        "Brüt Kar Marjı %",            # 7 Puan
        "Faiz Karşılama Oranı",        # 7 Puan
        "Varlık Getirisi (ROA) %",     # 6 Puan
        "Borç / Özsermaye",            # 5 Puan
        "Borç / Varlık %",             # 4 Puan
        "Cari Oran",                   # 3 Puan
        "Likidite Oranı",              # 3 Puan
        "Varlık Devir Hızı",           # 2 Puan
        "Bilanço Tarihi",              # Bilgi (puan yok)
        "Sonraki Bilanço",             # Bilgi (puan yok)
        "_az_hisseli",                 # Alt sektör <= SMALL_SECTOR_MAX hisse -> "U"
    ]

    out = df[output_cols].astype(object).where(df[output_cols].notna(), None)
    return out.to_dict('records')


def prepare_display_df(df):
    """Tablo için hazırlık: az hisseli alt sektörlerin sektör hücrelerini "U" olarak
    gösterilecek şekilde işaretler (değer sonsuz yapılır, metne style_valuation_df
    çevirir) ve yardımcı sütunu kaldırır. Değerler sayısal kalır; renklendirme ve
    sıralama bozulmaz."""
    df = df.copy()
    if "_az_hisseli" in df.columns:
        small = df["_az_hisseli"].fillna(False).astype(bool)
        if "Alt Sektör Ort. F/K" in df.columns:
            df["Alt Sektör Ort. F/K"] = df["Alt Sektör Ort. F/K"].astype(float)
            df.loc[small, "Alt Sektör Ort. F/K"] = np.inf
        if "Alt Sektör İskontosu %" in df.columns:
            disc = df["Alt Sektör İskontosu %"].astype(float)
            # F/K'sı olmayan hissede "Y" kalır (iskonto zaten hesaplanamadı).
            df["Alt Sektör İskontosu %"] = disc.where(~(small & disc.notna()), np.inf)
        df = df.drop(columns=["_az_hisseli"])
    return df


def _format_cell(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return MARK_NO_DATA
    if isinstance(value, float) and np.isinf(value):
        return MARK_SMALL_SECTOR
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def style_valuation_df(df):
    """Pandas dataframe için renklendirme kuralları. Eksik veri "Y", az hisseli
    alt sektör "U" olarak gösterilir (bkz. prepare_display_df)."""
    def apply_styles(val_df):
        style_df = pd.DataFrame('', index=val_df.index, columns=val_df.columns)
        neg = f'color: {negative_color()};'
        neg_bold = f'color: {negative_color()}; font-weight: bold;'

        for idx in val_df.index:
            if val_df.loc[idx, 'Nihai Skor'] >= 70:
                style_df.loc[idx, 'Nihai Skor'] = 'background-color: #1b4332; color: #2ec4b6; font-weight: bold;'
            elif val_df.loc[idx, 'Nihai Skor'] < 40:
                style_df.loc[idx, 'Nihai Skor'] = neg_bold

            if pd.notna(val_df.loc[idx, 'Alt Sektör İskontosu %']) and val_df.loc[idx, 'Alt Sektör İskontosu %'] < 0:
                style_df.loc[idx, 'Alt Sektör İskontosu %'] = neg
            if pd.notna(val_df.loc[idx, 'PEG']) and val_df.loc[idx, 'PEG'] > 1.5:
                style_df.loc[idx, 'PEG'] = neg
            if pd.notna(val_df.loc[idx, 'EPS Büyümesi %']) and val_df.loc[idx, 'EPS Büyümesi %'] < 0:
                style_df.loc[idx, 'EPS Büyümesi %'] = neg
            if pd.notna(val_df.loc[idx, 'Gelir Büyümesi %']) and val_df.loc[idx, 'Gelir Büyümesi %'] < 0:
                style_df.loc[idx, 'Gelir Büyümesi %'] = neg
            if pd.notna(val_df.loc[idx, 'Öz Sermaye Getirisi (ROE) %']) and val_df.loc[idx, 'Öz Sermaye Getirisi (ROE) %'] < 10.0:
                style_df.loc[idx, 'Öz Sermaye Getirisi (ROE) %'] = neg
            if pd.notna(val_df.loc[idx, 'Net Kar Marjı %']) and val_df.loc[idx, 'Net Kar Marjı %'] < 8.0:
                style_df.loc[idx, 'Net Kar Marjı %'] = neg
            if pd.notna(val_df.loc[idx, 'Faiz Karşılama Oranı']) and val_df.loc[idx, 'Faiz Karşılama Oranı'] < 1.5:
                style_df.loc[idx, 'Faiz Karşılama Oranı'] = neg
            if pd.notna(val_df.loc[idx, 'Borç / Özsermaye']) and (val_df.loc[idx, 'Borç / Özsermaye'] > 1.5 or val_df.loc[idx, 'Borç / Özsermaye'] < 0):
                style_df.loc[idx, 'Borç / Özsermaye'] = neg
            if pd.notna(val_df.loc[idx, 'Borç / Varlık %']) and val_df.loc[idx, 'Borç / Varlık %'] > 60.0:
                style_df.loc[idx, 'Borç / Varlık %'] = neg
            if pd.notna(val_df.loc[idx, 'Cari Oran']) and val_df.loc[idx, 'Cari Oran'] < 1.0:
                style_df.loc[idx, 'Cari Oran'] = neg

        return style_df

    styler = zebra_style(df, extra_style_fn=apply_styles)
    cols = [c for c in DATA_COLUMNS if c in df.columns]
    return styler.format(_format_cell, subset=cols)