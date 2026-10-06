#!/usr/bin/env bash
# Arayüz giriş kullanıcılarını yönetir (veritabanı). Ayrıntılar: deploy/web/manage_users.py
# Günlük yönetim arayüzde: 👤 Hesap → 🛡️ Kullanıcı Yönetimi.
#
#   sudo bash deploy/web/users.sh tasi [--yonetici <k>]       # secrets.toml'dan veritabanına (bir kerelik)
#   sudo bash deploy/web/users.sh durum                       # kullanıcılar ve sorunlar
#   sudo bash deploy/web/users.sh ekle <k> "<Ad Soyad>" [e-posta] [--yonetici]
#   sudo bash deploy/web/users.sh sifre <k> [--gecici]        # şifre ata
#   sudo bash deploy/web/users.sh dene <k>                    # şifre doğru mu
#   sudo bash deploy/web/users.sh onayla <k>                  # bekleyen başvuruyu onayla
#   sudo bash deploy/web/users.sh yonetici <k> [--kaldir]
#   sudo bash deploy/web/users.sh sil <k> [--veri-kalsin]
#   sudo bash deploy/web/users.sh anahtar                     # YATIRIM_SECRET_KEY yoksa üret
set -euo pipefail

BASE="${BASE:-/opt/yatirim}"
APP="${APP:-$BASE/app}"
PY="${PY:-$BASE/venv/bin/python}"
ENV_FILE="${YATIRIM_ENV_FILE:-/etc/yatirim/env}"
APP_USER="${APP_USER:-yatirim}"
SERVICE="${SERVICE:-yatirim-streamlit}"
export BASE

[ "$(id -u)" -eq 0 ] || { echo "Hata: sudo ile çalıştırın." >&2; exit 1; }
[ -x "$PY" ] || { echo "Hata: Python bulunamadı: $PY" >&2; exit 1; }
[ -f "$APP/user_registry.py" ] || { echo "Hata: $APP güncel değil (user_registry.py yok); main ile eşitlenmesini bekleyin." >&2; exit 1; }
if [ $# -eq 0 ]; then
  sed -n '4,14p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi

# env dosyasındaki tek bir değer - run_job.sh gibi kabuk komutu olarak source EDİLMEZ.
env_value() {
  [ -r "$ENV_FILE" ] || return 0
  sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

ensure_secret_key() {
  if [ -n "$(env_value YATIRIM_SECRET_KEY)" ]; then
    return 1
  fi
  local key
  key="$("$PY" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
  printf '# Alpaca anahtarlarının veritabanında şifrelenmesi (alpaca_keys.py). KAYBETMEYİN:\n# değişirse kayıtlı anahtarlar çözülemez, kullanıcılar yeniden girer.\nYATIRIM_SECRET_KEY=%s\n' "$key" >> "$ENV_FILE"
  echo "YATIRIM_SECRET_KEY üretildi ve $ENV_FILE dosyasına eklendi."
  return 0
}

restart_ui() {
  systemctl restart "$SERVICE" && echo "$SERVICE yeniden başlatıldı." \
    || echo "UYARI: $SERVICE yeniden başlatılamadı." >&2
}

# Komut, baştaki seçeneklerden (-y, --yes) sonraki ilk argüman.
cmd=""
for a in "$@"; do
  case "$a" in -*) ;; *) cmd="$a"; break ;; esac
done
if [ "$cmd" = "anahtar" ]; then
  if ensure_secret_key; then restart_ui; else echo "YATIRIM_SECRET_KEY zaten tanımlı."; fi
  exit 0
fi
key_added=0
if [ "$cmd" = "tasi" ] && ensure_secret_key; then key_added=1; fi

db_path="$(env_value YATIRIM_DB_PATH)"
[ -n "$db_path" ] || { echo "Hata: $ENV_FILE içinde YATIRIM_DB_PATH yok - önce SQLite'ı açın (deploy/enable_sqlite.sh)." >&2; exit 1; }

set +e
(cd "$APP" && runuser -u "$APP_USER" -- env HOME="$BASE" BASE="$BASE" APP_DIR="$APP" \
  YATIRIM_DB_PATH="$db_path" YATIRIM_SECRET_KEY="$(env_value YATIRIM_SECRET_KEY)" \
  "$PY" "$APP/deploy/web/manage_users.py" "$@")
rc=$?
set -e
# tasi secrets.toml'u değiştirir; yeni anahtar da ancak yeniden başlatınca arayüze geçer.
if [ "$cmd" = "tasi" ] && { [ $rc -eq 0 ] || [ $key_added -eq 1 ]; }; then restart_ui; fi
exit $rc
