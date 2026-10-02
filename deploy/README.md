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

`update.sh`, izlenen dosyalardaki yerel değişiklikleri siler ve repodaki son hâli esas alır
(Streamlit Cloud da her deploy'da temiz klon kullanır). `secrets.toml` ve `venv/` korunur.

## Notlar

- `.github/workflows` altındaki zamanlanmış işler (trailing stop, ORB tarama vb.) GitHub
  Actions'ta çalışmaya devam eder. Droplet yalnızca arayüzü barındırır.
- Streamlit sadece `127.0.0.1:8501`'i dinler. Dışarıya yalnızca SSH, 80 ve 443 portları açıktır (`ufw`).
- Let's Encrypt sertifikası `certbot` tarafından otomatik yenilenir.

## SQLite veritabanı (storage.py)

Uygulama verisi JSON dosyaları yerine `/var/lib/yatirim/yatirim.db` dosyasına taşınıyor
(yol `yatirim.service` içindeki `YATIRIM_DB_PATH` ile belirlenir). Mevcut JSON'ları
bir kez aktarmak için, repo klasöründe:

```bash
cd /opt/yatirim
sudo -u yatirim YATIRIM_DB_PATH=/var/lib/yatirim/yatirim.db venv/bin/python scripts/migrate_json_to_sqlite.py --dry-run   # önce dene
sudo -u yatirim YATIRIM_DB_PATH=/var/lib/yatirim/yatirim.db venv/bin/python scripts/migrate_json_to_sqlite.py
```

Kullanıcı listesi `.streamlit/secrets.toml` içinden okunur. Betik kaynak JSON'lara dokunmaz,
veritabanında olan kayıtları atlar (`--overwrite` ile üzerine yazar); tekrar çalıştırmak güvenlidir.

Veritabanının içine bakmak için: `sudo apt install sqlite3`, sonra
`sqlite3 /var/lib/yatirim/yatirim.db "SELECT username, name, updated_at FROM settings;"`.
