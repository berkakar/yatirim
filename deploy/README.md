# Zamanlanmış işleri DigitalOcean Droplet'e taşıma

GitHub Actions'ın `schedule` tetikleyicisi dakikasında çalışmıyor (5-30 dk gecikme,
bazen hiç çalışmama). Bu klasör, aynı işleri bir Droplet'te **systemd timer'ları** ile
tam saniyesinde çalıştırır. Streamlit uygulaması Streamlit Cloud'da kalır ve hiçbir
değişiklik gerektirmez, çünkü durum (state) dosyaları eskisi gibi repoya commit'lenir.

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
| `relative-strength` | relative_strength.yml | Pazartesi 03:15 ET |
| `tefas` | tefas_fonlari.yml | Hafta içi 09:00, 13:00, 19:10 TRT |
| `fon-hisse-uyari` | fon_hisse_uyari.yml | Hafta içi 10:00–17:55 TRT, 5 dk'da bir |
| `russell2000` | update_russell2000.yml | Her ayın 1'i 06:00 UTC |

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
   Bu değişiklik henüz main'e alınmadıysa `deploy/` klasörü main'de yoktur. O durumda
   klonlarken `-b ccr-8b6d1873-uh480v` ekleyin. İşlerin kendisi her zaman main'deki
   kodla çalışır.
   İlk çalıştırmada bir **deploy key** yazdırılıp durulur. Bu anahtarı GitHub'da
   *Settings → Deploy keys → Add deploy key* ekranına yapıştırın,
   **"Allow write access"** kutusunu işaretleyin ve script'i tekrar çalıştırın.
   (`--with-playwright` sadece Russell 2000 işi için gerekli.)
3. **Secret'ları girin:** `sudo nano /etc/yatirim/env`. Değerler GitHub'daki
   `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY` ve `TELEGRAM_BOT_TOKEN` secret'larıyla aynı.
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

## Actions'tan davranış farkları

- Bir işteki komutlardan biri hata verirse sonrakiler yine çalışır ve o ana kadar
  değişen state commit'lenir. Actions'ta ilk hata işi durdurup commit'i
  atlıyordu. Hata yine de Telegram'a bildirilir.
- `fon-hisse-uyari` artık 4 saat açık kalan bir döngü değil, timer'la 5 dakikada bir
  çalışan tek seferlik bir iş.
- Sunucu kapalıyken kaçırılan tetiklemeler açılışta telafi edilmez
  (`Persistent=false`). Alım/satım işlerinin geç çalışmaması için bu bilinçli.

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
   duvarını (sadece 22/80/443) ve 2 GB swap'i kurar. Arayüz `/opt/yatirim/app`
   kopyasından çalışır ve bu kopya dakikada bir `main` ile eşitlenir.
3. **Secrets:** Streamlit Cloud → uygulama → *Settings → Secrets* içeriğini aynen
   `/opt/yatirim/.streamlit/secrets.toml` dosyasına yapıştırın, sonra
   `sudo systemctl restart yatirim-streamlit` çalıştırın.
4. Yeni adreste her şey çalışıyorsa Streamlit Cloud'daki uygulamayı kapatabilirsiniz.

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
