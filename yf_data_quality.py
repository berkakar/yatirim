"""Yahoo Finance'ten çekilen OHLCV verisinin güncel ve anlamlı olup
olmadığını doğrulayan ortak yardımcılar.

fon_hisse_uyari.py'deki BIST günlük değişim doğrulamasının (son barın
gerçekten bugüne ait olup olmadığı + günlük taban/tavan marjını aşan
tick'lerin elenmesi) genelleştirilmiş hali - scanner.py, dtw_analysis.py
ve hisse_patern_analysis.py bu modülü paylaşır ki her biri kendi ayrı
(ve tutarsız) tazelik/anlamlılık kontrolünü yazmasın.
"""
from datetime import date

import pandas as pd

# BIST'te bir hissenin tek günde hareket edebileceği marj (taban/tavan,
# devre kesici) ~%10 - ABD borsalarında (NASDAQ/NYSE) böyle bir günlük
# limit yok (haber/kazanç ile %10'un çok üzerinde gerçek hareketler
# olağan bir durum), bu yüzden bu eşik SADECE ".IS" ile biten (BIST)
# sembollerde uygulanır - bkz. fon_hisse_uyari.py'deki aynı sabit.
BIST_MAX_PLAUSIBLE_DAILY_PCT = 10.5

# Son barın tarihinin "bugün"den en fazla bu kadar takvim günü eski
# olmasına izin verilir - hafta sonu + resmi tatil + Yahoo'nun olağan
# yayın gecikmesini karşılamaya yeter; daha eskisi barın gerçekte
# çekilememiş/bayat bir önbellekten geldiğine işaret eder.
MAX_STALE_DAYS = 5


def is_ohlc_consistent(df: pd.DataFrame) -> bool:
    """Open/High/Low/Close arasındaki temel iç tutarlılığı (hiçbir fiyat
    <= 0 değil, High her satırda en büyük, Low en küçük) doğrular -
    piyasadan/periyottan bağımsız, her zaman doğru olması gereken bir
    kural. Yahoo'nun bozuk bir satır döndürdüğü (ör. bölünme/birleşme
    sırasında yanlış ayarlanmış fiyat) durumları yakalar."""
    if df is None or df.empty:
        return False
    cols = ("Open", "High", "Low", "Close")
    if not all(c in df.columns for c in cols):
        return False
    ohlc = df[list(cols)]
    if (ohlc <= 0).any().any():
        return False
    if (df["High"] < ohlc[["Open", "Close", "Low"]].max(axis=1)).any():
        return False
    if (df["Low"] > ohlc[["Open", "Close", "High"]].min(axis=1)).any():
        return False
    return True


def is_fresh(last_bar_date: date, as_of: date | None = None, max_stale_days: int = MAX_STALE_DAYS) -> bool:
    """Son barın tarihinin `as_of` (varsayılan: bugün) tarihine göre kabul
    edilebilir ölçüde güncel olup olmadığını döner."""
    as_of = as_of or date.today()
    return (as_of - last_bar_date).days <= max_stale_days


def has_implausible_daily_move(closes: pd.Series, ticker: str, max_pct: float = BIST_MAX_PLAUSIBLE_DAILY_PCT) -> bool:
    """Sadece BIST sembollerinde (`.IS` sonekli) anlamlı olan günlük
    taban/tavan marjını aşan, bardan-bara bir fiyat sıçraması olup
    olmadığını döner - ABD hisselerinde (NASDAQ/NYSE) uygulanmaz (bkz.
    modül başı notu). Granülariteden bağımsız çalışır: intraday barlarda
    da tek bir bar bu marjı aşıyorsa (tüm günün payına düşen sınırın
    üzerinde), bu kesin bir veri hatasıdır."""
    if not ticker.upper().endswith(".IS"):
        return False
    if closes is None or len(closes) < 2:
        return False
    changes = closes.pct_change().abs() * 100
    return bool((changes > max_pct).any())


# Bir pencerede en az bu kadar FARKLI kapanış fiyatı olması beklenir - altında
# kalması, Yahoo'nun işlem görmeyen/durdurulmuş bir sembol için aynı bayat
# fiyatı tekrar tekrar döndürdüğüne işaret eder. Bilinçli olarak çok düşük
# tutulur (yalnızca bariz durgun/bayat verileri yakalar): gerçekten seyrek
# işlem gören ama meşru bir hisseyi yanlışlıkla elememek için.
MIN_UNIQUE_CLOSES = 3


def has_flat_prices(closes: pd.Series, min_unique: int = MIN_UNIQUE_CLOSES) -> bool:
    """Kapanış fiyatlarının pencere boyunca neredeyse hiç değişmediğini
    (Yahoo'nun bayat/tekrarlanan veri döndürdüğü durumları) tespit eder.
    Barların sayısı `min_unique`'dan azsa (çok kısa bir pencere), bunun
    hakkında bir şey söylenemez, False (sorun yok) döner."""
    if closes is None or len(closes) < min_unique:
        return False
    return closes.nunique() < min_unique


def is_info_meaningful(info: dict | None, min_keys: int = 10) -> bool:
    """yfinance'in `.info` sözlüğünün gerçek bir şirkete mi ait yoksa
    delisted/durdurulmuş/geçersiz bir sembol için neredeyse boş bir yanıt
    mı olduğunu ayırt eder. Gerçek, aktif bir hissede yfinance genelde
    100'ün üzerinde alan döner; geçersiz bir sembolde sözlük boş ya da
    birkaç anahtarla sınırlı olur. Anahtar sayısı yeterli olsa bile hiçbir
    temel sinyal (sektör, piyasa değeri, herhangi bir fiyat çarpanı) yoksa
    yine de reddedilir - şirket kabuğu var ama veri yok demektir."""
    if not info:
        return False
    if len(info) < min_keys:
        return False
    return any(
        info.get(field) not in (None, "")
        for field in ("sector", "marketCap", "trailingPE", "priceToBook", "enterpriseToEbitda")
    )
