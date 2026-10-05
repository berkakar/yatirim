#!/usr/bin/env bash
# Streamlit arayüzünün kopyasını (/opt/yatirim/app) origin/main ile eşitler -
# Streamlit Cloud'un her push'ta yeniden klonlamasının karşılığı. Bir .py
# dosyası ya da requirements.txt değiştiyse (paketler kurulup) servis yeniden
# başlatılır. yatirim-app-sync.timer ile dakikada bir
# root olarak çalışır.
set -euo pipefail

BASE=/opt/yatirim
APP="$BASE/app"
VENV="$BASE/venv"
BRANCH=main
# Kod güncellendi ama Streamlit henüz yeniden başlatılmadıysa bu dosya durur:
# bir adım yarıda kesilirse (pip hatası, zaman aşımı) sonraki çalıştırma
# "HEAD zaten güncel" diye çıkmaz, yeniden başlatmayı tamamlar.
RESTART_PENDING="$BASE/.app_restart_pending"
as_user() { runuser -u yatirim -- env GIT_SSH_COMMAND="ssh -i $BASE/.ssh/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$BASE/.ssh/known_hosts" "$@"; }

cd "$APP"
as_user git fetch --quiet origin "$BRANCH"
# git komutları da yatirim kullanıcısıyla - root olarak çalışsa "dubious ownership" hatası verir.
if [ "$(as_user git rev-parse HEAD)" = "$(as_user git rev-parse "origin/$BRANCH")" ]; then
  [ -f "$RESTART_PENDING" ] || exit 0
  systemctl restart yatirim-streamlit.service
  rm -f "$RESTART_PENDING"
  exit 0
fi

old_head="$(as_user git rev-parse HEAD)"

# Önce fast-forward dene: uygulamanın yerelde yazdığı ama main'de değişmemiş
# dosyalar korunur. Aynı dosya main'de de değiştiyse main'deki sürüm kazanır
# (uygulama kalıcı ayarları zaten GitHub API ile main'e yazıyor).
as_user git merge --quiet --ff-only "origin/$BRANCH" 2>/dev/null \
  || as_user git reset --quiet --hard "origin/$BRANCH"

# Bu betiğin kendisi /opt/yatirim/bin'e kurulu bir kopya - repodaki sürüm
# değiştiyse kendini günceller (sonraki çalıştırmada geçerli olur).
if ! cmp -s deploy/web/app_sync.sh "$BASE/bin/app_sync.sh"; then
  install -m 755 -o root -g root deploy/web/app_sync.sh "$BASE/bin/app_sync.sh"
fi

restart=0
# Streamlit her çalıştırmada sadece app.py'yi yeniden yürütür; içe aktarılan
# modüller (valuation.py, backtest.py, ...) bellekte eski kalabiliyor - app.py
# yeni bir fonksiyonu import edince "cannot import name" hatası verir. Herhangi
# bir .py değiştiyse servis yeniden başlatılır (birkaç saniyelik kesinti).
# (app.py de değişen modülleri kendisi yeniden yükler - bkz.
# _purge_stale_project_modules; bu yeniden başlatma ikinci güvence.)
if [ -n "$(as_user git diff --name-only "$old_head" HEAD -- '*.py')" ]; then
  restart=1
  touch "$RESTART_PENDING"
fi
want_hash="$(sha256sum requirements.txt | cut -d' ' -f1)"
if [ "$(cat "$VENV/.requirements.sha256" 2>/dev/null)" != "$want_hash" ]; then
  # run_job.sh ile aynı kilit ve damga - paylaşılan venv'e aynı anda iki pip yazmasın.
  as_user flock "$BASE/.pip.lock" bash -c \
    "'$VENV/bin/pip' install --quiet --upgrade -r requirements.txt && echo '$want_hash' > '$VENV/.requirements.sha256'"
  restart=1
fi
if [ "$restart" = 1 ]; then
  systemctl restart yatirim-streamlit.service
  rm -f "$RESTART_PENDING"
fi
