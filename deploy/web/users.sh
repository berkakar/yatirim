#!/usr/bin/env bash
# Arayüz giriş kullanıcılarını yönetir. Ayrıntılar: deploy/web/manage_users.py
#
#   sudo bash deploy/web/users.sh durum                       # sorunları göster
#   sudo bash deploy/web/users.sh duzelt                      # listeleri tek dosyada topla
#   sudo bash deploy/web/users.sh ekle <kullanıcı> "<Ad Soyad>" [e-posta]
#   sudo bash deploy/web/users.sh sifre <kullanıcı>           # şifre değiştir
#   sudo bash deploy/web/users.sh dene <kullanıcı>            # şifre doğru mu
#   sudo bash deploy/web/users.sh sil <kullanıcı>
set -euo pipefail

BASE="${BASE:-/opt/yatirim}"
export BASE
PY="${PY:-$BASE/venv/bin/python}"
export PY

[ "$(id -u)" -eq 0 ] || { echo "Hata: sudo ile çalıştırın." >&2; exit 1; }
[ -x "$PY" ] || { echo "Hata: Python bulunamadı: $PY" >&2; exit 1; }
if [ $# -eq 0 ]; then
  sed -n '4,9p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi
exec "$PY" "$(dirname "$(readlink -f "$0")")/manage_users.py" "$@"
