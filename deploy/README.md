# DigitalOcean Droplet kurulumu

Uygulamayı Streamlit Cloud yerine kendi Droplet'inizde, uykuya geçmeden 7/24 çalıştırmak için.

| Dosya | Görevi |
|---|---|
| `yatirim.service` | systemd servisi: Streamlit'i arka planda çalıştırır, çökerse / sunucu yeniden başlarsa otomatik açar |
| `nginx-yatirim.conf` | Nginx reverse proxy: 80/443 → `127.0.0.1:8501` (WebSocket dahil) |
| `setup.sh` | İlk kurulum (paketler, swap, venv, servis, Nginx, güvenlik duvarı, isteğe bağlı HTTPS) |
| `update.sh` | GitHub'daki son kodu çekip servisi yeniden başlatır |

## 1. Droplet oluşturun

- **İmaj:** Ubuntu 24.04 LTS
- **Boyut:** en az 1 GB RAM (2 GB daha rahat). `setup.sh` 2 GB swap ekler.
- **Kimlik doğrulama:** SSH anahtarı önerilir.

## 2. Repoyu klonlayın

```bash
ssh root@DROPLET_IP
git clone https://github.com/berkakar/yatirim.git /opt/yatirim
```

Repo **private** ise GitHub şifre sormaz, token ister. Sadece bu repoya **Contents: Read-only**
yetkisi olan bir *fine-grained personal access token* oluşturup şöyle klonlayın:

```bash
git clone https://x-access-token:TOKEN@github.com/berkakar/yatirim.git /opt/yatirim
```

(Token `.git/config` içinde saklanır ve `update.sh` bunu kullanır.)

## 3. Gizli ayarları kopyalayın

`.streamlit/secrets.toml` git'te yok (giriş bilgileri, cookie anahtarı, `GITHUB_TOKEN`,
Alpaca anahtarları). Streamlit Cloud'daki **Settings → Secrets** içeriğini veya
bilgisayarınızdaki dosyayı sunucuya aktarın. Kendi bilgisayarınızdan:

```bash
scp .streamlit/secrets.toml root@DROPLET_IP:/opt/yatirim/.streamlit/secrets.toml
```

## 4. Kurulumu çalıştırın

Alan adınız varsa önce DNS'te bir **A kaydı** ile alan adını Droplet IP'sine yönlendirin, sonra:

```bash
sudo bash /opt/yatirim/deploy/setup.sh yatirim.ornek.com siz@ornek.com
```

Alan adı yoksa (sadece `http://DROPLET_IP` ile erişim, HTTPS olmadan):

```bash
sudo bash /opt/yatirim/deploy/setup.sh
```

Alan adını sonradan eklerseniz betiği alan adıyla tekrar çalıştırmanız yeterli.

## Günlük kullanım

```bash
sudo bash /opt/yatirim/deploy/update.sh      # yeni kodu yayınla (main dalı)
systemctl status yatirim                     # çalışıyor mu?
journalctl -u yatirim -f                     # canlı loglar
sudo systemctl restart yatirim               # yeniden başlat
```

