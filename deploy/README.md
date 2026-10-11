# Zamanlanmış işleri DigitalOcean Droplet'e taşıma

GitHub Actions'ın `schedule` tetikleyicisi dakikasında çalışmıyor (5-30 dk gecikme,
bazen hiç çalışmama). Bu klasör, aynı işleri bir Droplet'te **systemd timer'ları** ile
tam saniyesinde çalıştırır. Durum (state) dosyaları eskisi gibi repoya commit'lenir.
Streamlit arayüzü de isteğe bağlı olarak aynı Droplet'e taşınabilir (bkz. aşağıda).

## Nasıl çalışıyor

- `jobs.sh`: her işin komutları, commit'lenecek state dosyaları ve zaman aşımı
  (eski workflow'ların birebir karşılığı).
- `systemd/yatirim-<iş>.timer`: zamanlamalar. Saatler **America/New_York** (ABD işleri)
  ve **Europe/Istanbul** (TEFAS/BIST) saat dilimine göre yazıldığı için DST geçişleri
  kendiliğinden doğru işler. Eskiden UTC'ye göre ayarlanan çift pencereye gerek kalmadı.
- `run_job.sh`: her çalıştırmada işin kendi klonunu `origin/main`'e eşitler, komutları
  çalıştırır, değişen state'i `[skip ci]` ile commit'leyip push'lar. Hata olursa
  Telegram'a bildirim gönderir.
- Aynı iş hâlâ çalışıyorken gelen tetikleme systemd tarafından atlanır (eski
  `concurrency:` karşılığı). Farklı işler ayrı klonlarda çalıştığı için birbirini
  etkilemez.

| İş | Eski workflow | Zamanlama |
|---|---|---|
| `trailing-stop` | alpaca_trailing_stop.yml | Hafta içi 09:00–16:55 ET, 5 dk'da bir |
| `ext-hours-guard` | alpaca_extended_hours_guard.yml | 04:00–09:50 ve 16:00–19:50 ET, 10 dk'da bir |
| `ext-hours-entries` | alpaca_extended_hours_entries.yml | 04:00–09:50 ve 16:00–19:50 ET, 10 dk'da bir |
| `heikin-ashi` | heikin_ashi_intraday.yml | 09:02–15:32 ET, :02 ve :32'de + 15:45 gün sonu |
| `orb-scan` | orb_scan.yml | 10:30 ET (+10:45 yedek) |
| `otomatik-alim-satim` | otomatik_alim_satim.yml | Hafta içi 03:00 ET |
| `relative-strength` | relative_strength.yml | Pazartesi 10:00 ET (piyasa açıkken çalışmalı) |
| `tefas` | tefas_fonlari.yml | Hafta içi 09:00, 13:00, 19:10 TRT |
| `fon-hisse-uyari` | fon_hisse_uyari.yml | Hafta içi 10:00–17:55 TRT, 5 dk'da bir |
| `russell2000` | update_russell2000.yml | Her ayın 1'i 06:00 UTC |
| `valuation-bist100` | — (yeni) | Hafta içi 18:40 TRT |
| `valuation-nasdaq100` | — (yeni) | Hafta içi 17:15 ET |
| `valuation-nyse` | — (yeni) | Hafta içi 18:45 ET |
| `market-sentiment` | — (yeni) | Hafta içi 16:40 ET |
| `ai-dataset` | — (yeni) | Hafta içi 18:15 ET |
| `valuation-russell2000` | — (yeni) | Haftada 1 döngü: Cumartesi 00:05 ET başlar, saatte 1 paket (timer saat başı +5 dk tetiklenir, döngü yoksa boşta çıkar) |

Sadece elle tetiklenen `kap_refresh_holdings.yml`, `orb_stop_status.yml` ve ORB'nin
`reconcile_symbols` girişi GitHub Actions'ta kaldı. `update_version.yml` de orada kaldı.
Droplet commit'leri `[skip ci]` taşıdığı için onu tetiklemez.

## Kurulum

1. **Droplet oluşturun:** Ubuntu 24.04, Basic, 1 GB RAM (~6$/ay), bölge **NYC**
   (Alpaca'ya yakın). Giriş için SSH anahtarı kullanın.
2. **Repoyu klonlayıp kurulumu çalıştırın:**
   ```bash
   git clone https://github.com/berkakar/yatirim.git /root/yatirim
   cd /root/yatirim && sudo bash deploy/install.sh --with-playwright
   ```
   İlk çalıştırmada bir **deploy key** yazdırılıp durulur. Bu anahtarı GitHub'da
   *Settings → Deploy keys → Add deploy key* ekranına yapıştırın,
   **"Allow write access"** kutusunu işaretleyin ve script'i tekrar çalıştırın.
   (`--with-playwright` sadece Russell 2000 işi için gerekli.)
3. **Secret'ları girin:** `sudo nano /etc/yatirim/env`. Değerler GitHub'daki
   `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY` ve `TELEGRAM_BOT_TOKEN` secret'larıyla aynı.
   **Gerçek Para:** işlerin hangi Alpaca hesabında çalışacağını `APCA_API_BASE_URL`
   değil, arayüzdeki *Giriş Sayfası → ⚙️ Alpaca Hesap Türü* ayarı belirler (bkz.
   `alpaca_account.py`). Gerçek Para seçilecekse buraya gerçek hesabın anahtarlarını
   `APCA_LIVE_API_KEY_ID` / `APCA_LIVE_API_SECRET_KEY` olarak ekleyin; eklenmemişse
   Gerçek Para seçiliyken işler çalışmaz (Telegram'a hata gider), paper hesaba düşmez.
4. **Deneyin:** Alpaca'ya dokunmayan bir işle başlayın.
   ```bash
   sudo systemctl start yatirim-job@tefas
   journalctl -u yatirim-job@tefas -n 50
   ```
5. **Geçiş:** GitHub Actions'taki `schedule:` bloklarını kaldıran değişikliği main'e
   alın, ardından hemen timer'ları açın:
   ```bash
   sudo /opt/yatirim/bin/yatirim-timers enable
   sudo /opt/yatirim/bin/yatirim-timers status
   ```
   ⚠️ İkisini aynı anda açık bırakmayın, aynı alım iki kez yapılabilir.

## Değerleme & Ucuzluk Skoru servisleri

`valuation-*` işleri `valuation_service.py`'yi her piyasa için ayrı saatte çalıştırır
(Yahoo Finance'in toplu istekleri engellememesi için). Her çalıştırma:

1. Evreni kurar: tüm kullanıcıların o piyasa listesi + o piyasaya bağlı tüm kullanıcı
   grupları, tekrarlar elenmiş olarak.
2. Veritabanında o piyasaya ait olup artık ne piyasa listesinde ne de herhangi bir
   kullanıcının hisselerinde (grupları, kayıtlı seçimleri) olan satırları siler.
3. Yahoo'dan 50'lik paketler halinde çeker (paketler arası ~30 sn; 429 gelirse
   60/120/240 sn bekleyip tekrar dener, sonra bırakır). **Russell 2000** istisna:
   haftada 1 döngü, her saat yalnızca 1 paket; ~2000 hisse ≈ 40 saat. Ara veriler
   `valuation_cycles` tablosunda birikir, skorlar son paketten sonra yazılır. Elle
   tek seferde doldurmak için: `python valuation_service.py --market russell2000 --full`.
4. Çekim bitince skorları mevcut kriterlerle hesaplar ve `valuation_scores` tablosuna
   tarih/saatle yazar (varsa üzerine). Yahoo'da varsa bilanço dönem tarihi ve bir sonraki
   bilanço açıklama tarihi de kaydedilir.

Arayüz seçilen portföyü bu tablodan okur; tabloda olmayan bir hisse Yahoo'dan anlık
çekilir, piyasanın kayıtlı hisseleriyle birlikte skorlanır ve kaydedilir. Bu işler
**SQLite gerektirir** (`YATIRIM_DB_PATH`). Ayarlar: `VALUATION_BATCH_SIZE` (50),
`VALUATION_BATCH_PAUSE_S` (30).

Devreye alma: kod sunucuda `/opt/yatirim/app` altında dakikada bir main ile eşitlendiği
için betik dosyaları oradan alır (ayrı klon gerekmez). PR main'e alındıktan sonra:

```bash
sudo bash /opt/yatirim/app/deploy/enable_valuation.sh --fill                  # kur, timer'ları aç, BIST/NASDAQ/NYSE'yi doldur
sudo bash /opt/yatirim/app/deploy/enable_valuation.sh --fill --with-russell   # + Russell 2000 (tek seferde, ~1 saat)
sudo bash /opt/yatirim/app/deploy/enable_valuation.sh status                  # timer'lar, son çalışmalar, kayıt sayıları
sudo bash /opt/yatirim/app/deploy/enable_valuation.sh disable                 # timer'ları kapat
journalctl -u yatirim-valuation-fill -f                                       # ilk doldurmayı izle
```

İlk doldurma arka planda (systemd-run) ve piyasalar sırayla çalışır; terminali kapatmak
onu durdurmaz.

## Piyasa Duyarlılığı servisi

`market-sentiment` işi `market_sentiment.py`'yi hafta içi 16:40 ET'de (ABD kapanışından
sonra) çalıştırır ve NASDAQ 100, NYSE ve BIST 100 için 0-100 arası bir Korku/Açgözlülük
skoru hesaplar (BIST o saatte, 23:40 TRT, çoktan kapanmıştır). Veri Yahoo Finance'ten piyasa
başına tek istekte çekilir (günlük kapanışlar, ~3 yıl):

| Bileşen | NASDAQ 100 | NYSE | BIST 100 |
|---|---|---|---|
| Momentum (endeks / 125 günlük ortalama) | `^NDX` | `^NYA` | `XU100.IS`, dolar bazında |
| Oynaklık (50 günlük ortalamasına göre, ters) | `^VXN` | `^VIX` | XU100 20 günlük gerçekleşen oynaklık |
| Genişlik (% hisse > 50 günlük ortalama) | NASDAQ 100 listesi | NYSE listesi | BIST 100 listesi (TL) |
| Yeni zirve / dip (52 hafta, son 5 gün) | NASDAQ 100 listesi | NYSE listesi | BIST 100 listesi, dolar bazında |
| Güvenli liman (endeks − güvenli liman, 20 gün) | `TLT` | `TLT` | `TRY=X` (USD/TRY) |
| Put/call oranı (bilgi, skora katılmaz) | `QQQ` | `SPY` | — (VİOP verisi Yahoo'da yok) |

BIST'te dolar bazı (fiyat / USDTRY), TL enflasyonunun momentum ve zirve ölçülerini yapısal
olarak açgözlülüğe kaydırmaması için. Son 300 barında hatalı Yahoo mumu (%10,5'i aşan
günlük sıçrama, donmuş fiyat; bkz. `yf_data_quality.py`) olan BIST hisseleri genişlik
ölçülerinden çıkarılır ve logda listelenir.

Listeler tüm kullanıcıların ilgili piyasa listelerinin birleşimidir. Sonuç storage'daki
`market_sentiment_cache` kaydına yazılır, **Giriş Sayfası** buradan gösterir. Kayıt yoksa
sayfadaki *🔄 Şimdi hesapla* düğmesi servisi beklemeden çalıştırır.

Devreye alma: kod sunucuda `/opt/yatirim/app` altında main ile eşitlendiği için betik
dosyaları oradan alır (ayrı klon gerekmez). Root olarak:

```bash
sudo bash /opt/yatirim/app/deploy/enable_sentiment.sh            # kur, timer'ı aç, ilk hesaplamayı yap
sudo bash /opt/yatirim/app/deploy/enable_sentiment.sh status     # timer + kayıtlı skorlar
sudo bash /opt/yatirim/app/deploy/enable_sentiment.sh run        # şimdi yeniden hesapla
sudo bash /opt/yatirim/app/deploy/enable_sentiment.sh disable    # timer'ı kapat
journalctl -u yatirim-job@market-sentiment --since today          # zamanlanmış çalışmaların logu
```

## Yapay Zeka Analiz Modülü: günlük veri seti güncellemesi

`ai-dataset` işi `ai_dataset.py update --all`'u hafta içi 18:15 ET'de çalıştırır. Arayüzdeki
**🤖 Yapay Zeka Analiz Modülü**'nde kaydedilmiş her eğitim veri setine, son kayıtlı günden
sonraki işlem günlerini ekler (`ai_dataset_daily`, kaynak `daily`). Var olan günler değişmez.
Saat, yeni günün tüm parçaları hazır olsun diye seçildi:

- 16:40 ET `market-sentiment`: NASDAQ 100 duyarlılık ve sektör ETF arşivi (`sentiment_daily`,
  `sector_etf_daily`)
- 17:15 ET `valuation-nasdaq100`: günün Ucuzluk Skoru (`valuation_scores_daily`)
- 18:15 ET `ai-dataset`: fiyat / hacim Yahoo'dan, VWAP Alpaca'dan (veri seti Alpaca VWAP'ıyla
  hazırlandıysa; anahtarlar `/etc/yatirim/env` içindeki `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY`,
  yoksa tipik fiyat), diğerleri veritabanındaki arşivlerden. NYSE değerleme servisinden
  (18:45 ET) önce biter.

Veri setleri arasında 5 sn beklenir. Bir hisse hata verse de diğerleri güncellenir; hata varsa
iş başarısız sayılır ve Telegram'a bildirilir. Tatil günlerinde eklenecek gün olmaz, iş
"0 yeni gün" ile biter.

Devreye alma (root olarak; dosyalar `/opt/yatirim/app`'ten alınır):

```bash
sudo bash /opt/yatirim/app/deploy/enable_ai_dataset.sh            # kur, timer'ı aç, şimdi güncelle
sudo bash /opt/yatirim/app/deploy/enable_ai_dataset.sh status     # timer + kayıtlı veri setleri
sudo bash /opt/yatirim/app/deploy/enable_ai_dataset.sh run        # şimdi güncelle
sudo bash /opt/yatirim/app/deploy/enable_ai_dataset.sh disable    # timer'ı kapat
journalctl -u yatirim-job@ai-dataset --since today                 # zamanlanmış çalışmaların logu
```

## Yapay Zeka Analiz Modülü: model eğitimi (PyTorch)

"3. Model Eğitimi" bölümü (`ai_model.py`) PyTorch gerektirir. `requirements.txt`'de yok:
PyPI'deki Linux paketi CUDA kütüphaneleriyle birkaç GB tutar ve ortak venv'e her
`requirements.txt` değişikliğinde inerdi. Sunucuda bir kez CPU sürümü (~200 MB) kurulur:

```bash
sudo -u yatirim /opt/yatirim/venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
sudo systemctl restart yatirim-streamlit     # arayüz torch'u görsün
```

Kurulu değilse bölüm bu komutu gösterir; uygulamanın geri kalanı etkilenmez.

### Eğitimi sunucuda çalıştırma

Eğitim CPU'da yapılır ve uzun sürebilir (5 yıllık veri: 1 çekirdekte tur başına birkaç dakika).
`deploy/ai_train.sh` eğitimi ayrı, düşük öncelikli bir systemd birimi olarak başlatır - SSH
bağlantısı koparsa durmaz, uygulamayla aynı ortamı (`/etc/yatirim/env` - veritabanı yolu) ve
venv'i kullanır:

```bash
sudo bash /opt/yatirim/app/deploy/ai_train.sh start NVDA        # başlat (ek ayar: --lookback 32 --epochs 20 ...)
sudo bash /opt/yatirim/app/deploy/ai_train.sh log NVDA          # ilerlemeyi izle (Ctrl+C yalnızca izlemeyi kapatır)
sudo bash /opt/yatirim/app/deploy/ai_train.sh status NVDA       # çalışıyor mu + kayıtlı modeller
sudo bash /opt/yatirim/app/deploy/ai_train.sh stop NVDA
```

Elle çalıştırırken ortam dosyası yüklenmelidir; yüklenmezse `YATIRIM_DB_PATH` tanımsız kalır ve
komut uygulamanın veritabanını değil repodaki boş dosyayı görür ("kayıtlı veri seti yok"):

```bash
sudo -u yatirim bash -c 'set -a; . /etc/yatirim/env; set +a; cd /opt/yatirim/app && /opt/yatirim/venv/bin/python ai_model.py train --ticker NVDA'
```

### Bellek (1 GB sunucu)

Streamlit ~0,7 GB kullanır; eğitim ayrıca ~0,5-1 GB ister. Toplam RAM 3 GB'ın altındaysa eğitim
otomatik olarak küçük adımlarla yapılır (16 pencere × 8 kanal; `--batch-size` /
`--channels-per-batch` ile değiştirilebilir); 2 GB'ın altındaysa arayüzdeki "Modeli eğit" düğmesi
kapanır ve yukarıdaki betiğin komutu gösterilir (eğitim web uygulamasının içinde çalışırken bellek
dolunca Linux siteyi öldürüyordu). Bellek yine yetmezse Linux önce eğitimi durdurur
(`OOMScoreAdjust=900`). 1 GB sunucuda 2 GB swap eklenmesi önerilir (bir kez):

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -m      # Swap satırında 2047 görünmeli
```

Swap eğitimi yavaşlatır; daha rahat çalışma için sunucuyu 2 GB RAM'e büyütmek gerekir.

Modeller SQLite'taki `ai_models` tablosunda tutulur (ağırlıklar, ayarlar, test sonuçları).

## Günlük kullanım

```bash
sudo /opt/yatirim/bin/yatirim-timers status          # sonraki/önceki tetiklenmeler
journalctl -u 'yatirim-job@*' --since today          # tüm işlerin logları
journalctl -u yatirim-job@trailing-stop -f           # bir işi canlı izle
sudo systemctl start yatirim-job@orb-scan            # bir işi elle çalıştır
```

- **Kod güncellemeleri** için bir şey yapmaya gerek yok: her iş çalışmadan önce
  main'i çeker. `requirements.txt` değişirse paketler de kendiliğinden kurulur.
- **`deploy/` altındaki bir dosya değişirse** (zamanlama, yeni iş) Droplet'te
  `cd /root/yatirim && git pull && sudo bash deploy/install.sh` çalıştırın.
- **Push çakışması:** state push edilemezse commit kaybolmaz. Commit
  `/opt/yatirim/work/<iş>` içinde `unpushed/<zaman>` branch'inde saklanır ve
  Telegram'a bildirim gelir.
- **Geri dönüş:** `sudo /opt/yatirim/bin/yatirim-timers disable`, ardından
  workflow'lardaki `schedule:` bloklarını geri alın.
- **`/etc/yatirim/env` biçimi:** her satır boşluksuz `ANAHTAR=değer` olmalı (tırnak
  gerekmez). Bu dosya `secrets.toml` değildir; TOML satırları (`name = "..."`,
  `[cookie]`) buraya girmemeli. Geçersiz satırlar atlanır, işler çalışmaya devam eder ve
  Telegram'a saatte en fazla bir uyarı gelir. Düzenledikten sonra kontrol:
  `bash -n /etc/yatirim/env && echo geçerli`.

## Actions'tan davranış farkları

- Bir işteki komutlardan biri hata verirse sonrakiler yine çalışır ve o ana kadar
  değişen state commit'lenir. Actions'ta ilk hata işi durdurup commit'i
  atlıyordu. Hata yine de Telegram'a bildirilir.
- `fon-hisse-uyari` artık 4 saat açık kalan bir döngü değil, timer'la 5 dakikada bir
  çalışan tek seferlik bir iş.
- Sunucu kapalıyken kaçırılan tetiklemeler açılışta telafi edilmez
  (`Persistent=false`). Alım/satım işlerinin geç çalışmaması için bu bilinçli.

## Sunucu kaynaklarını Telegram'a gönderme

`install.sh` iki izleme timer'ını da kurup açar (iş timer'larından bağımsızdır,
`yatirim-timers disable` onları kapatmaz). Mesajlar işlerin hata bildirimleriyle
aynı bot ve chat ID'ye gider (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` ya da
uygulamadaki bildirim ayarı).

- **Günlük rapor** (`yatirim-server-report.timer`, her gün 09:00 TRT): CPU, load,
  RAM, swap, disk, en çok RAM kullanan süreçler, arayüz servisinin durumu, açık iş
  timer'ı sayısı ve son 24 saatte hata veren işler.
- **Eşik kontrolü** (`yatirim-server-check.timer`, 5 dk'da bir): bir eşik aşılınca
  uyarı, sorun sürerse 3 saatte bir hatırlatma, düzelince tek bir "düzeldi" mesajı.

| Değişken (`/etc/yatirim/env`) | Varsayılan | Anlamı |
|---|---|---|
| `MONITOR_DISK_PCT` | 85 | Kök disk doluluk % sınırı |
| `MONITOR_MEM_PCT` | 90 | RAM kullanım % sınırı (önbellek hariç) |
| `MONITOR_SWAP_PCT` | 80 | Swap kullanım % sınırı |
| `MONITOR_LOAD_FACTOR` | 2 | 5 dk load > çekirdek sayısı × bu değer |
| `MONITOR_REMIND_MIN` | 180 | Süren sorun için hatırlatma aralığı (dk) |

```bash
sudo systemctl start yatirim-server-report         # raporu şimdi gönder
/opt/yatirim/bin/server_monitor.sh print           # sadece ekrana yaz
systemctl list-timers 'yatirim-server-*'
```

## İsteğe bağlı: Streamlit arayüzünü de Droplet'e taşıma

`deploy/web/` altındaki dosyalar arayüzü Nginx arkasında yayına alır. Önce
`deploy/install.sh` çalıştırılmış olmalı.

1. **(Önerilen) Alan adı:** DNS'te alan adınız için bu Droplet'in IP'sini gösteren
   bir **A kaydı** ekleyin. Alan adı olmadan da çalışır ama bağlantı şifrelenmez,
   giriş şifresi açık metin olarak gider.
2. **Kurulum:**
   ```bash
   cd /root/yatirim && git pull
   sudo bash deploy/web/install_web.sh borsa.ornek.com ben@ornek.com   # alan adıyla
   sudo bash deploy/web/install_web.sh                                  # alan adı olmadan
   ```
   Script Nginx'i (WebSocket başlıklarıyla), Let's Encrypt sertifikasını, güvenlik
   duvarını (sadece 22/80/443) ve 2 GB swap'i kurar. Alan adı verildiyse tarayıcıya
   IP yazanlar da `https://alan-adı` adresine yönlendirilir. Arayüz `/opt/yatirim/app`
   kopyasından çalışır ve bu kopya dakikada bir `main` ile eşitlenir.
3. **Secrets:** Streamlit Cloud → uygulama → *Settings → Secrets* içeriğini aynen
   `/opt/yatirim/.streamlit/secrets.toml` dosyasına yapıştırın, sonra
   `sudo systemctl restart yatirim-streamlit` çalıştırın.
4. Yeni adreste her şey çalışıyorsa Streamlit Cloud'daki uygulamayı kapatabilirsiniz.

**Giriş kullanıcıları (veritabanı):** Kullanıcılar, rolleri ve her kullanıcının
Alpaca anahtarları SQLite veritabanında tutulur (`user_registry.py`, `alpaca_keys.py`);
`secrets.toml`'da kullanıcı ya da `[alpaca.*]` bölümü **yoktur**. Yeni kullanıcılar giriş
ekranındaki *📝 Hesap Oluştur* sekmesinden başvurur. Başvuru, yöneticinin
*👤 Hesap → 🛡️ Kullanıcı Yönetimi* sayfasında onayıyla açılır; yöneticinin ürettiği bir
davet koduyla yapılan başvuru ise beklemeden açılır. Her kullanıcı Sanal Para ve Gerçek
Para anahtarlarını *👤 Hesabım* sayfasında kendisi girer. Anahtarlar kaydedilmeden önce
Alpaca'da denenir ve `/etc/yatirim/env` içindeki `YATIRIM_SECRET_KEY` ile şifrelenir.
Bu değişkeni kaybetmeyin: değişirse kayıtlı anahtarlar çözülemez. Botlar (`JOB_USERNAME`)
anahtarları önce veritabanından, yoksa `APCA_*` değişkenlerinden alır.

Yeni başvuru ve şifre sıfırlama talepleri `secrets.toml`'daki `TELEGRAM_BOT_TOKEN` ile
`TELEGRAM_CHAT_ID`'ye bildirilir. `secrets.toml`'da yalnızca uygulama sırları kalır:
`[cookie]`, `GITHUB_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

Eski düzenden geçiş (bir kerelik, SQLite açıkken):
```bash
cd /root/yatirim && git pull --ff-only origin main
sudo bash deploy/web/users.sh tasi                      # varsayılan yönetici: berkakar
sudo bash deploy/web/users.sh durum
```
`tasi` önce `YATIRIM_SECRET_KEY` yoksa üretip `/etc/yatirim/env`'e ekler. Ardından iki
secrets dosyasındaki kullanıcıları (şifre hash'leriyle) ve `[alpaca.<kullanıcı>]`
anahtarlarını veritabanına yazar, okuyarak doğrular. Doğrulama geçerse bu bölümleri
secrets dosyalarından siler ve arayüzü yeniden başlatır. Silmeden önce `.bak.*` yedeği
alınır; girişleri denedikten sonra bu yedekleri silin, çünkü eski anahtarları içerir.

Sunucudan acil durum komutları (veritabanı yatirim kullanıcısıyla yazılır):
```bash
sudo bash deploy/web/users.sh ekle volkanerdogan "Volkan Erdoğan" volkan@ornek.com [--yonetici]
sudo bash deploy/web/users.sh sifre volkanerdogan --gecici   # ilk girişte değiştirmesi istenir
sudo bash deploy/web/users.sh dene volkanerdogan
sudo bash deploy/web/users.sh onayla volkanerdogan
sudo bash deploy/web/users.sh yonetici volkanerdogan [--kaldir]
sudo bash deploy/web/users.sh sil volkanerdogan [--veri-kalsin]
sudo bash deploy/web/users.sh anahtar                        # YATIRIM_SECRET_KEY yoksa üret
```

Nginx ayarının kritik kısmı (`deploy/web/nginx-yatirim.conf`):
```nginx
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection $connection_upgrade;   # map ile: upgrade / close
proxy_read_timeout 86400s;                         # açık sekme 60 sn'de kopmasın
```
Bu başlıklar olmadan sayfa açılır ama Streamlit'in `/_stcore/stream` WebSocket
bağlantısı kurulamaz ve ekran "Please wait..." durumunda kalır.

Sorun giderme:
```bash
journalctl -u yatirim-streamlit -f        # uygulama logları
sudo nginx -t && sudo systemctl reload nginx
sudo tail -f /var/log/nginx/error.log
```

## SQLite'ı devreye alma (storage.py)

Ortam değişkeni `YATIRIM_DB_PATH` tanımlıysa uygulamanın bütün verisi GitHub ve repodaki
JSON dosyaları yerine Droplet'teki tek bir SQLite veritabanında tutulur:

- **Arayüz ayarları:** seçili hisseler, hisse listeleri ve grupları, ilk sermaye, stop loss ve
  bildirim ayarları, takip edilen fonlar.
- **Strateji config'leri:** `portfolio_config`, `otomatik_alim_satim_config`, `orb_scan_config`,
  `relative_strength_config`, `ha_intraday_config`, `backtest_results`.
- **Strateji state'leri:** ORB / RS / Heikin Ashi holdings, `buy_stop_rebuy_state`,
  `bildirim_durumu`.
- **Önbellekler:** Alpaca bar / gerçekleşmiş K/Z / yönetim önbellekleri, TEFAS, KAP, değerleme,
  DTW ve hisse patern önbellekleri.

Arayüz ve işler aynı kayıtları okuyup yazar. İşler bu dosyaları artık değiştirmediği için
`run_job.sh` commit'leyecek bir şey bulmaz ve GitHub'a state push'u kendiliğinden durur.
Değişken tanımlı değilse eski düzen (GitHub API + JSON dosyaları + push) aynen çalışır.

> **Önemli:** Değişken arayüzde **ve** işlerde aynı anda tanımlı olmalı. İşler
> `/etc/yatirim/env` dosyasını zaten okuyor (`yatirim-job@.service`), ama
> `yatirim-streamlit.service` okumuyor; aşağıdaki 6. adım bunu ekler. Yalnızca bir tarafta
> tanımlıysa arayüz ile işler farklı veri görür.

**Tek komutla** (piyasa kapalıyken, hafta sonu önerilir):

```bash
cd /root/yatirim && git pull --ff-only origin main
sudo bash deploy/enable_sqlite.sh
```

`enable_sqlite.sh` aşağıdaki adımların hepsini sırayla yapar:
- İşlerin çalıştığı saatlerdeyse durur (`--force` ile atlanır).
- Çalışan iş varsa bitmesini bekler.
- Push edilmemiş state varsa hiçbir şeye dokunmadan durur.
- Sonunda arayüzün değişkeni gördüğünü doğrular.
- Bir adım başarısız olursa ayar değişikliklerini geri alır; servisleri ve timer'ları başladığı
  hâline döndürür.

Elle yapmak isterseniz adımlar (root olarak):

1. İşleri ve arayüzü durdurun:
   ```bash
   /opt/yatirim/bin/yatirim-timers disable
   systemctl stop yatirim-app-sync.timer yatirim-streamlit
   ```
2. Push edilemeyip bekleyen state olmadığını kontrol edin. Aşağıdaki komut bir şey
   listelerse durun ve önce o commit'leri main'e alın:
   ```bash
   for d in /opt/yatirim/work/*; do sudo -u yatirim git -C "$d" branch --list 'unpushed/*'; done
   ```
3. Güncel `run_job.sh`'yi kurun (hata bildirimi chat ID'yi veritabanından okusun):
   ```bash
   cd /root/yatirim && git pull --ff-only origin main
   install -m 755 -o root -g root deploy/run_job.sh /opt/yatirim/bin/
   ```
4. Veritabanı klasörünü oluşturun ve main'deki güncel JSON'ları aktarın. Kullanıcı listesi
   `/opt/yatirim/.streamlit/secrets.toml` içinden okunur (kullanıcılar henüz veritabanına
   taşınmadıysa; taşındıysa `--users berkakar,...` verin):
   ```bash
   install -d -o yatirim -g yatirim -m 750 /var/lib/yatirim
   systemctl start yatirim-app-sync.service
   cd /opt/yatirim/app
   sudo -u yatirim -H YATIRIM_DB_PATH=/var/lib/yatirim/yatirim.db /opt/yatirim/venv/bin/python scripts/migrate_json_to_sqlite.py --dry-run
   sudo -u yatirim -H YATIRIM_DB_PATH=/var/lib/yatirim/yatirim.db /opt/yatirim/venv/bin/python scripts/migrate_json_to_sqlite.py --overwrite
   ```
5. Değişkeni ortak ortam dosyasına ekleyin:
   ```bash
   echo 'YATIRIM_DB_PATH=/var/lib/yatirim/yatirim.db' >> /etc/yatirim/env
   ```
6. Arayüzün de bu dosyayı okumasını sağlayın:
   ```bash
   mkdir -p /etc/systemd/system/yatirim-streamlit.service.d
   printf '[Service]\nEnvironmentFile=/etc/yatirim/env\n' > /etc/systemd/system/yatirim-streamlit.service.d/env.conf
   systemctl daemon-reload
   ```
7. Başlatın:
   ```bash
   systemctl start yatirim-app-sync.timer yatirim-streamlit
   /opt/yatirim/bin/yatirim-timers enable
   ```
8. Kontrol:
   ```bash
   apt install -y sqlite3
   sqlite3 /var/lib/yatirim/yatirim.db "SELECT username, name, updated_at FROM settings ORDER BY updated_at DESC LIMIT 10;"
   journalctl -u 'yatirim-job@*' --since "-30 min" | grep -E "Commit'lenecek|push|HATA"
   ```
   Arayüzde bir ayarı kaydedince `updated_at` güncellenmeli. İşlerin logunda
   "Commit'lenecek state değişikliği yok." görünmeli ve GitHub'a yeni state commit'i
   gelmemeli.

### SQLite açıkken dikkat

- **GitHub Actions'taki elle tetiklenen workflow'ları kullanmayın** (`kap_refresh_holdings`,
  `orb_stop_status`, ORB'nin `reconcile_symbols` girişi). Bunlar repodaki JSON'ları okur;
  SQLite açıkken o dosyalar güncel değildir. Gerekirse aynı komutu Droplet'te çalıştırın:
  ```bash
  sudo -u yatirim -H bash -c 'set -a; . /etc/yatirim/env; set +a; cd /opt/yatirim/app && /opt/yatirim/venv/bin/python orb_stop_status.py'
  ```
- **Yedek:** veritabanı tek bir dosya. Çalışırken `cp` ile kopyalamayın, şunu kullanın:
  `sqlite3 /var/lib/yatirim/yatirim.db ".backup /root/yatirim-$(date +%F).db"`.
  DigitalOcean'ın haftalık Droplet yedeklerini de açmanız önerilir.

### Geri dönmek

SQLite açıkken holdings ve state yalnızca veritabanında güncellenir. Doğrudan dönerseniz
işler repodaki eski state ile çalışır. Önce veritabanını JSON'a geri yazın:

1. `/opt/yatirim/bin/yatirim-timers disable` ve `systemctl stop yatirim-streamlit`
2. Veritabanını repoya aktarıp main'e gönderin:
   ```bash
   cd /root/yatirim && git pull --ff-only origin main
   YATIRIM_DB_PATH=/var/lib/yatirim/yatirim.db /opt/yatirim/venv/bin/python scripts/export_sqlite_to_json.py
   git add -A '*.json' && git commit -m "SQLite'tan JSON'a geri dönüş" && git push origin main
   ```
3. `/etc/yatirim/env` dosyasından `YATIRIM_DB_PATH` satırını silin.
4. `systemctl start yatirim-streamlit` ve `/opt/yatirim/bin/yatirim-timers enable`
