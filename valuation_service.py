"""
Değerleme & Ucuzluk Skoru piyasa servisleri.

Her piyasa (NASDAQ 100, NYSE, BIST 100, Russell 2000) için ayrı bir servis vardır
ve her biri gün içinde ayrı bir saatte çalışır (bkz. deploy/systemd/
yatirim-valuation-*.timer) - Yahoo Finance'e tüm hisseler için aynı anda
gidilmesin diye. Bir servis çalıştığında:

1. Evreni oluşturur: tüm kullanıcıların o piyasa listesi + tüm kullanıcıların o
   piyasayla ilişkilendirdiği hisse grupları. Tekrarlayan hisseler elenir.
2. Veritabanında o piyasaya ait olup artık ne piyasa listesinde ne de herhangi bir
   kullanıcının hisselerinde (grupları ya da kayıtlı seçimleri) bulunan satırları
   siler. Kalan satırlar (örn. arayüzün anlık çekip kaydettiği bir kullanıcı
   hissesi) evrene eklenir ki bu çalıştırmada güncellensin.
3. Yahoo Finance'ten 50'lik paketler halinde, paketler arasında bekleyerek çeker.
   429 (çok fazla istek) gelirse artan sürelerle bekleyip tekrar dener; ısrar
   ederse çekimi bırakır, o ana kadar çekilenlerle devam eder.
4. Çekim bittikten sonra skorları mevcut kriterlerle (valuation.
   calculate_sector_relative_scores) evrenin tamamı üzerinden hesaplar.
5. Sonuçları tarih/saatle veritabanına yazar; satır varsa üzerine yazar.

Kullanım:
    python valuation_service.py --market nasdaq100
    python valuation_service.py --market bist100 --batch-size 50 --pause 30

Arayüz tarafı (seçilen portföyün veritabanından getirilmesi, eksik hisselerin
anlık çekilmesi) get_scores_for_selection() fonksiyonundadır.
"""

import argparse
import glob
import os
import random
import sys
import time

import config
import storage
import valuation
import valuation_db as db

MARKET_SLUGS = {
    "nasdaq100": "NASDAQ 100",
    "nyse": "NYSE",
    "bist100": "BIST 100",
    "russell2000": "Russell 2000",
}

BATCH_SIZE = int(os.environ.get("VALUATION_BATCH_SIZE", "50"))
# Servis: paketler arası bekleme (sn) ve hisseler arası rastgele kısa bekleme aralığı.
SERVICE_BATCH_PAUSE_S = float(os.environ.get("VALUATION_BATCH_PAUSE_S", "30"))
SERVICE_TICKER_DELAY_S = (0.3, 1.0)
# Arayüz: kullanıcı beklediği için daha kısa.
UI_BATCH_PAUSE_S = 5.0
UI_TICKER_DELAY_S = (0.1, 0.3)
# 429 gelince bekleme: 60 sn, 120 sn, 240 sn; sonra çekim bırakılır.
RATE_LIMIT_BACKOFF_S = 60.0
RATE_LIMIT_MAX_RETRIES = 3
# Aynı hisse başka bir piyasanın evreninde son bu kadar saat içinde çekildiyse
# Yahoo'ya tekrar gidilmez, o veri kullanılır.
SERVICE_REUSE_HOURS = 6
UI_REUSE_HOURS = 24

_USER_RECORDS = ("custom_tickers", "custom_stock_groups", "custom_stock_group_markets", "selected_tickers")


def log(msg):
    print(f"[valuation] {msg}", flush=True)


def normalize_ticker(ticker) -> str:
    return str(ticker or "").strip().upper()


def dedupe(tickers) -> list:
    """Sırayı koruyarak tekrarları ve boşları eler (büyük/küçük harf ve boşluk farkı yok sayılır)."""
    return list(dict.fromkeys(t for t in (normalize_ticker(x) for x in tickers) if t))


# ------------------------------------------------------------------------------
# Evren
# ------------------------------------------------------------------------------

