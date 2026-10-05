#!/usr/bin/env bash
# Değerleme & Ucuzluk Skoru piyasa servislerini Droplet'te devreye alır.
#
# Kod sunucuda /opt/yatirim/app altında dakikada bir main ile eşitlendiği için
# (yatirim-app-sync.timer) bu betik dosyaları oradan alır - /root/yatirim gibi ayrı
# bir klona gerek yok. Önce ilgili PR'ı main'e alın, sonra root olarak:
#
#   sudo bash /opt/yatirim/app/deploy/enable_valuation.sh                 # kur + timer'ları aç
#   sudo bash /opt/yatirim/app/deploy/enable_valuation.sh --fill          # + BIST/NASDAQ/NYSE ilk doldurma
#   sudo bash /opt/yatirim/app/deploy/enable_valuation.sh --fill --with-russell
#                                                                          # + Russell 2000 (tek seferde, ~1 saat)
#   sudo bash /opt/yatirim/app/deploy/enable_valuation.sh status          # timer'lar + son çalışmalar
#   sudo bash /opt/yatirim/app/deploy/enable_valuation.sh disable         # timer'ları kapat
#
# Yaptıkları (kur):
#   1. Arayüz kopyasını hemen main ile eşitler, kodun güncel olduğunu doğrular.
#   2. SQLite'ın açık olduğunu (YATIRIM_DB_PATH) doğrular - servisler buna yazar.
#   3. jobs.sh ve 4 valuation timer'ını kurar (diğer işlerin timer'larına dokunmaz).
#   4. Veritabanında valuation tablolarını oluşturur.
#   5. Timer'ları açar ve sonraki tetiklenme saatlerini gösterir.
#   6. --fill: ilk doldurmayı arka planda, piyasaları SIRAYLA (Yahoo'yu aynı anda
#      yormadan) çalıştırır; ilerleme: journalctl -u yatirim-valuation-fill -f
#
# Tekrar tekrar çalıştırılabilir.
set -euo pipefail

BASE="${YATIRIM_BASE:-/opt/yatirim}"
APP="$BASE/app"
VENV="$BASE/venv"
ENV_FILE="${YATIRIM_ENV_FILE:-/etc/yatirim/env}"
SYSTEMD_DIR="${YATIRIM_SYSTEMD_DIR:-/etc/systemd/system}"
FILL_UNIT=yatirim-valuation-fill
MARKETS=(bist100 nasdaq100 nyse russell2000)
TIMERS=()
for m in "${MARKETS[@]}"; do TIMERS+=("yatirim-valuation-$m.timer"); done

log() { echo "==> $*"; }
die() { echo "HATA: $*" >&2; exit 1; }
as_yatirim() { runuser -u yatirim -- "$@"; }

[ "$(id -u)" -eq 0 ] || die "root olarak çalıştırın: sudo bash $0 $*"
cd /  # yatirim kullanıcısı /root'a erişemez

ACTION=install
FILL=0
WITH_RUSSELL=0
for arg in "$@"; do
  case "$arg" in
    status|disable) ACTION="$arg" ;;
    --fill) FILL=1 ;;
    --with-russell) WITH_RUSSELL=1 ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) die "bilinmeyen seçenek: $arg (bkz. $0 --help)" ;;
  esac
done

db_path() { sed -n 's/^YATIRIM_DB_PATH=//p' "$ENV_FILE" | tail -n1 | tr -d '"'"'"; }

show_status() {
  systemctl list-timers --all --no-pager 'yatirim-valuation-*' || true
  local db; db="$(db_path)"
  [ -n "$db" ] || { echo "(YATIRIM_DB_PATH tanımlı değil)"; return; }
  echo
  (cd "$APP" && as_yatirim env YATIRIM_DB_PATH="$db" "$VENV/bin/python" - <<'PY'
import valuation_db as db
import valuation_service as svc
for slug, s in svc.SERVICES.items():
    run = db.get_run(s["market"])
    rows = db.get_rows(s["market"])
    prog = svc.cycle_progress(s["market"])
    last = f"son: {run['finished_at']} ({run['universe_size']} hisse{', YARIM' if run['aborted'] else ''})" if run else "son: henüz yok"
    extra = f" | döngü: {prog['done']}/{prog['total']}" if prog else ""
    print(f"{s['market']:<13} {s['frequency']:<34} {last} | kayıt: {len(rows)}{extra}")
PY
  ) || echo "(durum okunamadı)"
  if systemctl is-active --quiet "$FILL_UNIT"; then
    echo; echo "İlk doldurma sürüyor: journalctl -u $FILL_UNIT -f"
  fi
}

case "$ACTION" in
  status)
    show_status
    exit 0
    ;;
  disable)
    log "Timer'lar kapatılıyor"
    systemctl disable --now "${TIMERS[@]}" 2>/dev/null || true
    systemctl stop "$FILL_UNIT" 2>/dev/null || true
    echo "Kapatıldı. Veritabanındaki kayıtlar silinmedi; arayüz son verileri göstermeye devam eder."
    exit 0
    ;;
