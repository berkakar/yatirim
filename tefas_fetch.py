"""TEFAS'tan günlük fon verisini çekip önbelleği (tefas_fonlari_cache.json)
günceller. GitHub Actions tarafından günde bir kez, TEFAS'ın fiyatları
tamamladığı saatten (~19:00 TRT) 10 dakika sonra çalıştırılır - bkz.
.github/workflows/tefas_fonlari.yml.

Çekilen ham veri (fiyat, fon toplam değeri, geçmiş) ile hesaplanmış tablo
(5/10/15/30 iş günlük fiyat ve hacim değişim %) aynı JSON dosyasında
tutulur; her çalıştırma sadece son çalıştırmadan bu yana eksik günleri
TEFAS'tan çeker, tüm geçmişi değil (bkz. tefas_fonlari_data.update_cache).

Run with --once (used by the GitHub Actions workflow).
"""
import argparse
from datetime import datetime

from tefas_fonlari_data import load_cache, save_cache, update_cache


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def run_once() -> None:
    before_date = (load_cache().get("meta") or {}).get("last_fetched_date")

    cache = update_cache()
    after_date = (cache.get("meta") or {}).get("last_fetched_date")

    # Her zaman kaydet - "table" güncelleme mantığındaki bir kod değişikliği
    # yüzünden de değişmiş olabilir, sadece yeni bir TEFAS gününde değil.
    # Gerçekten hiçbir şey değişmediyse üretilen JSON öncekiyle birebir aynı
    # olur; workflow'daki git diff adımı bu durumda zaten commit atmaz.
    save_cache(cache)
    if after_date == before_date:
        log(f"Yeni TEFAS verisi yok (son çekilen tarih hâlâ {after_date}), tablo yine de yeniden hesaplanıp kaydedildi.")
    else:
        log(f"Önbellek güncellendi: {after_date}, {len(cache.get('table', []))} fon.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Tek seferlik çalıştırma (GitHub Actions).")
    args = parser.parse_args()

    run_once()
