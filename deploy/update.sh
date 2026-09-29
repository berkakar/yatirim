#!/usr/bin/env bash
# GitHub'daki son kodu çeker, bağımlılıkları günceller ve servisi yeniden başlatır.
#   sudo bash /opt/yatirim/deploy/update.sh
set -euo pipefail

APP_DIR=/opt/yatirim
APP_USER=yatirim
BRANCH="${1:-main}"

cd "$APP_DIR"
# Uygulama repodaki bazı JSON önbelleklerine yerelde yazdığı için düz "git pull"
# çakışabilir. Streamlit Cloud gibi her güncellemede repodaki son hâli esas alınır:
# izlenen dosyalardaki yerel değişiklikler atılır. İzlenmeyen dosyalar
# (.streamlit/secrets.toml, venv/) korunur.
sudo -u "$APP_USER" git fetch origin "$BRANCH"
sudo -u "$APP_USER" git checkout -B "$BRANCH" "origin/$BRANCH"
sudo -u "$APP_USER" git reset --hard "origin/$BRANCH"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -r requirements.txt
systemctl restart yatirim
systemctl --no-pager --lines=5 status yatirim