`update.sh` hiçbir yerel değişikliği silmez:
- Bir iş o an state dosyası yazıyorsa (commit'lenmemiş değişiklik) 2 dakikaya kadar bekler.
  Hâlâ bitmediyse hiçbir şeye dokunmadan durur.
- İşlerin henüz push edemediği commit'ler varsa onları yeni kodun üzerine taşır. Çakışma
  olursa işlemi geri alıp durur.
- Arayüzü yalnızca kod değiştiyse yeniden başlatır, bağımlılıkları yalnızca
  `requirements.txt` değiştiyse kurar. Zamanlanmış işler bir sonraki çalışmalarında yeni
  kodu kendiliğinden kullanır.
- `deploy/` altındaki servis veya Nginx dosyası değiştiyse bunları otomatik uygulamaz,
  ne yapılacağını yazar.

## Notlar

- Zamanlanmış işler (trailing stop, ORB tarama vb.) Droplet'te systemd servisi olarak
  çalışıyor. `.github/workflows` altındaki eşlerinin zamanlamaları kapatılmalı; aksi halde
  aynı iş iki yerde çalışır (çift emir riski).
- Streamlit sadece `127.0.0.1:8501`'i dinler. Dışarıya yalnızca SSH, 80 ve 443 portları açıktır (`ufw`).
- Let's Encrypt sertifikası `certbot` tarafından otomatik yenilenir.

## SQLite'ı devreye alma (storage.py)

Arayüzün yazdığı ayarlar SQLite veritabanına bağlandı: seçili hisseler, hisse listeleri ve
grupları, ilk sermaye, stop loss ayarları, bildirim ayarları, takip edilen fonlar, KAP portföy
önbelleği ve değerleme önbelleği. Bu ayarları okuyan işler de (trailing stop, alım noktaları,
ORB / RS / Heikin Ashi, fon uyarısı, KAP yenileme, Russell 2000) aynı yerden okuyacak şekilde
bağlandı.

Hepsi **yalnızca `YATIRIM_DB_PATH` tanımlıysa** SQLite kullanır. Tanımlı değilse eski düzen
(GitHub API + JSON dosyaları) aynen çalışır.

> **Önemli:** Değişken arayüz servisinde **ve bütün iş servislerinde** aynı anda tanımlı olmalı.
> Yalnızca arayüzde tanımlıysa, örneğin stop loss ayarlarını arayüzde değiştirdiğinizde
> trailing stop işi eski ayarla çalışmaya devam eder.

Adımlar (piyasa kapalıyken):

1. Kodu güncelleyin: `cd /opt/yatirim && sudo -u yatirim git pull`
2. Arayüzü ve iş timer'larını durdurun:
   `sudo systemctl stop yatirim` ve her iş için `sudo systemctl stop <iş>.timer`.
3. Ortak ortam dosyasını oluşturun:
   ```bash
   sudo install -d -o yatirim -g yatirim -m 750 /var/lib/yatirim
   sudo mkdir -p /etc/yatirim
   echo 'YATIRIM_DB_PATH=/var/lib/yatirim/yatirim.db' | sudo tee /etc/yatirim/yatirim.env
   ```
4. Her iş servisinin `[Service]` bölümüne `yatirim.service`'teki satırın aynısını ekleyin:
   `EnvironmentFile=-/etc/yatirim/yatirim.env`
   (`sudo systemctl edit <iş>.service` ile). Ardından `sudo systemctl daemon-reload`.
   Tüm servisler, `/var/lib/yatirim` klasörüne **yazabilen** aynı kullanıcıyla çalışmalı.
   SQLite sadece okuyan süreçlerin de bu klasöre yazabilmesini ister.
5. Güncel JSON'ları veritabanına aktarın (daha önce denediyseniz `--overwrite` ile güncellenir):
   ```bash
   cd /opt/yatirim
   sudo -u yatirim YATIRIM_DB_PATH=/var/lib/yatirim/yatirim.db venv/bin/python scripts/migrate_json_to_sqlite.py --dry-run
   sudo -u yatirim YATIRIM_DB_PATH=/var/lib/yatirim/yatirim.db venv/bin/python scripts/migrate_json_to_sqlite.py --overwrite
   ```
   Kullanıcı listesi `.streamlit/secrets.toml` içinden okunur.
6. Servisleri başlatın: `sudo systemctl start yatirim` ve iş timer'ları.
7. Kontrol: arayüzde bir ayarı değiştirip kaydedin, sonra
   `sqlite3 /var/lib/yatirim/yatirim.db "SELECT username, name, updated_at FROM settings ORDER BY updated_at DESC LIMIT 5;"`
   (`sudo apt install sqlite3`). Bu ayarlar artık GitHub'a commit'lenmez.

**Geri dönmek için** `/etc/yatirim/yatirim.env` dosyasından satırı silip servisleri yeniden başlatın.
SQLite'tayken yapılan ayar değişiklikleri JSON dosyalarına geri yazılmaz.

Strateji state ve config dosyaları (`*_holdings`, `orb_scan_config`, `portfolio_config` vb.) ve
fiyat önbellekleri henüz JSON + git ile çalışıyor; bunlar sonraki adımda taşınacak.
