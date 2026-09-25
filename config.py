# config.py
import json
import os

import streamlit as st
import yfinance as yf

from github_config import read_json_from_github, write_json_to_github

GITHUB_REPO = "berkakar/yatirim"


def _get_github_token():
    """st.secrets.get(...) tek başına GÜVENLİ değil - hiç secrets.toml
    dosyası yoksa (GitHub Actions runner'larının hepsinde durum bu)
    Streamlit doğrudan StreamlitSecretNotFoundError fırlatıyor, .get()
    kullanmak bunu ÖNLEMİYOR. Bu fonksiyonu kullanmayan çağrılar (bkz. bu
    dosyanın git geçmişi) otomatik alım/satım modüllerinin (ORB, Relative
    Strength Rotasyonu, Otomatik Alım/Satım) evren oluştururken - yani
    GitHub Actions'ta, HER ÇALIŞTIRMADA - sessizce çökmesine yol açıyordu."""
    try:
        return st.secrets.get("GITHUB_TOKEN")
    except Exception:
        return None

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

# DİKKAT: Aşağıdaki liste, resmi/canlı bir Russell 2000 endeks bileşen
# beslemesi DEĞİLDİR (bu ortamda finans veri sitelerine - stockanalysis.com,
# ishares.com, wikipedia vb. - erişim ağ proxy'si tarafından engellendiğinden
# doğrulanamadı) - diğer DEFAULT_* listeleri gibi, elle kürasyon edilmiş,
# sektörlere yayılmış, göreceli olarak likit küçük/orta ölçekli hisselerden
# oluşan bir BAŞLANGIÇ seti. Russell endeksleri her yıl Haziran'da yeniden
# dengelendiğinden, bu listeyi periyodik olarak (⚙️ Hisse Listelerini Yönet
# sayfasından) gözden geçirip güncellemeniz önerilir. Asıl likidite güvencesi
# bu liste değil, otomatik_alim_satim_core.filter_by_liquidity'nin çalışma
# zamanında Alpaca'dan çektiği gerçek hacim verisidir.
DEFAULT_RUSSELL_2000 = [
    # Finans / Bölgesel Bankalar
    "UMBF", "PPBI", "SSB", "CATY", "WTFC", "ONB", "HOMB", "CVBF", "BANR", "GBCI",
    "COLB", "FHB", "PFS", "INDB", "WSFS", "TOWN", "NBTB", "FULT", "SFNC", "UBSI",
    # Biyoteknoloji / Sağlık
    "INSM", "HALO", "SUPN", "ACAD", "CORT", "PCVX", "ARWR", "FOLD", "ALKS", "RGNX",
    "VCEL", "NVAX", "KROS", "AXSM", "NARI", "TMDX", "IRTC", "GKOS", "OMCL", "CDNA",
    "NEOG", "MMSI",
    # Sanayi
    "FIX", "MOG.A", "FTAI", "FN", "AAON", "WTS", "ATKR", "CR", "GTES", "KAI",
    "TREX", "BECN", "MLI", "RBC", "CSWI", "MATX",
    # Teknoloji / Yazılım
    "FROG", "CRDO", "DV", "PRGS", "SPSC", "BL", "FIVN", "QLYS", "TENB", "VRNT",
    "EXLS", "TWST", "HUT",
    # Tüketim / Perakende
    "SFM", "BOOT",
    # GYO (REIT)
    "AAT", "CUZ", "STAG", "NHI", "IRT",
    # Enerji
    "CIVI", "MTDR", "SM", "CHRD", "VNOM",
]

MARKETS = ["NASDAQ 100", "NYSE", "BIST 100", "Russell 2000"]

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
        "BIST 100": list(dict.fromkeys(DEFAULT_BIST_100)),
        "Russell 2000": list(dict.fromkeys(DEFAULT_RUSSELL_2000)),
    }


def load_ticker_lists(username):
    """Kullanıcıya özel listeleri yükler. Önce GitHub'daki (kalıcı) kopyayı, yoksa yerel
    dosyayı, o da yoksa varsayılanları döner.

    Streamlit Cloud her yeniden başlatmada repoyu sıfırdan klonladığı için sadece
    diske yazmak kalıcı olmuyor - bu yüzden asıl kaynak GitHub'daki dosya."""
    custom_file = _custom_file(username)
    data = None
    token = _get_github_token()
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
        "BIST 100": list(dict.fromkeys(data.get("BIST 100", DEFAULT_BIST_100))),
        "Russell 2000": list(dict.fromkeys(data.get("Russell 2000", DEFAULT_RUSSELL_2000))),
    }


def save_ticker_lists(ticker_dict, username):
    """Kullanıcıya özel listeleri kalıcı olması için GitHub'a commit'ler (mümkün olduğunda),
    ayrıca yerel dosyaya da yazar."""
    custom_file = _custom_file(username)
    token = _get_github_token()
    if token:
        try:
            write_json_to_github(GITHUB_REPO, token, custom_file, ticker_dict, f"Update custom ticker lists ({username})")
        except Exception as e:
            st.warning(f"⚠️ Liste GitHub'a kalıcı olarak kaydedilemedi (sadece bu oturumda geçerli olacak): {e}")

    with open(custom_file, 'w', encoding='utf-8') as f:
        json.dump(ticker_dict, f, ensure_ascii=False, indent=4)


