#!/usr/bin/env bash
# Streamlit arayüzünün kopyasını (/opt/yatirim/app) origin/main ile eşitler -
# Streamlit Cloud'un her push'ta yeniden klonlamasının karşılığı. Değişen .py
# dosyalarını Streamlit kendisi algılar; requirements.txt değiştiyse paketler
# kurulup servis yeniden başlatılır. yatirim-app-sync.timer ile dakikada bir
# root olarak çalışır.
set -euo pipefail

BASE=/opt/yatirim
APP="$BASE/app"
VENV="$BASE/venv"
BRANCH=main
as_user() { runuser -u yatirim -- env GIT_SSH_COMMAND="ssh -i $BASE/.ssh/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$BASE/.ssh/known_hosts" "$@"; }

cd "$APP"
as_user git fetch --quiet origin "$BRANCH"
# git komutları da yatirim kullanıcısıyla - root olarak çalışsa "dubious ownership" hatası verir.
[ "$(as_user git rev-parse HEAD)" = "$(as_user git rev-parse "origin/$BRANCH")" ] && exit 0

# Önce fast-forward dene: uygulamanın yerelde yazdığı ama main'de değişmemiş
# dosyalar korunur. Aynı dosya main'de de değiştiyse main'deki sürüm kazanır
# (uygulama kalıcı ayarları zaten GitHub API ile main'e yazıyor).
as_user git merge --quiet --ff-only "origin/$BRANCH" 2>/dev/null \
  || as_user git reset --quiet --hard "origin/$BRANCH"

want_hash="$(sha256sum requirements.txt | cut -d' ' -f1)"
if [ "$(cat "$VENV/.requirements.sha256" 2>/dev/null)" != "$want_hash" ]; then
  # run_job.sh ile aynı kilit ve damga - paylaşılan venv'e aynı anda iki pip yazmasın.
  as_user flock "$BASE/.pip.lock" bash -c \
    "'$VENV/bin/pip' install --quiet --upgrade -r requirements.txt && echo '$want_hash' > '$VENV/.requirements.sha256'"
  systemctl restart yatirim-streamlit.service
fi
