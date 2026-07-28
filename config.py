# config.py
import json
import os

# ------------------------------------------------------------------------------
# VARSAYILAN LİSTELER (DEFAULT)
# ------------------------------------------------------------------------------
DEFAULT_NASDAQ_100 = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "GOOG", "META", "TSLA", "AVGO", "PEP", 
    "COST", "ADBE", "AMD", "CSCO", "INTC", "CMCSA", "AMGN", "NFLX", "INTU", "TXN", 
    "QCOM", "AMAT", "BKNG", "HON", "VRTX", "SBUX", "ISRG", "MDLZ", "ADP", "REGN", 
    "GILD", "ADI", "PANW", "MU", "SNPS", "PYPL", "KLAC", "CDNS", "CSX", "ORLY", 
    "MAR", "MELI", "CTAS", "LRCX", "MNST", "PAYX", "MCHP", "PCAR", "KDP", "ADSK",
    "NXPI", "IDXX", "PDD", "FTNT", "KHC", "ROP", "ROST", "ASML", "AEP", "AZN", 
    "BIIB", "BKR", "NOW", "CHTR", "CPRT", "CRWD", "CSGP", "CTSH", "DDOG", "DLTR", 
    "DXCM", "EA", "ENPH", "EXC", "EXPE", "FAST", "GEHC", "ILMN", "JD", 
    "LULU", "MDB", "MRNA", "MRVL", "ODFL", "ON", "PAYC", "SGEN", "SIRI", "SPLK", 
    "TROW", "TTWO", "VEEV", "VRSK", "WBA", "WBD", "SPCX", "ZS", "TEM", "LCID", 
    "CRWV", "EOSE", "OSS"
]

DEFAULT_NYSE = [
    "ONTO", "V", "WMT", "JNJ", "PG", "MA", "HD", "BAC", "KO", "DIS", 
    "XOM", "CVX", "PFE", "ABBV", "MRK", "UPS", "VZ", "T", "ORCL", 
    "MCD", "ANET", "DVA", "CAT", "GS", "MS", "GE", "BA", "MMM", "CVS", 
    "XLV", "MAGS", "XLF", "NOW", "TE", "BE", "PLTR", "SOFI", "LLY"
]

DEFAULT_BIST_100 = [
    "THYAO.IS", "GARAN.IS", "EREGL.IS", "ASELS.IS", "KCHOL.IS", "AKBNK.IS", "SISE.IS", 
    "ISCTR.IS", "TUPRS.IS", "BIMAS.IS", "PETKM.IS", "YKBNK.IS", "PGSUS.IS", "SAHOL.IS", 
    "FROTO.IS", "KOZAL.IS", "HEKTS.IS", "SASA.IS", "DOHOL.IS", "VESTL.IS", "TCELL.IS", 
    "TOASO.IS", "EKGYO.IS", "ARDYZ.IS", "ALARK.IS", "KRDMD.IS", "GUBRF.IS", "MAVI.IS",
    "AEFES.IS", "AGHOL.IS", "AHGAZ.IS", "AKCNS.IS", "AKSEN.IS", "ALBRK.IS", "ANHYT.IS", 
    "ARCLK.IS", "ASTOR.IS", "BIZIM.IS", "BRSAN.IS", "BSOKE.IS", "CANTE.IS", "CCOLA.IS", 
    "CIMSA.IS", "ECILC.IS", "ENJSA.IS", "ENKAI.IS", "HALKB.IS", "ISGYO.IS", "ISMEN.IS", 
    "KONTR.IS", "KORDS.IS", "MGROS.IS", "ODAS.IS", "OYAKC.IS", "SKBNK.IS", "SOKM.IS", 
    "TSKB.IS", "ULKER.IS", "VAKBN.IS", "YYLGD.IS", "ZOREN.IS", "AKFGY.IS", "AKGRT.IS", 
    "AKSGY.IS", "BAGFS.IS", "BANVT.IS", "BOSSA.IS", "BRISA.IS", "BVSAN.IS", 
    "CEMTS.IS", "CLEBI.IS", "CRDFA.IS", "DAGI.IS", "DESA.IS", "DEVA.IS", "DOAS.IS", 
    "DYOBY.IS", "EGEEN.IS", "EGSER.IS", "EMKEL.IS", "ESCOM.IS", "EUPWR.IS", "FMIZP.IS", 
    "GOLTS.IS", "GRNYO.IS", "GSDHO.IS", "GSDDE.IS", "GUSGR.IS", "IEYHO.IS", "IHLAS.IS", 
    "IHLGM.IS", "INDES.IS", "INFO.IS", "ITTYH.IS", "IZMDC.IS", "JANTS.IS", "KAREL.IS", 
    "KERVT.IS", "KONYA.IS", "KRONT.IS"
]

CUSTOM_FILE = "custom_tickers.json"

def load_ticker_lists():
    """Özel listeleri JSON dosyasından yükler. Dosya yoksa varsayılanları döner."""
    if os.path.exists(CUSTOM_FILE):
        try:
            with open(CUSTOM_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {
                    "NASDAQ 100": list(dict.fromkeys(data.get("NASDAQ 100", DEFAULT_NASDAQ_100))),
                    "NYSE": list(dict.fromkeys(data.get("NYSE", DEFAULT_NYSE))),
                    "BIST 100": list(dict.fromkeys(data.get("BIST 100", DEFAULT_BIST_100)))
                }
        except Exception:
            pass
            
    return {
        "NASDAQ 100": list(dict.fromkeys(DEFAULT_NASDAQ_100)),
        "NYSE": list(dict.fromkeys(DEFAULT_NYSE)),
        "BIST 100": list(dict.fromkeys(DEFAULT_BIST_100))
    }

def save_ticker_lists(ticker_dict):
    """Güncellenmiş listeleri JSON dosyasına kaydeder."""
    with open(CUSTOM_FILE, 'w', encoding='utf-8') as f:
        json.dump(ticker_dict, f, ensure_ascii=False, indent=4)