# config.py
import json
import os

import streamlit as st
import yfinance as yf

from github_config import read_json_from_github, write_json_to_github

GITHUB_REPO = "berkakar/yatirim"

# ------------------------------------------------------------------------------
# VARSAYILAN LİSTELER (DEFAULT)
# ------------------------------------------------------------------------------
DEFAULT_NASDAQ_100 = [
    "AAPL", "ABNB", "ADBE", "ADI", "ADP", "ADSK", "AEP", "AMAT", "AMD", "AMGN",
    "AMZN", "ANSS", "APP", "ARM", "ASML", "AVGO", "AXON", "BKR", "BIIB", "BKNG",
    "CDNS", "CEG", "CHTR", "CMCSA", "COST", "CPRT", "CRWD", "CSGP", "CSX", "CTAS",
    "CTSH", "DASH", "DDOG", "DLTR", "DXCM", "EA", "EXC", "FAST", "FTNT", "GEHC",
    "GILD", "GOOG", "GOOGL", "HON", "IDXX", "ILMN", "INTC", "INTU", "ISRG", "KDP",
    "KHC", "KLAC", "LRCX", "LULU", "MAR", "MCHP", "MDB", "MDLZ", "MELI", "META",
    "MNST", "MRVL", "MSFT", "MU", "NFLX", "NVDA", "NXPI", "ODFL", "ON", "ORLY",
    "PANW", "PAYX", "PCAR", "PDD", "PEP", "PLTR", "PYPL", "QCOM", "REGN", "ROP",
    "ROST", "SBUX", "SMCI", "SNPS", "TEAM", "TMUS", "TSLA", "TTD", "TXN", "VRSK",
    "VRTX", "WBD", "WDAY", "XEL", "ZS","SKHY"
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

def _custom_file(username):
    return f"custom_tickers_{username}.json"


def _defaults():
    return {
        "NASDAQ 100": list(dict.fromkeys(DEFAULT_NASDAQ_100)),
        "NYSE": list(dict.fromkeys(DEFAULT_NYSE)),
        "BIST 100": list(dict.fromkeys(DEFAULT_BIST_100))
    }


def load_ticker_lists(username):
    """Kullanıcıya özel listeleri yükler. Önce GitHub'daki (kalıcı) kopyayı, yoksa yerel
    dosyayı, o da yoksa varsayılanları döner.

    Streamlit Cloud her yeniden başlatmada repoyu sıfırdan klonladığı için sadece
    diske yazmak kalıcı olmuyor - bu yüzden asıl kaynak GitHub'daki dosya."""
    custom_file = _custom_file(username)
    data = None
    token = st.secrets.get("GITHUB_TOKEN")
    if token:
        try:
            data = read_json_from_github(GITHUB_REPO, token, custom_file, {})
        except Exception:
            data = None

    if not data and os.path.exists(custom_file):
        try:
            with open(custom_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = None

    if not data:
        return _defaults()

    return {
        "NASDAQ 100": list(dict.fromkeys(data.get("NASDAQ 100", DEFAULT_NASDAQ_100))),
        "NYSE": list(dict.fromkeys(data.get("NYSE", DEFAULT_NYSE))),
        "BIST 100": list(dict.fromkeys(data.get("BIST 100", DEFAULT_BIST_100)))
    }


def save_ticker_lists(ticker_dict, username):
    """Kullanıcıya özel listeleri kalıcı olması için GitHub'a commit'ler (mümkün olduğunda),
    ayrıca yerel dosyaya da yazar."""
    custom_file = _custom_file(username)
    token = st.secrets.get("GITHUB_TOKEN")
    if token:
        try:
            write_json_to_github(GITHUB_REPO, token, custom_file, ticker_dict, f"Update custom ticker lists ({username})")
        except Exception as e:
            st.warning(f"⚠️ Liste GitHub'a kalıcı olarak kaydedilemedi (sadece bu oturumda geçerli olacak): {e}")

    with open(custom_file, 'w', encoding='utf-8') as f:
        json.dump(ticker_dict, f, ensure_ascii=False, indent=4)


def search_tickers(query, max_results=8):
    """Yahoo Finance'in kendi arama API'si (yf.Search) üzerinden şirket adı/sembole
    göre hisse arar - hem ABD hem BIST hisselerini kapsar. Sonuçları
    [{'symbol','name','exchange'}, ...] olarak döner, hata/sonuç yoksa boş liste."""
    if not query or not query.strip():
        return []
    try:
        matches = yf.Search(query.strip(), max_results=max_results).quotes or []
    except Exception:
        return []

    results = []
    for q in matches:
        quote_type = q.get('quoteType', '')
        if quote_type and quote_type != 'EQUITY':
            continue
        symbol = q.get('symbol')
        if not symbol:
            continue
        name = q.get('shortname') or q.get('longname') or symbol
        results.append({
            "symbol": symbol,
            "name": name,
            "exchange": q.get('exchange', ''),
        })
    return results