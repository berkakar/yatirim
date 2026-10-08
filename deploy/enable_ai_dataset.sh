#!/usr/bin/env bash
# Yapay Zeka Analiz Modülü'nün günlük güncellemesini (ai_dataset.py update --all) Droplet'te
# zamanlanmış işe bağlar: kayıtlı eğitim veri setlerine her hafta içi 18:15 ET'de yeni
# işlem günleri eklenir.
#
# Kod sunucuda /opt/yatirim/app altında dakikada bir main ile eşitlendiği için
# (yatirim-app-sync.timer) betik dosyaları oradan alır. Root olarak:
#
#   sudo bash /opt/yatirim/app/deploy/enable_ai_dataset.sh            # kur + timer'ı aç + şimdi güncelle
#   sudo bash /opt/yatirim/app/deploy/enable_ai_dataset.sh --no-run   # kur + timer'ı aç, güncelleme yapma
#   sudo bash /opt/yatirim/app/deploy/enable_ai_dataset.sh run        # yalnızca şimdi güncelle
#   sudo bash /opt/yatirim/app/deploy/enable_ai_dataset.sh status     # timer + kayıtlı veri setleri
#   sudo bash /opt/yatirim/app/deploy/enable_ai_dataset.sh disable    # timer'ı kapat
#
# Yaptıkları (kur):
#   1. Arayüz kopyasını hemen main ile eşitler, kodun güncel olduğunu doğrular.
#   2. SQLite'ın açık olduğunu (YATIRIM_DB_PATH) doğrular - veri setleri orada.
#   3. jobs.sh, yatirim-timers ve yatirim-ai-dataset.timer'ı kurar
#      (diğer işlerin timer'larına dokunmaz).
#   4. Timer'ı açar ve sonraki tetiklenme saatini gösterir.
#   5. Güncellemeyi bir kez ön planda çalıştırır ve sonucu gösterir.
#
# Tekrar tekrar çalıştırılabilir.
set -euo pipefail

BASE="${YATIRIM_BASE:-/opt/yatirim}"
APP="$BASE/app"
VENV="$BASE/venv"
ENV_FILE="${YATIRIM_ENV_FILE:-/etc/yatirim/env}"
SYSTEMD_DIR="${YATIRIM_SYSTEMD_DIR:-/etc/systemd/system}"
TIMER=yatirim-ai-dataset.timer

log() { echo "==> $*"; }
die() { echo "HATA: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "root olarak çalıştırın: sudo bash $0 $*"
cd /  # yatirim kullanıcısı /root'a erişemez

ACTION=install
RUN=1
for arg in "$@"; do
  case "$arg" in
    status|disable|run) ACTION="$arg" ;;
    --no-run) RUN=0 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) die "bilinmeyen seçenek: $arg (bkz. $0 --help)" ;;
  esac
done

# Komutu yatirim kullanıcısıyla, işlerle aynı ortam değişkenleriyle, arayüz kopyasında çalıştırır.
as_job() {
  runuser -u yatirim -- /bin/bash -c \
    'set -a; . "$0"; set +a; cd "$1" && PATH="$2/bin:$PATH" PYTHONUNBUFFERED=1 exec "${@:3}"' \
    "$ENV_FILE" "$APP" "$VENV" "$@"
}

show_status() {
  systemctl list-timers --all --no-pager "$TIMER" || true
  echo
  echo "Kayıtlı veri setleri (ai_dataset.py status):"
  as_job python ai_dataset.py status || echo "(veri setleri okunamadı)"
}

run_now() {
  log "Kayıtlı veri setlerine yeni günler ekleniyor (Yahoo Finance + Alpaca)"
  if as_job python ai_dataset.py update --all; then
    echo
    show_status
  else
    echo "UYARI: güncelleme hata verdi (yukarıdaki log). Timer yine de açık; ilk tetiklemede tekrar denenir." >&2
    return 1
  fi
}

case "$ACTION" in
  status)
    show_status
    exit 0
    ;;
  run)
    run_now
    exit $?
    ;;
  disable)
    log "Timer kapatılıyor"
    systemctl disable --now "$TIMER" 2>/dev/null || true
    echo "Kapatıldı. Kayıtlı veri setleri silinmedi; arayüzdeki 'Yeni günleri ekle' ile elle güncellenebilir."
    exit 0
    ;;
esac

# --- 1) Kodu güncelle ve doğrula ---------------------------------------------------
[ -d "$APP/.git" ] || die "$APP bulunamadı - arayüz kurulumu beklenen yapıda değil (bkz. deploy/README.md)."
log "Arayüz kopyası main ile eşitleniyor"
systemctl start yatirim-app-sync.service || die "eşitleme başarısız (journalctl -u yatirim-app-sync -n 50)."
SRC="$APP/deploy"
[ -f "$APP/ai_dataset.py" ] || die "$APP/ai_dataset.py yok - PR henüz main'e alınmamış olabilir."
grep -q "def run_updates" "$APP/ai_dataset.py" || die "$APP/ai_dataset.py eski - main'i bekleyin."
[ -f "$SRC/systemd/$TIMER" ] || die "$SRC/systemd/$TIMER yok."
grep -q "ai-dataset" "$SRC/jobs.sh" || die "$SRC/jobs.sh eski."

# --- 2) SQLite ----------------------------------------------------------------------
DB="$(sed -n 's/^YATIRIM_DB_PATH=//p' "$ENV_FILE" | tail -n1 | tr -d '"'"'")"
[ -n "$DB" ] || die "YATIRIM_DB_PATH $ENV_FILE içinde yok. Veri setleri SQLite'ta; önce: sudo bash $SRC/enable_sqlite.sh"
[ -f "$DB" ] || die "$DB bulunamadı."
if ! grep -qs '^APCA_API_KEY_ID=..*' "$ENV_FILE"; then
  echo "UYARI: $ENV_FILE içinde APCA_API_KEY_ID yok - VWAP'ı Alpaca'dan alınan setlerin yeni günlerinde tipik fiyat kullanılır." >&2
fi

# --- 3) İş tanımı ve timer -------------------------------------------------------------
log "jobs.sh ve $TIMER kuruluyor"
install -m 644 -o root -g root "$SRC/jobs.sh" "$BASE/bin/"
install -m 755 -o root -g root "$SRC/yatirim-timers" "$BASE/bin/"
install -m 644 -o root -g root "$SRC/systemd/$TIMER" "$SYSTEMD_DIR/"
systemctl daemon-reload

# --- 4) Timer'ı aç ----------------------------------------------------------------------
log "Timer açılıyor"
systemctl enable --now "$TIMER" >/dev/null
systemctl list-timers --all --no-pager "$TIMER"

# --- 5) İlk güncelleme -------------------------------------------------------------------
if [ "$RUN" -eq 1 ]; then
  echo
  run_now || true
fi

cat <<EOF

Yapay zeka veri seti güncellemesi açık (hafta içi 18:15 ET; kayıtlı tüm veri setleri).
  Durum:           sudo bash $SRC/enable_ai_dataset.sh status
  Şimdi güncelle:  sudo bash $SRC/enable_ai_dataset.sh run
  Loglar:          journalctl -u yatirim-job@ai-dataset --since today
  Kapatmak:        sudo bash $SRC/enable_ai_dataset.sh disable
Hata olursa run_job.sh Telegram'a bildirir.
EOF
