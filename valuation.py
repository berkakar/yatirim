# valuation.py
import yfinance as yf
import pandas as pd
import numpy as np
import json
import os

SUB_SECTOR_FILE = "sub_sectors.json"

def load_sub_sectors():
    """sub_sectors.json dosyasından özel alt sektör haritasını yükler."""
    if os.path.exists(SUB_SECTOR_FILE):
        try:
            with open(SUB_SECTOR_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def fetch_single_ticker_raw(ticker):
    """yfinance üzerinden verileri çeker ve mikro iş modeli alt sektörünü atar."""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        
        main_sector = info.get('sector', 'Diğer')
        industry = info.get('industry', 'Diğer')

        # JSON dosyasından özel iş modeli alt sektörünü al
        sub_sectors_map = load_sub_sectors()
        alt_sek = sub_sectors_map.get(ticker, industry) # JSON'da yoksa yfinance industry kullan

        # 1. Çarpanlar & Büyümeler
        pe = info.get('trailingPE', None)
        pb = info.get('priceToBook', None)
        ev_ebitda = info.get('enterpriseToEbitda', None)

        peg = info.get('pegRatio', None)
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

        return {
            "Hisse": ticker,
            "Alt Sektör (İş Modeli)": alt_sek,
            "Ana Sektör": main_sector,
            "F/K": round(pe, 2) if pe and pe > 0 else None,
            "PD/DD": round(pb, 2) if pb and pb > 0 else None,
            "FD/FAVÖK": round(ev_ebitda, 2) if ev_ebitda and ev_ebitda > 0 else None,
            "PEG": round(peg, 2) if peg and peg > 0 else None,
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
            "Varlık Devir Hızı": asset_turnover
        }
    except Exception:
        return None


def calculate_sector_relative_scores(raw_data_list):
    """
    İskontoyu GENEL SEKTÖR yerine MİKRO ALT SEKTÖR (İş Modeli) medyanına göre hesaplar.
    """
    if not raw_data_list:
        return []

    df = pd.DataFrame(raw_data_list)

    # 1. Alt Sektöre (İş Modeline) Göre F/K Medyanını Hesapla
    sub_sector_medians = df.groupby('Alt Sektör (İş Modeli)')['F/K'].transform('median')
    df['Alt Sektör Ort. F/K'] = sub_sector_medians

    # 2. İş Modeli Grubu İskontosu % Hesapla
    df['Alt Sektör İskontosu %'] = np.where(
        df['Alt Sektör Ort. F/K'].notna() & df['F/K'].notna() & (df['Alt Sektör Ort. F/K'] > 0),
        ((df['Alt Sektör Ort. F/K'] - df['F/K']) / df['Alt Sektör Ort. F/K']) * 100,
        0
    )
    df['Alt Sektör İskontosu %'] = df['Alt Sektör İskontosu %'].round(1)

    # 3. 100 Puanlık Skorlama Algoritması
    scores = []
    for idx, row in df.iterrows():
        score = 0
        
        # 1. Alt Sektör İskontosu (Ağırlık: 15 Puan)
        disc = row['Alt Sektör İskontosu %']
        if disc >= 30: score += 15
        elif 15 <= disc < 30: score += 10
        elif 0 <= disc < 15: score += 5
        
        # 2. PEG Oranı (10 Puan)
        peg = row['PEG']
        if pd.notna(peg):
            if peg <= 1.0: score += 10
            elif 1.0 < peg <= 1.5: score += 5

        # 3. EPS Büyümesi % (10 Puan)
        eps_g = row['EPS Büyümesi %']
        if pd.notna(eps_g):
            if eps_g >= 10.0: score += 10
            elif 5.0 <= eps_g < 10.0: score += 5

        # 4. Gelir Büyümesi % (10 Puan)
        rev_g = row['Gelir Büyümesi %']
        if pd.notna(rev_g):
            if rev_g >= 10.0: score += 10
            elif 5.0 <= rev_g < 10.0: score += 5

        # 5. Öz Sermaye Getirisi (ROE) % (10 Puan)
        roe = row['Öz Sermaye Getirisi (ROE) %']
        if pd.notna(roe):
            if roe >= 10.0: score += 10
            elif 5.0 <= roe < 10.0: score += 5

        # 6. Net Kar Marjı % (8 Puan)
        nm = row['Net Kar Marjı %']
        if pd.notna(nm):
            if nm >= 15.0: score += 8
            elif 8.0 <= nm < 15.0: score += 4

        # 7. Brüt Kar Marjı % (7 Puan)
        gm = row['Brüt Kar Marjı %']
        if pd.notna(gm):
            if 30.0 <= gm <= 60.0: score += 7
            elif gm > 60.0: score += 5

        # 8. Faiz Karşılama Oranı (7 Puan)
        ic = row['Faiz Karşılama Oranı']
        if pd.notna(ic):
            if ic >= 3.0: score += 7
            elif 1.5 <= ic < 3.0: score += 3

        # 9. Varlık Getirisi (ROA) % (6 Puan)
        roa = row['Varlık Getirisi (ROA) %']
        if pd.notna(roa):
            if 5.0 <= roa <= 10.0 or roa > 10.0: score += 6
            elif 2.0 <= roa < 5.0: score += 3

        # 10. Borç / Özsermaye (5 Puan)
        de = row['Borç / Özsermaye']
        if pd.notna(de):
            if de <= 0.5: score += 5
            elif 0.5 < de <= 1.0: score += 3

        # 11. Borç / Varlık % (4 Puan)
        da = row['Borç / Varlık %']
        if pd.notna(da):
            if da <= 50.0: score += 4
            elif 50.0 < da <= 70.0: score += 2

        # 12. Cari Oran (3 Puan)
        cr = row['Cari Oran']
        if pd.notna(cr):
            if 1.0 <= cr <= 2.0: score += 3
            elif cr > 2.0: score += 2

        # 13. Likidite Oranı (3 Puan)
        qr = row['Likidite Oranı']
        if pd.notna(qr):
            if qr >= 1.0: score += 3

        # 14. Varlık Devir Hızı (2 Puan)
        at = row['Varlık Devir Hızı']
        if pd.notna(at):
            if 1.0 <= at <= 2.0: score += 2
            elif at > 2.0: score += 1

        scores.append(score)

    df['Nihai Skor'] = scores

    # AĞIRLIĞA GÖRE SIRALANMIŞ EN YÜKSEKTEN EN DÜŞÜĞE SÜTUN DİZİLİMİ
    output_cols = [
        "Hisse", "Alt Sektör (İş Modeli)", "Ana Sektör", "Nihai Skor",
        "Alt Sektör İskontosu %",       # 15 Puan
        "Alt Sektör Ort. F/K",
        "F/K",
        "PEG",                         # 10 Puan
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
        "Varlık Devir Hızı"            # 2 Puan
    ]
    
    return df[output_cols].to_dict('records')


def style_valuation_df(df):
    """Pandas dataframe için renklendirme kuralları."""
    def apply_styles(val_df):
        style_df = pd.DataFrame('', index=val_df.index, columns=val_df.columns)
        
        for idx in val_df.index:
            if val_df.loc[idx, 'Nihai Skor'] >= 70:
                style_df.loc[idx, 'Nihai Skor'] = 'background-color: #1b4332; color: #2ec4b6; font-weight: bold;'
            elif val_df.loc[idx, 'Nihai Skor'] < 40:
                style_df.loc[idx, 'Nihai Skor'] = 'color: #e63946; font-weight: bold;'

            if pd.notna(val_df.loc[idx, 'Alt Sektör İskontosu %']) and val_df.loc[idx, 'Alt Sektör İskontosu %'] < 0:
                style_df.loc[idx, 'Alt Sektör İskontosu %'] = 'color: #e63946;'
            if pd.notna(val_df.loc[idx, 'PEG']) and val_df.loc[idx, 'PEG'] > 1.5:
                style_df.loc[idx, 'PEG'] = 'color: #e63946;'
            if pd.notna(val_df.loc[idx, 'EPS Büyümesi %']) and val_df.loc[idx, 'EPS Büyümesi %'] < 0:
                style_df.loc[idx, 'EPS Büyümesi %'] = 'color: #e63946;'
            if pd.notna(val_df.loc[idx, 'Gelir Büyümesi %']) and val_df.loc[idx, 'Gelir Büyümesi %'] < 0:
                style_df.loc[idx, 'Gelir Büyümesi %'] = 'color: #e63946;'
            if pd.notna(val_df.loc[idx, 'Öz Sermaye Getirisi (ROE) %']) and val_df.loc[idx, 'Öz Sermaye Getirisi (ROE) %'] < 10.0:
                style_df.loc[idx, 'Öz Sermaye Getirisi (ROE) %'] = 'color: #e63946;'
            if pd.notna(val_df.loc[idx, 'Net Kar Marjı %']) and val_df.loc[idx, 'Net Kar Marjı %'] < 8.0:
                style_df.loc[idx, 'Net Kar Marjı %'] = 'color: #e63946;'
            if pd.notna(val_df.loc[idx, 'Faiz Karşılama Oranı']) and val_df.loc[idx, 'Faiz Karşılama Oranı'] < 1.5:
                style_df.loc[idx, 'Faiz Karşılama Oranı'] = 'color: #e63946;'
            if pd.notna(val_df.loc[idx, 'Borç / Özsermaye']) and val_df.loc[idx, 'Borç / Özsermaye'] > 1.5:
                style_df.loc[idx, 'Borç / Özsermaye'] = 'color: #e63946;'
            if pd.notna(val_df.loc[idx, 'Borç / Varlık %']) and val_df.loc[idx, 'Borç / Varlık %'] > 60.0:
                style_df.loc[idx, 'Borç / Varlık %'] = 'color: #e63946;'
            if pd.notna(val_df.loc[idx, 'Cari Oran']) and val_df.loc[idx, 'Cari Oran'] < 1.0:
                style_df.loc[idx, 'Cari Oran'] = 'color: #e63946;'

        return style_df

    return df.style.apply(apply_styles, axis=None)