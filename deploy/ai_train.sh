#!/usr/bin/env bash
# Yapay Zeka Analiz Modülü - modeli sunucuda arka planda eğitir (ai_model.py train).
#
# Eğitim ayrı bir systemd birimi (yatirim-ai-train-<HİSSE>) olarak çalışır: SSH bağlantısı
# koparsa durmaz, uygulamayla aynı ortam değişkenlerini (/etc/yatirim/env - veritabanı yolu)
# ve venv'i kullanır, düşük öncelikle (Nice) çalışır. Bellek dolarsa Linux önce eğitimi
# durdurur, siteyi (Streamlit) değil (OOMScoreAdjust). Root olarak:
#
#   sudo bash /opt/yatirim/app/deploy/ai_train.sh start NVDA                 # eğitimi başlat
#   sudo bash /opt/yatirim/app/deploy/ai_train.sh start NVDA --lookback 32   # ek ayarlarla
#   sudo bash /opt/yatirim/app/deploy/ai_train.sh log NVDA                   # ilerlemeyi izle (Ctrl+C çıkar)
#   sudo bash /opt/yatirim/app/deploy/ai_train.sh status NVDA                # çalışıyor mu + kayıtlı modeller
#   sudo bash /opt/yatirim/app/deploy/ai_train.sh stop NVDA                  # eğitimi durdur
#
# Bitince model uygulamadaki "3. Model Eğitimi" bölümünde görünür.
set -euo pipefail

BASE="${YATIRIM_BASE:-/opt/yatirim}"
APP="$BASE/app"
VENV="$BASE/venv"
ENV_FILE="${YATIRIM_ENV_FILE:-/etc/yatirim/env}"

die() { echo "HATA: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "root olarak çalıştırın: sudo bash $0 $*"
cd /

ACTION="${1:-}"
TICKER="$(echo "${2:-}" | tr '[:lower:]' '[:upper:]')"
[ -n "$ACTION" ] && [ -n "$TICKER" ] || { sed -n '2,15p' "$0"; exit 2; }
shift 2
UNIT="yatirim-ai-train-${TICKER//[^A-Z0-9]/_}"

as_job() {
  runuser -u yatirim -- /bin/bash -c \
    'set -a; . "$0"; set +a; cd "$1" && PATH="$2/bin:$PATH" PYTHONUNBUFFERED=1 exec "${@:3}"' \
    "$ENV_FILE" "$APP" "$VENV" "$@"
}

memory_check() {
  local mem_mb swap_mb
  mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
  swap_mb=$(awk '/SwapTotal/ {print int($2/1024)}' /proc/meminfo)
  echo "Bellek: ${mem_mb} MB RAM, ${swap_mb} MB swap"
  if [ "$mem_mb" -lt 2000 ] && [ "$swap_mb" -lt 1500 ]; then
    echo "UYARI: RAM 2 GB'tan az ve yeterli swap yok - eğitim (~0,5-1 GB) site ile birlikte belleğe sığmayabilir." >&2
    echo "       Önerilen: 2 GB swap ekleyin (bkz. deploy/README.md) ya da sunucuyu büyütün." >&2
  fi
}

case "$ACTION" in
  start)
    systemctl is-active --quiet "$UNIT" && die "$TICKER için eğitim zaten çalışıyor (log: $0 log $TICKER)."
    systemctl reset-failed "$UNIT" 2>/dev/null || true
    [ -x "$VENV/bin/python" ] || die "$VENV/bin/python yok."
    "$VENV/bin/python" -c "import torch" 2>/dev/null \
      || die "PyTorch kurulu değil: sudo -u yatirim $VENV/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu"
    memory_check
    systemd-run --unit="$UNIT" --uid=yatirim --gid=yatirim \
      -p EnvironmentFile="$ENV_FILE" -p WorkingDirectory="$APP" \
      -p Environment=PYTHONUNBUFFERED=1 -p Nice=10 -p OOMScoreAdjust=900 \
      "$VENV/bin/python" ai_model.py train --ticker "$TICKER" "$@"
    echo "Eğitim başladı ($UNIT). İlerleme: sudo bash $0 log $TICKER"
    ;;
  log)
    journalctl -u "$UNIT" -f --no-pager -o cat
    ;;
  status)
    systemctl status "$UNIT" --no-pager -n 5 2>/dev/null || echo "$UNIT çalışmıyor."
    echo
    echo "Kayıtlı modeller:"
    as_job python ai_model.py status --ticker "$TICKER"
    ;;
  stop)
    systemctl stop "$UNIT" && echo "Durduruldu."
    ;;
  *)
    die "bilinmeyen komut: $ACTION (start | log | status | stop)"
    ;;
esac
