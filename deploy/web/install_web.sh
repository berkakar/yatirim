#!/usr/bin/env bash
# Streamlit arayüzünü Droplet'te Nginx arkasında yayına alır (Ubuntu 24.04, root).
# Önce deploy/install.sh çalıştırılmış olmalı (kullanıcı, venv, deploy key).
#
#   sudo bash deploy/web/install_web.sh                         # alan adı yok: http://<IP>
#   sudo bash deploy/web/install_web.sh borsa.ornek.com         # + Let's Encrypt HTTPS
#   sudo bash deploy/web/install_web.sh borsa.ornek.com ben@ornek.com
#
# Tekrar tekrar çalıştırılabilir.
set -euo pipefail

BASE=/opt/yatirim
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_URL=git@github.com:berkakar/yatirim.git
DOMAIN="${1:-}"
EMAIL="${2:-}"
cd /   # yatirim kullanıcısının erişemediği /root altında komut çalıştırmamak için

if [ "$(id -u)" -ne 0 ]; then
  echo "root olarak çalıştırın: sudo bash $0 [alan-adı] [e-posta]" >&2
  exit 1
fi
if [ ! -x "$BASE/venv/bin/python" ] || [ ! -f "$BASE/.ssh/deploy_key" ]; then
  echo "Önce deploy/install.sh çalıştırılmalı." >&2
  exit 1
fi

as_user() { runuser -u yatirim -- env GIT_SSH_COMMAND="ssh -i $BASE/.ssh/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$BASE/.ssh/known_hosts" "$@"; }

echo "==> Paketler (nginx, certbot, ufw)"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx certbot python3-certbot-nginx ufw >/dev/null

echo "==> Swap"
# 1 GB RAM'de Streamlit + alım/satım işleri aynı anda çalışınca bellek yetmeyebilir.
if ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "    2 GB swap eklendi."
fi

echo "==> Uygulama kopyası ($BASE/app)"
if [ ! -d "$BASE/app/.git" ]; then
  as_user git clone --quiet --branch main "$REPO_URL" "$BASE/app"
fi
want_hash="$(sha256sum "$BASE/app/requirements.txt" | cut -d' ' -f1)"
if [ "$(cat "$BASE/venv/.requirements.sha256" 2>/dev/null)" != "$want_hash" ]; then
  echo "    Python paketleri kuruluyor..."
  as_user flock "$BASE/.pip.lock" bash -c \
    "'$BASE/venv/bin/pip' install --quiet --upgrade -r '$BASE/app/requirements.txt' && echo '$want_hash' > '$BASE/venv/.requirements.sha256'"
fi

echo "==> Streamlit secrets"
# Streamlit ~/.streamlit/secrets.toml'u da okur; yatirim kullanıcısının HOME'u $BASE.
# Repo kopyasının dışında tutuluyor ki git reset/clean ona hiç dokunmasın.
install -d -m 700 -o yatirim -g yatirim "$BASE/.streamlit"
SECRETS="$BASE/.streamlit/secrets.toml"
if [ ! -f "$SECRETS" ]; then
  cat > "$SECRETS" <<'EOF'
# Streamlit Cloud → uygulamanız → Settings → Secrets içeriğinin AYNISINI buraya yapıştırın.
# ([cookie], GITHUB_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID). Kullanıcılar ve Alpaca
# anahtarları veritabanında - eski [credentials]/[alpaca] için: users.sh tasi
EOF
  echo "    Şablon oluşturuldu: $SECRETS (doldurulması gerekiyor)"
fi
chown yatirim:yatirim "$SECRETS"
chmod 600 "$SECRETS"

echo "==> systemd birimleri"
install -m 755 -o root -g root "$SRC/app_sync.sh" "$BASE/bin/"
install -m 644 -o root -g root "$SRC/yatirim-streamlit.service" "$SRC/yatirim-app-sync.service" \
  "$SRC/yatirim-app-sync.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now yatirim-app-sync.timer >/dev/null
systemctl enable yatirim-streamlit.service >/dev/null
systemctl restart yatirim-streamlit.service

echo "==> Nginx"
sed "s/__SERVER_NAME__/${DOMAIN:-_}/" "$SRC/nginx-yatirim.conf" > /etc/nginx/sites-available/yatirim
ln -sf /etc/nginx/sites-available/yatirim /etc/nginx/sites-enabled/yatirim
rm -f /etc/nginx/sites-enabled/default
nginx -t
# Nginx hiç çalışmıyorsa (örn. daha önce durdurulmuş ya da başlatılamamış) reload
# başarısız olur - gerekirse başlat, çalışıyorsa sadece ayarı yeniden yükle.
systemctl enable nginx >/dev/null 2>&1
if ! systemctl reload-or-restart nginx; then
  echo "!!! Nginx başlatılamadı. Sebebi için: journalctl -u nginx -n 30 --no-pager" >&2
  echo "    80/443 portunu başka bir program kullanıyor olabilir: ss -ltnp | grep -E ':(80|443) '" >&2
  exit 1
fi

echo "==> Güvenlik duvarı (ufw): sadece SSH, 80, 443"
ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null

if [ -n "$DOMAIN" ]; then
  echo "==> HTTPS sertifikası ($DOMAIN)"
  if [ -n "$EMAIL" ]; then email_args=(-m "$EMAIL"); else email_args=(--register-unsafely-without-email); fi
  if ! certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect "${email_args[@]}"; then
    echo "!!! Sertifika alınamadı. $DOMAIN için DNS'te bu Droplet'in IP'sini gösteren bir A kaydı"
    echo "    olduğundan emin olun (yayılması birkaç dakika sürebilir), sonra script'i tekrar çalıştırın."
  fi
fi

IP_REDIRECT=/etc/nginx/sites-available/yatirim-ip-redirect
if [ -n "$DOMAIN" ]; then
  echo "==> IP ile gelenleri $DOMAIN adresine yönlendirme"
  if grep -q "listen 443" /etc/nginx/sites-available/yatirim; then target="https://$DOMAIN"; else target="http://$DOMAIN"; fi
  sed "s|__TARGET__|$target|" "$SRC/nginx-ip-redirect.conf" > "$IP_REDIRECT"
  ln -sf "$IP_REDIRECT" /etc/nginx/sites-enabled/yatirim-ip-redirect
else
  rm -f /etc/nginx/sites-enabled/yatirim-ip-redirect "$IP_REDIRECT"
fi
nginx -t
systemctl reload nginx

IP="$(curl -fsS -4 -m 5 https://ifconfig.me 2>/dev/null || hostname -I | cut -d' ' -f1)"
if [ -n "$DOMAIN" ] && grep -q "listen 443" /etc/nginx/sites-available/yatirim; then
  URL="https://$DOMAIN"
elif [ -n "$DOMAIN" ]; then
  URL="http://$DOMAIN  (HTTPS henüz YOK - sertifika alınamadı; şimdilik: http://$IP)"
else
  URL="http://$IP  (alan adı olmadan şifreli DEĞİL)"
fi
cat <<EOF

Kurulum tamam.
  1. Secrets'ı doldurun:      sudo nano $SECRETS
     Sonra:                   sudo systemctl restart yatirim-streamlit
  2. Adres:                   $URL
  3. Loglar:                  journalctl -u yatirim-streamlit -f
EOF