def known_users() -> list:
    """Hisse listesi, grubu ya da seçimi kayıtlı tüm kullanıcılar."""
    users = set()
    if storage.enabled():
        for name in _USER_RECORDS:
            users.update(storage.users_with(name))
    else:
        for path in glob.glob("*.json"):
            key = storage.key_for_path(path)
            if key and key[1] in _USER_RECORDS and key[0] != storage.SHARED:
                users.add(key[0])
    return sorted(users)


def build_universe(market: str):
    """(evren, kullanıcı_hisseleri) döner.

    evren: tüm kullanıcıların `market` listesi + `market` ile ilişkilendirilmiş tüm
    kullanıcı grupları, tekrarsız. Kayıtlı kullanıcı yoksa varsayılan liste.
    kullanıcı_hisseleri: tüm kullanıcıların tüm grupları (piyasadan bağımsız) ve
    kayıtlı seçimleri - veritabanındaki bir satırın silinip silinmeyeceğine karar
    vermek için."""
    users = known_users()
    market_tickers = []
    group_tickers = []
    user_stocks = []
    if not users:
        market_tickers = config._defaults().get(market, [])
    for user in users:
        market_tickers += config.load_ticker_lists(user).get(market, [])
        groups = config.load_stock_groups(user)
        group_markets = config.load_group_markets(user)
        for group, tickers in groups.items():
            user_stocks += tickers
            if group_markets.get(group) == market:
                group_tickers += tickers
        user_stocks += storage.load_json(f"selected_tickers_{user}.json", []) or []
    return dedupe(market_tickers + group_tickers), set(dedupe(user_stocks))


# ------------------------------------------------------------------------------
# Paketler halinde çekim
# ------------------------------------------------------------------------------

def fetch_in_batches(tickers, batch_size=BATCH_SIZE, batch_pause_s=SERVICE_BATCH_PAUSE_S,
                     ticker_delay_s=SERVICE_TICKER_DELAY_S, fetcher=None, sleep=time.sleep,
                     progress_callback=None):
    """Hisseleri `batch_size`lık paketler halinde Yahoo'dan çeker.

    Döner: (sonuçlar {hisse: ham_veri}, veri_gelmeyenler [..], çekim_bırakıldı_mı).
    Çekim bırakıldıysa (ısrarlı 429) kalan hisseler hiç denenmemiştir."""
    fetcher = fetcher or valuation.fetch_single_ticker_raw
    sub_sectors_map = valuation.load_sub_sectors()
    results, failed = {}, []
    total = len(tickers)
    done = 0
    for batch_no, start in enumerate(range(0, total, batch_size)):
        batch = tickers[start:start + batch_size]
        if batch_no > 0 and batch_pause_s > 0:
            sleep(batch_pause_s + random.uniform(0, batch_pause_s * 0.25))
        for ticker in batch:
            retries = 0
            while True:
                try:
                    raw = fetcher(ticker, sub_sectors_map, raise_on_rate_limit=True)
                    break
                except valuation.YahooRateLimited:
                    retries += 1
                    if retries > RATE_LIMIT_MAX_RETRIES:
                        log(f"Yahoo {ticker} için {RATE_LIMIT_MAX_RETRIES} beklemeden sonra da 429 "
                            f"verdi; çekim bırakıldı ({done}/{total} hisse denendi).")
                        return results, failed, True
                    wait = RATE_LIMIT_BACKOFF_S * (2 ** (retries - 1))
                    log(f"Yahoo 429 ({ticker}) - {wait:.0f} sn beklenip tekrar denenecek ({retries}/{RATE_LIMIT_MAX_RETRIES}).")
                    sleep(wait)
            if raw:
                results[ticker] = raw
            else:
                failed.append(ticker)
            done += 1
            if progress_callback:
                progress_callback(done, total, ticker)
            if ticker_delay_s:
                sleep(random.uniform(*ticker_delay_s))
        log(f"Paket {batch_no + 1}: {min(start + batch_size, total)}/{total} hisse denendi.")
    return results, failed, False


# ------------------------------------------------------------------------------
# Skor
# ------------------------------------------------------------------------------

