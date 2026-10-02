#!/usr/bin/env bash
# Ubuntu 22.04/24.04 Droplet'e ilk kurulum. Repo /opt/yatirim'e klonlandıktan
# sonra root olarak çalıştırın:
#   sudo bash /opt/yatirim/deploy/setup.sh                      # sadece HTTP (IP ile erişim)
#   sudo bash /opt/yatirim/deploy/setup.sh alan.adi.com eposta  # HTTPS dahil
# Tekrar çalıştırmak güvenlidir.
set -euo pipefail

APP_DIR=/opt/yatirim
APP_USER=yatirim
DOMAIN="${1:-}"
EMAIL="${2:-}"

if [[ $EUID -ne 0 ]]; then
    echo "Bu betik root olarak çalıştırılmalı (sudo bash ...)." >&2
    exit 1
fi
if [[ ! -f "$APP_DIR/app.py" ]]; then
    echo "$APP_DIR/app.py bulunamadı. Önce repoyu $APP_DIR'e klonlayın." >&2
    exit 1
fi

echo "==> Paketler kuruluyor"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip git nginx ufw

echo "==> Swap kontrol ediliyor"
# Küçük Droplet'lerde pandas/yfinance belleği aşabilir; 2 GB swap ekle.
if ! swapon --show | grep -q .; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Uygulama kullanıcısı: $APP_USER"
id "$APP_USER" &>/dev/null || useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
# root'un bu dizinde git çalıştırabilmesi için (update.sh).
git config --system --add safe.directory "$APP_DIR" 2>/dev/null || true

# storage.py'nin SQLite veritabanı için klasör (bkz. yatirim.service YATIRIM_DB_PATH).
install -d -o "$APP_USER" -g "$APP_USER" -m 750 /var/lib/yatirim

echo "==> Python sanal ortamı ve bağımlılıklar"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

SECRETS="$APP_DIR/.streamlit/secrets.toml"
if [[ -f "$SECRETS" ]]; then
    chown "$APP_USER:$APP_USER" "$SECRETS"
    chmod 600 "$SECRETS"
else
    echo "UYARI: $SECRETS yok. Uygulama giriş ekranında hata verir; bkz. deploy/README.md." >&2
fi

echo "==> systemd servisi"
cp "$APP_DIR/deploy/yatirim.service" /etc/systemd/system/yatirim.service
systemctl daemon-reload
systemctl enable yatirim
systemctl restart yatirim

echo "==> Nginx"
cp "$APP_DIR/deploy/nginx-yatirim.conf" /etc/nginx/sites-available/yatirim
if [[ -n "$DOMAIN" ]]; then
    sed -i "s/server_name _;/server_name $DOMAIN;/" /etc/nginx/sites-available/yatirim
fi
ln -sf /etc/nginx/sites-available/yatirim /etc/nginx/sites-enabled/yatirim
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "==> Güvenlik duvarı (sadece SSH, 80, 443)"
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

if [[ -n "$DOMAIN" ]]; then
    echo "==> HTTPS sertifikası (Let's Encrypt)"
    DEBIAN_FRONTEND=noninteractive apt-get install -y certbot python3-certbot-nginx
    if [[ -n "$EMAIL" ]]; then
        certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect
    else
        certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect
    fi
fi

echo
echo "Kurulum tamam."
if [[ -n "$DOMAIN" ]]; then
    echo "Adres: https://$DOMAIN"
else
    echo "Adres: http://$(curl -s -4 ifconfig.me || hostname -I | awk '{print $1}')"
fi
echo "Durum: systemctl status yatirim   |   Loglar: journalctl -u yatirim -f"
