#!/usr/bin/env bash
# Geriye uyumluluk: artık deploy/web/users.sh ekle ... kullanın.
#   sudo bash deploy/web/add_user.sh <kullanıcı_adı> "<Ad Soyad>" [e-posta]
exec bash "$(dirname "$(readlink -f "$0")")/users.sh" ekle "$@"