esac

# --- 1) Kodu güncelle ve doğrula ---------------------------------------------------
[ -d "$APP/.git" ] || die "$APP bulunamadı - arayüz kurulumu beklenen yapıda değil (bkz. deploy/README.md)."
log "Arayüz kopyası main ile eşitleniyor"
systemctl start yatirim-app-sync.service || die "eşitleme başarısız (journalctl -u yatirim-app-sync -n 50)."
[ -f "$APP/valuation_service.py" ] && grep -q "MODE_WEEKLY_HOURLY" "$APP/valuation_service.py" \
  || die "$APP içinde valuation servisleri yok - PR henüz main'e alınmamış olabilir."
SRC="$APP/deploy"
for m in "${MARKETS[@]}"; do
  [ -f "$SRC/systemd/yatirim-valuation-$m.timer" ] || die "$SRC/systemd/yatirim-valuation-$m.timer yok."
done
grep -q "valuation-nasdaq100" "$SRC/jobs.sh" || die "$SRC/jobs.sh eski."

# --- 2) SQLite ----------------------------------------------------------------------
DB="$(db_path)"
[ -n "$DB" ] || die "YATIRIM_DB_PATH $ENV_FILE içinde yok. Servisler SQLite'a yazar; önce: sudo bash $APP/deploy/enable_sqlite.sh"
[ -f "$DB" ] || die "$DB bulunamadı."

# --- 3) İş tanımları ve timer'lar ------------------------------------------------------
log "jobs.sh ve valuation timer'ları kuruluyor"
install -m 644 -o root -g root "$SRC/jobs.sh" "$BASE/bin/"
install -m 755 -o root -g root "$SRC/yatirim-timers" "$BASE/bin/"
for m in "${MARKETS[@]}"; do
  install -m 644 -o root -g root "$SRC/systemd/yatirim-valuation-$m.timer" "$SYSTEMD_DIR/"
done
systemctl daemon-reload

# --- 4) Tablolar ------------------------------------------------------------------------
log "Veritabanı tabloları oluşturuluyor ($DB)"
(cd "$APP" && as_yatirim env YATIRIM_DB_PATH="$DB" "$VENV/bin/python" -c \
  "import valuation_db as d; d.get_run('_'); print('    valuation_scores / valuation_runs / valuation_cycles hazır')") \
  || die "tablolar oluşturulamadı."

# --- 5) Timer'ları aç ---------------------------------------------------------------------
log "Timer'lar açılıyor"
systemctl enable --now "${TIMERS[@]}" >/dev/null
systemctl list-timers --all --no-pager 'yatirim-valuation-*'

# --- 6) İlk doldurma ------------------------------------------------------------------------
if [ "$FILL" -eq 1 ]; then
  if systemctl is-active --quiet "$FILL_UNIT"; then
    echo "İlk doldurma zaten sürüyor: journalctl -u $FILL_UNIT -f"
  else
    fill_cmds="python valuation_service.py --market bist100; python valuation_service.py --market nasdaq100; python valuation_service.py --market nyse"
    [ "$WITH_RUSSELL" -eq 1 ] && fill_cmds="$fill_cmds; python valuation_service.py --market russell2000 --full"
    log "İlk doldurma arka planda başlatılıyor (sırayla): ${fill_cmds//python valuation_service.py /}"
    systemctl reset-failed "$FILL_UNIT" 2>/dev/null || true
    # Arayüz kopyasından çalışır (iş klonları ilk tetiklemede oluşur). Ortam değişkenleri
    # işlerle aynı dosyadan; Yahoo'yu aynı anda yormamak için piyasalar art arda.
    systemd-run --quiet --unit="$FILL_UNIT" --uid=yatirim --gid=yatirim \
      --property=EnvironmentFile="$ENV_FILE" --property=WorkingDirectory="$APP" \
      --setenv=PATH="$VENV/bin:/usr/bin:/bin" --setenv=PYTHONUNBUFFERED=1 \
      /bin/bash -c "$fill_cmds"
    echo "    İzlemek için: journalctl -u $FILL_UNIT -f"
  fi
fi

cat <<EOF

Değerleme servisleri açık.
  Durum:          sudo bash $APP/deploy/enable_valuation.sh status
  Bir işi elle:   sudo systemctl start yatirim-job@valuation-nasdaq100
  Loglar:         journalctl -u 'yatirim-job@valuation-*' --since today
  Kapatmak:       sudo bash $APP/deploy/enable_valuation.sh disable
EOF
[ "$FILL" -eq 1 ] || echo "  İlk doldurma:   sudo bash $APP/deploy/enable_valuation.sh --fill [--with-russell]"