def score_records(records):
    """records: [{"ticker", "raw", ...}] -> her kayda "scored" (skor satırı) eklenmiş liste.
    Skorlar (alt sektör medyanı dahil) listenin tamamı üzerinden hesaplanır."""
    records = [r for r in records if r.get("raw")]
    if not records:
        return []
    scored = valuation.calculate_sector_relative_scores([r["raw"] for r in records])
    for record, row in zip(records, scored):
        record["scored"] = row
    return records


# ------------------------------------------------------------------------------
# Servis
# ------------------------------------------------------------------------------

def run_market(market: str, batch_size=BATCH_SIZE, batch_pause_s=SERVICE_BATCH_PAUSE_S,
               ticker_delay_s=SERVICE_TICKER_DELAY_S, fetcher=None, sleep=time.sleep, now=None):
    """Bir piyasa servisinin tek çalıştırması. Özet sözlüğü döner."""
    now = now or db.utc_now
    started_at = db.to_iso(now())

    base, user_stocks = build_universe(market)
    base_set = set(base)
    existing = db.get_rows(market)

    # Ne piyasa/grup evreninde ne de herhangi bir kullanıcının hisselerinde olanlar silinir.
    stale = sorted(t for t in existing if t not in base_set and t not in user_stocks)
    removed = db.delete_rows(market, stale)
    kept_extra = sorted(t for t in existing if t not in base_set and t not in stale)
    universe = base + kept_extra
    log(f"{market}: evren {len(universe)} hisse ({len(base)} piyasa+grup, {len(kept_extra)} "
        f"kullanıcı hissesi), {removed} artık kullanılmayan satır silindi.")

    reusable = db.get_recent_raw(universe, SERVICE_REUSE_HOURS, exclude_market=market, now=now())
    to_fetch = [t for t in universe if t not in reusable]
    fetched, failed, aborted = fetch_in_batches(
        to_fetch, batch_size=batch_size, batch_pause_s=batch_pause_s,
        ticker_delay_s=ticker_delay_s, fetcher=fetcher, sleep=sleep,
    )
    fetched_at = db.to_iso(now())

    records = []
    for ticker in universe:
        source = db.SOURCE_SERVICE if ticker in base_set else db.SOURCE_ON_DEMAND
        if ticker in fetched:
            records.append({"ticker": ticker, "raw": fetched[ticker], "fetched_at": fetched_at, "source": source})
        elif ticker in reusable:
            raw, ts = reusable[ticker]
            records.append({"ticker": ticker, "raw": raw, "fetched_at": ts, "source": source})
        elif ticker in existing:
            # Bu sefer veri gelmedi (geçici hata / çekim bırakıldı) - eski veriyle skorlanmaya devam eder.
            old = existing[ticker]
            records.append({"ticker": ticker, "raw": old["raw"], "fetched_at": old["fetched_at"], "source": source})

    records = score_records(records)
    scored_at = db.to_iso(now())
    db.upsert_rows(
        {"market": market, "ticker": r["ticker"], "raw": r["raw"], "scored": r["scored"],
         "fetched_at": r["fetched_at"], "scored_at": scored_at, "source": r["source"]}
        for r in records
    )

    summary = {
        "started_at": started_at,
        "finished_at": db.to_iso(now()),
        "universe_size": len(universe),
        "fetched": len(fetched),
        "reused": len(reusable),
        "failed": len(failed),
        "removed": removed,
        "aborted": aborted,
        "note": ("Yahoo 429 nedeniyle çekim yarıda bırakıldı; çekilemeyenler eski veriyle skorlandı."
                 if aborted else None),
    }
    db.record_run(market, **summary)
    log(f"{market}: {len(fetched)} çekildi, {len(reusable)} başka piyasadan alındı, "
        f"{len(failed)} veri gelmedi, {len(records)} satır yazıldı.")
    summary["written"] = len(records)
    return summary


# ------------------------------------------------------------------------------
# Arayüz
# ------------------------------------------------------------------------------

