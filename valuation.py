# valuation.py
import yfinance as yf
import pandas as pd
import numpy as np

def get_valuation_data(ticker):
    """
    yfinance üzerinden temel verileri çeker, 0-100 arası Değerleme Skoru hesaplar.
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info
        
        sector = info.get('sector', 'Diğer')
        if not sector or sector == 'None':
            sector = 'Diğer'

        # Temel Verileri Alma
        pe = info.get('trailingPE', None)
        pb = info.get('priceToBook', None)
        ev_ebitda = info.get('enterpriseToEbitda', None)
        peg = info.get('pegRatio', None)
        
        roe = info.get('returnOnEquity', None)
        if roe is not None:
            roe = round(roe * 100, 2)
            
        total_debt = info.get('totalDebt', 0) or 0
        total_cash = info.get('totalCash', 0) or 0
        ebitda = info.get('ebitda', None)
        
        net_debt_ebitda = None
        if ebitda and ebitda > 0:
            net_debt = total_debt - total_cash
            net_debt_ebitda = round(net_debt / ebitda, 2)

        fcf = info.get('freeCashflow', None)
        market_cap = info.get('marketCap', None)
        fcf_yield = None
        if fcf and market_cap and market_cap > 0:
            fcf_yield = round((fcf / market_cap) * 100, 2)

        # ----------------------------------------------------------------------
        # NİHAİ SKOR HESAPLAMA ALGORİTMASI (0 - 100 Puan)
        # ----------------------------------------------------------------------
        score = 0
        
        # 1. F/K Puanı (Max 20)
        if pe and 0 < pe <= 10: score += 20
        elif pe and 10 < pe <= 15: score += 15
        elif pe and 15 < pe <= 22: score += 8
        
        # 2. PD/DD Puanı (Max 15)
        if pb and 0 < pb <= 1.5: score += 15
        elif pb and 1.5 < pb <= 3.0: score += 10
        elif pb and 3.0 < pb <= 5.0: score += 5
        
        # 3. FD/FAVÖK Puanı (Max 20)
        if ev_ebitda and 0 < ev_ebitda <= 7: score += 20
        elif ev_ebitda and 7 < ev_ebitda <= 12: score += 12
        elif ev_ebitda and 12 < ev_ebitda <= 18: score += 6
        
        # 4. PEG Puanı (Max 15)
        if peg and 0 < peg <= 1.0: score += 15
        elif peg and 1.0 < peg <= 1.5: score += 10
        elif peg and 1.5 < peg <= 2.0: score += 5
        
        # 5. ROE (Özkaynak Karlılığı) Puanı (Max 15)
        if roe and roe >= 25: score += 15
        elif roe and 15 <= roe < 25: score += 10
        elif roe and 10 <= roe < 15: score += 5
        
        # 6. Borçluluk Puanı (Max 15)
        if net_debt_ebitda is not None:
            if net_debt_ebitda <= 1.5: score += 15
            elif 1.5 < net_debt_ebitda <= 3.0: score += 10
            elif 3.0 < net_debt_ebitda <= 5.0: score += 5

        return {
            "Hisse": ticker,
            "Sektör": sector,
            "Nihai Skor": score,
            "F/K": round(pe, 2) if pe else None,
            "PD/DD": round(pb, 2) if pb else None,
            "FD/FAVÖK": round(ev_ebitda, 2) if ev_ebitda else None,
            "PEG": round(peg, 2) if peg else None,
            "Özkaynak Karlılığı %": roe,
            "Net Borç / FAVÖK": net_debt_ebitda,
            "FCF Verimi %": fcf_yield
        }
    except Exception:
        return None


def style_valuation_df(df):
    """
    Pandas dataframe için özel renklendirme kuralları uygular.
    """
    def apply_styles(val_df):
        style_df = pd.DataFrame('', index=val_df.index, columns=val_df.columns)
        
        for idx in val_df.index:
            # 70+ Puan Yeşil Vurgu
            if val_df.loc[idx, 'Nihai Skor'] >= 70:
                style_df.loc[idx, 'Nihai Skor'] = 'background-color: #1b4332; color: #2ec4b6; font-weight: bold;'
            elif val_df.loc[idx, 'Nihai Skor'] < 40:
                style_df.loc[idx, 'Nihai Skor'] = 'color: #e63946; font-weight: bold;'

            # Pahalı/Riskli Değerleri Kırmızı Yazma Kuralları
            if pd.notna(val_df.loc[idx, 'F/K']) and val_df.loc[idx, 'F/K'] > 20:
                style_df.loc[idx, 'F/K'] = 'color: #e63946; font-weight: bold;'
            if pd.notna(val_df.loc[idx, 'PD/DD']) and val_df.loc[idx, 'PD/DD'] > 4.0:
                style_df.loc[idx, 'PD/DD'] = 'color: #e63946; font-weight: bold;'
            if pd.notna(val_df.loc[idx, 'FD/FAVÖK']) and val_df.loc[idx, 'FD/FAVÖK'] > 15.0:
                style_df.loc[idx, 'FD/FAVÖK'] = 'color: #e63946; font-weight: bold;'
            if pd.notna(val_df.loc[idx, 'PEG']) and val_df.loc[idx, 'PEG'] > 1.8:
                style_df.loc[idx, 'PEG'] = 'color: #e63946; font-weight: bold;'
            if pd.notna(val_df.loc[idx, 'Özkaynak Karlılığı %']) and val_df.loc[idx, 'Özkaynak Karlılığı %'] < 10.0:
                style_df.loc[idx, 'Özkaynak Karlılığı %'] = 'color: #e63946; font-weight: bold;'
            if pd.notna(val_df.loc[idx, 'Net Borç / FAVÖK']) and val_df.loc[idx, 'Net Borç / FAVÖK'] > 3.5:
                style_df.loc[idx, 'Net Borç / FAVÖK'] = 'color: #e63946; font-weight: bold;'
                
        return style_df

    return df.style.apply(apply_styles, axis=None)