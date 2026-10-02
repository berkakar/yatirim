#!/usr/bin/env bash
# GitHub'daki son kodu Droplet'e alır.
#   sudo bash /root/yatirim/deploy/update.sh          # main dalı
#   sudo bash /root/yatirim/deploy/update.sh <dal>
#   sudo SERVICE=<arayüz-servisi> bash /root/yatirim/deploy/update.sh   # servis adı "yatirim" değilse
#
# Zamanlanmış işler de bu klasörde çalışıp state/önbellek JSON'larını yazıyor,
# commit'leyip push ediyor. Bu yüzden betik hiçbir yerel değişikliği SİLMEZ:
# - İzlenen dosyalarda commit'lenmemiş değişiklik varsa (bir iş o an yazıyordur)
#   WAIT_SECONDS boyunca bekler; hâlâ varsa hiçbir şeye dokunmadan durur.
# - İşlerin henüz push edemediği yerel commit'ler varsa onları yeni kodun üzerine
#   taşır (rebase); çakışırsa işlemi geri alıp durur.
# - Arayüzü yalnızca kod değiştiyse yeniden başlatır; requirements.txt değiştiyse
#   bağımlılıkları kurar. Zamanlanmış işler her çalıştırmada kodu baştan okuduğu
#   için onlara ayrıca bir şey yapmak gerekmez.
set -euo pipefail

# Droplet'teki asıl kurulum: repo /root/yatirim'de, işler ve arayüz root olarak çalışıyor.
APP_DIR="${APP_DIR:-/root/yatirim}"
APP_USER="${APP_USER:-root}"
SERVICE="${SERVICE:-yatirim}"
WAIT_SECONDS="${WAIT_SECONDS:-120}"
BRANCH="${1:-main}"

as_app() {
    if [[ "$(id -un)" == "$APP_USER" ]]; then
        "$@"
    else
        sudo -u "$APP_USER" "$@"
    fi
}

cd "$APP_DIR"

current_branch="$(as_app git rev-parse --abbrev-ref HEAD)"
if [[ "$current_branch" != "$BRANCH" ]]; then
    echo "HATA: $APP_DIR '$current_branch' dalında, '$BRANCH' değil. Dal değiştirmek için elle müdahale edin." >&2
    exit 1
fi

# 1) Commit'lenmemiş değişiklik yokken devam et.
waited=0
while [[ -n "$(as_app git status --porcelain --untracked-files=no)" ]]; do
    if (( waited >= WAIT_SECONDS )); then
        echo "HATA: İzlenen dosyalarda $WAIT_SECONDS sn'dir commit'lenmemiş değişiklik var:" >&2
        as_app git status --short --untracked-files=no >&2
        echo "Bir iş takılmış olabilir. Hiçbir şey değiştirilmedi; işleri kontrol edip tekrar deneyin." >&2
        exit 1
    fi
    if (( waited == 0 )); then
        echo "==> Commit'lenmemiş değişiklik var (bir iş çalışıyor olabilir), bekleniyor..."
    fi
    sleep 5
    waited=$((waited + 5))
done

# 2) Yeni kodu al; push edilmemiş yerel commit'leri koru.
as_app git fetch origin "$BRANCH"
old_head="$(as_app git rev-parse HEAD)"
if ! as_app git rebase "origin/$BRANCH"; then
    as_app git rebase --abort || true
    echo "HATA: Push edilmemiş yerel commit'ler yeni kodla çakıştı. Hiçbir şey değiştirilmedi." >&2
    echo "Yerel commit'ler: git -C $APP_DIR log --oneline origin/$BRANCH..HEAD" >&2
    exit 1
fi
new_head="$(as_app git rev-parse HEAD)"

if [[ "$old_head" == "$new_head" ]]; then
    echo "Zaten güncel."
    exit 0
fi

changed="$(as_app git diff --name-only "$old_head" "$new_head")"
code_changed="$(grep -E '\.py$|^requirements\.txt$|^\.streamlit/config\.toml$|^assets/' <<<"$changed" || true)"

# 3) Gerekiyorsa bağımlılıklar ve yeniden başlatma.
if grep -qx 'requirements.txt' <<<"$changed"; then
    echo "==> requirements.txt değişti, bağımlılıklar kuruluyor"
    as_app "$APP_DIR/venv/bin/pip" install -r requirements.txt
fi

if [[ -n "$code_changed" ]]; then
    echo "==> Kod değişti, arayüz yeniden başlatılıyor:"
    sed 's/^/    /' <<<"$code_changed"
    systemctl restart "$SERVICE"
    systemctl --no-pager --lines=5 status "$SERVICE" || true
else
    echo "==> Sadece veri/doküman değişti, yeniden başlatma gerekmiyor."
fi

if grep -qx 'deploy/yatirim.service' <<<"$changed"; then
    echo "UYARI: deploy/yatirim.service değişti; otomatik kopyalanmaz. Uygulamak için:"
    echo "       sudo cp $APP_DIR/deploy/yatirim.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart $SERVICE"
fi
if grep -qx 'deploy/nginx-yatirim.conf' <<<"$changed"; then
    echo "UYARI: deploy/nginx-yatirim.conf değişti; otomatik kopyalanmaz. Sunucudaki dosyada certbot'un"
    echo "       eklediği HTTPS satırları var, üzerine kopyalamayın: /etc/nginx/sites-available/yatirim"
    echo "       dosyasına farkı elle uygulayıp 'sudo nginx -t && sudo systemctl reload nginx' çalıştırın."
fi