def _group_file(username):
    return f"custom_stock_groups_{username}.json"


def load_stock_groups(username):
    """Kullanıcının borsa listelerinden bağımsız, serbestçe adlandırıp
    oluşturduğu hisse gruplarını yükler. Önce GitHub'daki (kalıcı) kopyayı,
    yoksa yerel dosyayı, o da yoksa boş bir sözlük döner."""
    group_file = _group_file(username)
    data = None
    token = _get_github_token()
    if token:
        try:
            data = read_json_from_github(GITHUB_REPO, token, group_file, {})
        except Exception:
            data = None

    if not data and os.path.exists(group_file):
        try:
            with open(group_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = None

    if not data:
        return {}

    return {name: list(dict.fromkeys(tickers)) for name, tickers in data.items()}


def save_stock_groups(groups_dict, username):
    """Kullanıcının hisse gruplarını kalıcı olması için GitHub'a commit'ler (mümkün
    olduğunda), ayrıca yerel dosyaya da yazar - bkz. save_ticker_lists için aynı gerekçe."""
    group_file = _group_file(username)
    token = _get_github_token()
    if token:
        try:
            write_json_to_github(GITHUB_REPO, token, group_file, groups_dict, f"Update stock groups ({username})")
        except Exception as e:
            st.warning(f"⚠️ Hisse grupları GitHub'a kalıcı olarak kaydedilemedi (sadece bu oturumda geçerli olacak): {e}")

    with open(group_file, 'w', encoding='utf-8') as f:
        json.dump(groups_dict, f, ensure_ascii=False, indent=4)


def _group_market_file(username):
    return f"custom_stock_group_markets_{username}.json"


def load_group_markets(username):
    """Kullanıcının hisse gruplarının hangi piyasayla (NASDAQ 100 / NYSE / BIST 100)
    ilişkilendirildiğini tutan eşlemeyi (grup adı -> piyasa adı) yükler. Bu, hisse
    gruplarının piyasadan bağımsız serbestçe oluşturulmasının önüne geçip, gruplar
    arasında anlamlı (aynı piyasaya ait) analizler yapılabilmesini sağlar. Önce
    GitHub'daki (kalıcı) kopyayı, yoksa yerel dosyayı, o da yoksa boş bir sözlük döner."""
    market_file = _group_market_file(username)
    data = None
    token = _get_github_token()
    if token:
        try:
            data = read_json_from_github(GITHUB_REPO, token, market_file, {})
        except Exception:
            data = None

    if not data and os.path.exists(market_file):
        try:
            with open(market_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = None

    return data or {}


def save_group_markets(group_markets, username):
    """Hisse grubu -> piyasa eşlemesini kalıcı olması için GitHub'a commit'ler
    (mümkün olduğunda), ayrıca yerel dosyaya da yazar - bkz. save_stock_groups
    için aynı gerekçe."""
    market_file = _group_market_file(username)
    token = _get_github_token()
    if token:
        try:
            write_json_to_github(GITHUB_REPO, token, market_file, group_markets, f"Update stock group markets ({username})")
        except Exception as e:
            st.warning(f"⚠️ Hisse grubu-piyasa eşlemesi GitHub'a kalıcı olarak kaydedilemedi (sadece bu oturumda geçerli olacak): {e}")

    with open(market_file, 'w', encoding='utf-8') as f:
        json.dump(group_markets, f, ensure_ascii=False, indent=4)


def _capital_file(username):
    return f"initial_capital_{username}.json"


def load_initial_capital(username):
    """Kullanıcının Alpaca hesabına ilk yatırdığı sermayeyi yükler - Genel
    Bakış'taki portföyün anlık kârlılığını (nakit + pozisyon değeri, bu
    sermayeye göre) hesaplamak için referans değer. Kayıtlı değer yoksa
    None döner."""
    capital_file = _capital_file(username)
    data = None
    token = _get_github_token()
    if token:
        try:
            data = read_json_from_github(GITHUB_REPO, token, capital_file, None)
        except Exception:
            data = None

    if not data and os.path.exists(capital_file):
        try:
            with open(capital_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = None

    if not data:
        return None
    return data.get("initial_capital")


def save_initial_capital(amount, username):
    """İlk sermayeyi kalıcı olması için GitHub'a commit'ler (mümkün olduğunda),
    ayrıca yerel dosyaya da yazar - bkz. save_ticker_lists için aynı gerekçe."""
    capital_file = _capital_file(username)
    payload = {"initial_capital": amount}
    token = _get_github_token()
    if token:
        try:
            write_json_to_github(GITHUB_REPO, token, capital_file, payload, f"Update initial capital ({username})")
        except Exception as e:
            st.warning(f"⚠️ İlk sermaye GitHub'a kalıcı olarak kaydedilemedi (sadece bu oturumda geçerli olacak): {e}")

    with open(capital_file, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=4)


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