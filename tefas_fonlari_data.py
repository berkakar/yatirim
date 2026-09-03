"""Türk Fonları modülü - TEFAS'tan çekilen fon verisinin önbelleklenmesi ve
5/10/15/30 iş günlük fiyat/hacim (fon toplam değeri) değişim tablosunun
hesaplanması.

Fon kategorisi (Hisse Senedi Yoğun / Değişken / Mutlak Getiri / İstatistiksel
Arbitraj): TEFAS'ın resmi unvan-türü filtresinin bu script'in kullandığı
uçtan (fonGnlBlgSiraliGetir) nasıl çalıştığı doğrulanamadığı için (bkz.
tefas_client.py docstring'i), fon unvanındaki anahtar kelimelere bakılarak
tespit edilir - TEFAS fonları resmi unvanlarında türlerini belirtir (SPK
düzenlemesi gereği). Bu bir sezgisel yöntemdir; ilk gerçek çalıştırmadan
sonra çıkan sınıflandırmanın gözden geçirilmesi önerilir.

CACHE_FILE hem ham geçmiş fiyat/büyüklük verisini ("funds") hem de
hesaplanmış değişim tablosunu ("table") tutar:
  - Her çalıştırma sadece son çalıştırmadan bu yana eksik günleri TEFAS'tan
    çeker (tüm geçmişi yeniden değil) - API'yi verimli kullanır.
  - Streamlit tarafı (tefas_fonlari.py) hiçbir hesaplama yapmadan, sadece
    "table" alanını okuyup gösterir.
"""
import json
import os
from datetime import date, datetime, timedelta

from tefas_client import fetch_fund_info

CACHE_FILE = "tefas_fonlari_cache.json"
HISTORY_RETENTION_DAYS = 50  # 30 iş günlük pencereye tatil/hafta sonu payı bırakan takvim günü
LOOKBACK_WINDOWS = (1, 5, 10, 15, 30)
FUND_KIND = "YAT"  # Yatırım Fonları - bu 4 kategori bu şemsiye altında yer alır

FUND_CATEGORIES = {
    "hisse_senedi_yogun": ("Hisse Senedi Yoğun Fon", ("hisse senedi yoğun",)),
    "degisken": ("Değişken Fon", ("değişken fon", "değişken şemsiye")),
    "mutlak_getiri": ("Mutlak Getiri Fonu", ("mutlak getiri",)),
    "istatistiksel_arbitraj": ("İstatistiksel Arbitraj Fonu", ("istatistiksel arbitraj",)),
}


def _tr_lower(s: str) -> str:
    """Türkçe büyük/küçük harf dönüşümü (İ->i, I->ı) - ASCII varsayımlı
    str.lower()'ın 'İstatistiksel' gibi kelimelerde bozduğu eşleşmeyi önler."""
    return s.replace("İ", "i").replace("I", "ı").lower()


def classify_fund(fund_name: str) -> str | None:
    """Fon unvanını 4 kategoriden birine eşler, eşleşme yoksa None döner
    (bu fon tabloya dahil edilmez)."""
    name = _tr_lower(fund_name or "")
    for category_id, (_, keywords) in FUND_CATEGORIES.items():
        if any(_tr_lower(kw) in name for kw in keywords):
            return category_id
    return None


def load_cache() -> dict:
    if not os.path.exists(CACHE_FILE):
        return {"meta": {}, "funds": {}, "table": []}
    with open(CACHE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_cache(cache: dict) -> None:
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _merge_rows(funds: dict, rows: list[dict]) -> None:
    for row in rows:
        code = row.get("fund_code")
        raw_date = row.get("date")
        price = row.get("price")
        if not code or not raw_date or price is None:
            continue
        day = str(raw_date)[:10]  # TEFAS "2026-09-02T00:00:00" -> "2026-09-02"
        size = row.get("portfolio_size")

        entry = funds.setdefault(code, {"fund_name": row.get("fund_name") or "", "history": {}})
        if row.get("fund_name"):
            entry["fund_name"] = row["fund_name"]
        entry["history"][day] = {
            "price": float(price),
            "portfolio_size": float(size) if size is not None else None,
        }


def _prune_history(funds: dict, keep_after: str) -> None:
    for entry in funds.values():
        entry["history"] = {d: v for d, v in entry["history"].items() if d >= keep_after}


def _pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return round((new - old) / old * 100, 2)


def _build_table(funds: dict) -> list[dict]:
    rows = []
    for code, entry in funds.items():
        category_id = classify_fund(entry.get("fund_name", ""))
        if category_id is None:
            continue

        dates = sorted(entry["history"].keys())
        if len(dates) < 2:
            continue
        series = [entry["history"][d] for d in dates]
        today, prev = series[-1], series[-2]

        row = {
            "Fon Kodu": code,
            "Fon Adı": entry.get("fund_name", ""),
            "Kategori": FUND_CATEGORIES[category_id][0],
            "Tarih": dates[-1],
            "Önceki Gün Fiyat": round(prev["price"], 4),
            "Bugünkü Fiyat": round(today["price"], 4),
        }
        for window in LOOKBACK_WINDOWS:
            if len(series) > window:
                base = series[-1 - window]
                row[f"{window}G Fiyat Değişim %"] = _pct_change(today["price"], base["price"])
                row[f"{window}G Hacim Değişim %"] = _pct_change(today["portfolio_size"], base["portfolio_size"])
            else:
                row[f"{window}G Fiyat Değişim %"] = None
                row[f"{window}G Hacim Değişim %"] = None
        rows.append(row)

    rows.sort(key=lambda r: (r["Kategori"], r["Fon Kodu"]))
    return rows


def update_cache() -> dict:
    """TEFAS'tan son çalıştırmadan bu yana eksik günleri çeker, önbelleğe
    (ham geçmiş + hesaplanmış tablo) yazar. Yeni veri yoksa (tatil/hafta
    sonu, ya da TEFAS henüz yayınlamadıysa) önbelleği değiştirmeden döner -
    çağıran taraf meta.last_fetched_date değişip değişmediğine bakarak
    diske yazıp yazmayacağına karar verir."""
    cache = load_cache()
    funds = cache.get("funds", {})

    today = date.today()
    last_fetched = (cache.get("meta") or {}).get("last_fetched_date")
    start = (
        datetime.strptime(last_fetched, "%Y-%m-%d").date() + timedelta(days=1)
        if last_fetched else today - timedelta(days=HISTORY_RETENTION_DAYS)
    )
    if start > today:
        return cache

    rows = fetch_fund_info(start, today, kind=FUND_KIND)
    fetched_dates = {str(r["date"])[:10] for r in rows if r.get("date")}
    if not fetched_dates:
        return cache

    _merge_rows(funds, rows)
    keep_after = (today - timedelta(days=HISTORY_RETENTION_DAYS)).strftime("%Y-%m-%d")
    _prune_history(funds, keep_after)

    cache["funds"] = funds
    cache["table"] = _build_table(funds)
    cache["meta"] = {
        "last_fetched_date": max(fetched_dates),
        "last_updated": datetime.now().isoformat(timespec="seconds"),
        "fund_count": len(cache["table"]),
    }
    return cache