def get_scores_for_selection(market: str, tickers, progress_callback=None, fetcher=None,
                             sleep=time.sleep, now=None):
    """Kullanıcının seçtiği hisselerin skorlarını getirir.

    Veritabanında `market` için satırı olanlar oradan okunur. Olmayanlar (başka bir
    piyasada son 24 saatte çekilmişse o veri, yoksa Yahoo'dan) alınır, bu piyasanın
    veritabanındaki tüm hisseleriyle birlikte skorlanır ve `market` altına kaydedilir
    - böylece sonraki servis çalıştırmasında güncellenir (kullanıcı hissesi olmaya
    devam ettiği sürece; bkz. run_market).

    Döner: (satırlar, özet). Her satır skor tablosunun satırıdır ve ek olarak
    "_fetched_at" (UTC ISO) ile "_source" alanlarını taşır."""
    now = now or db.utc_now
    tickers = dedupe(tickers)
    market_rows = db.get_rows(market)
    found = [t for t in tickers if t in market_rows]
    missing = [t for t in tickers if t not in market_rows]

    fetched, failed, aborted, reusable = {}, [], False, {}
    new_records = []
    if missing:
        reusable = db.get_recent_raw(missing, UI_REUSE_HOURS, exclude_market=market, now=now())
        to_fetch = [t for t in missing if t not in reusable]
        fetched, failed, aborted = fetch_in_batches(
            to_fetch, batch_pause_s=UI_BATCH_PAUSE_S, ticker_delay_s=UI_TICKER_DELAY_S,
            fetcher=fetcher, sleep=sleep, progress_callback=progress_callback,
        )
        fetched_at = db.to_iso(now())
        for ticker in missing:
            if ticker in fetched:
                new_records.append({"ticker": ticker, "raw": fetched[ticker], "fetched_at": fetched_at})
            elif ticker in reusable:
                raw, ts = reusable[ticker]
                new_records.append({"ticker": ticker, "raw": raw, "fetched_at": ts})

    if new_records:
        peers = [{"ticker": t, "raw": r["raw"]} for t, r in market_rows.items()]
        # Yeni kayıtların hepsinde ham veri var, skor listesinin sonunda yer alırlar.
        new_scored = score_records(peers + new_records)[-len(new_records):]
        scored_at = db.to_iso(now())
        db.upsert_rows(
            {"market": market, "ticker": r["ticker"], "raw": r["raw"], "scored": r["scored"],
             "fetched_at": r["fetched_at"], "scored_at": scored_at, "source": db.SOURCE_ON_DEMAND}
            for r in new_scored
        )
        for r in new_scored:
            market_rows[r["ticker"]] = {"scored": r["scored"], "fetched_at": r["fetched_at"],
                                        "source": db.SOURCE_ON_DEMAND}

    rows = []
    for ticker in tickers:
        row = market_rows.get(ticker)
        if row:
            rows.append({**row["scored"], "_fetched_at": row["fetched_at"], "_source": row["source"]})

    summary = {
        "from_db": len(found),
        "fetched": len(fetched),
        "reused": len([t for t in missing if t in reusable]),
        "failed": failed,
        "aborted": aborted,
        "last_run": db.get_run(market),
    }
    return rows, summary


# ------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Değerleme & Ucuzluk Skoru piyasa servisi")
    parser.add_argument("--market", required=True, choices=sorted(MARKET_SLUGS),
                        help="çalıştırılacak piyasa servisi")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--pause", type=float, default=SERVICE_BATCH_PAUSE_S,
                        help="paketler arası bekleme (sn)")
    args = parser.parse_args(argv)

    if not storage.enabled():
        log(f"UYARI: YATIRIM_DB_PATH tanımlı değil - sonuçlar yerel {storage.db_path()} dosyasına yazılacak.")
    summary = run_market(MARKET_SLUGS[args.market], batch_size=args.batch_size, batch_pause_s=args.pause)
    # Hiç satır yazılamadıysa (örn. Yahoo baştan engelledi) iş başarısız sayılsın, Telegram'a bildirilsin.
    if summary["written"] == 0 and summary["universe_size"] > 0:
        log("HATA: hiçbir hisse için veri yazılamadı.")
        return 1
    if summary["aborted"]:
        log("UYARI: Yahoo 429 nedeniyle çekim yarıda kaldı.")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
