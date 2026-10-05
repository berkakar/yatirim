#!/usr/bin/env bash
# Droplet kurulum scripti (Ubuntu 24.04, root olarak çalıştırılır). Tekrar
# tekrar çalıştırılabilir; deploy/ altındaki bir dosya değiştiğinde de aynı
# şekilde yeniden çalıştırılarak güncellenir.
#
#   sudo bash deploy/install.sh                    # temel kurulum
#   sudo bash deploy/install.sh --with-playwright  # + Russell 2000 işi için Chromium
#
# Timer'ları AÇMAZ - GitHub Actions'taki schedule'lar kapatılmadan ikisinin
# aynı anda çalışmaması için bu bilinçli olarak ayrı bir adım:
#   sudo /opt/yatirim/bin/yatirim-timers enable
set -euo pipefail

BASE=/opt/yatirim
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_URL=git@github.com:berkakar/yatirim.git
WITH_PLAYWRIGHT=0
[ "${1:-}" = "--with-playwright" ] && WITH_PLAYWRIGHT=1

# Aşağıdaki `sudo -u yatirim ...` komutları, script'in çalıştırıldığı klasörde
# (örn. /root/yatirim) başlar; yatirim kullanıcısı /root'a erişemediği için git
# orada "Permission denied" ile düşer. Erişilebilir bir klasöre geç.
cd /

if [ "$(id -u)" -ne 0 ]; then
  echo "root olarak çalıştırın: sudo bash $0" >&2
  exit 1
fi

echo "==> Paketler"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3 python3-venv python3-pip curl ca-certificates >/dev/null

echo "==> Saat senkronu (NTP)"
timedatectl set-ntp true || true

echo "==> Kullanıcı ve dizinler"
id yatirim >/dev/null 2>&1 || useradd --system --home-dir "$BASE" --shell /usr/sbin/nologin yatirim
install -d -o yatirim -g yatirim "$BASE" "$BASE/bin" "$BASE/work" "$BASE/.ssh"
chmod 700 "$BASE/.ssh"
install -d -m 750 -o root -g yatirim /etc/yatirim

echo "==> Script'ler ve systemd birimleri"
install -m 755 -o root -g root "$SRC/run_job.sh" "$SRC/yatirim-timers" "$SRC/server_monitor.sh" "$BASE/bin/"
install -m 644 -o root -g root "$SRC/jobs.sh" "$BASE/bin/"
install -m 644 -o root -g root "$SRC"/systemd/yatirim-job@.service "$SRC"/systemd/yatirim-server-*.service \
  "$SRC"/systemd/yatirim-*.timer /etc/systemd/system/
systemctl daemon-reload

if [ ! -f /etc/yatirim/env ]; then
  echo "==> /etc/yatirim/env şablonu oluşturuluyor"
  cat > /etc/yatirim/env <<'EOF'
# GitHub Actions secret'larının karşılığı. Değerleri doldurun.
APCA_API_KEY_ID=
APCA_API_SECRET_KEY=
# Gerçek Para hesabının anahtarları - yalnızca arayüzde hesap türü "Gerçek Para"
# seçildiğinde kullanılır (hesap türünü bu dosya değil, arayüzdeki ayar belirler).
APCA_LIVE_API_KEY_ID=
APCA_LIVE_API_SECRET_KEY=
APCA_API_BASE_URL=https://paper-api.alpaca.markets/v2
APCA_API_DATA_URL=https://data.alpaca.markets/v2
TELEGRAM_BOT_TOKEN=
# Boş bırakılırsa bildirim_ayarlari_berkakar.json'daki telegram_chat_id kullanılır.
TELEGRAM_CHAT_ID=
EOF
fi
chown root:yatirim /etc/yatirim/env
chmod 640 /etc/yatirim/env

echo "==> Sunucu izleme (Telegram'a kaynak raporu ve eşik uyarıları)"
# İş timer'larından farklı olarak hemen açılır: alım/satım yapmaz, Actions ile çakışmaz.
systemctl enable --now yatirim-server-report.timer yatirim-server-check.timer >/dev/null

echo "==> Python venv"
[ -x "$BASE/venv/bin/python" ] || sudo -u yatirim python3 -m venv "$BASE/venv"
sudo -u yatirim "$BASE/venv/bin/pip" install --quiet --upgrade pip

if [ "$WITH_PLAYWRIGHT" -eq 1 ]; then
  echo "==> Playwright + Chromium (Russell 2000 işi için)"
  sudo -u yatirim "$BASE/venv/bin/pip" install --quiet playwright
  "$BASE/venv/bin/python" -m playwright install-deps chromium >/dev/null
  sudo -u yatirim HOME="$BASE" "$BASE/venv/bin/python" -m playwright install chromium
fi

echo "==> GitHub deploy key"
if [ ! -f "$BASE/.ssh/deploy_key" ]; then
  sudo -u yatirim ssh-keygen -q -t ed25519 -N "" -C "yatirim-droplet" -f "$BASE/.ssh/deploy_key"
fi
if ! grep -q "^github.com " "$BASE/.ssh/known_hosts" 2>/dev/null; then
  ssh-keyscan -t ed25519,rsa github.com 2>/dev/null >> "$BASE/.ssh/known_hosts"
  chown yatirim:yatirim "$BASE/.ssh/known_hosts"
fi

if ! check_out="$(sudo -u yatirim GIT_SSH_COMMAND="ssh -i $BASE/.ssh/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$BASE/.ssh/known_hosts" \
    git ls-remote "$REPO_URL" HEAD 2>&1)"; then
  cat <<EOF

!!! GitHub'a deploy key ile bağlanılamadı. Hata:
$check_out

    "Permission denied (publickey)" ise anahtar GitHub'a eklenmemiş ya da farklı. Aşağıdaki anahtarı
    github.com/berkakar/yatirim → Settings → Deploy keys → Add deploy key
    ekranına yapıştırın ve "Allow write access" kutusunu İŞARETLEYİN:

$(cat "$BASE/.ssh/deploy_key.pub")

    Sonra bu script'i tekrar çalıştırın.
EOF
  exit 1
fi

echo "==> Isınma: ilk klon + bağımlılık kurulumu (tefas işi, Alpaca'ya dokunmaz)"
if ! grep -q '^APCA_API_KEY_ID=.\+' /etc/yatirim/env; then
  echo "    (Uyarı: /etc/yatirim/env içindeki Alpaca anahtarları boş - timer'ları açmadan önce doldurun.)"
fi
systemctl start yatirim-job@tefas.service || true
systemctl --no-pager --lines=15 status yatirim-job@tefas.service || true

cat <<EOF

Kurulum tamam. Sıradaki adımlar (bkz. deploy/README.md):
  1. /etc/yatirim/env dosyasını doldurun:   sudo nano /etc/yatirim/env
  2. Bir işi elle deneyin:                  sudo systemctl start yatirim-job@tefas
                                            journalctl -u yatirim-job@tefas -n 50
  3. GitHub Actions'taki schedule'ları kapatan değişikliği main'e alın.
  4. Timer'ları açın:                       sudo $BASE/bin/yatirim-timers enable
EOF
