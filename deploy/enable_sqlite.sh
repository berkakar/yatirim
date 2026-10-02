#!/usr/bin/env bash
# SQLite'ı devreye alır - deploy/README.md "SQLite'ı devreye alma" adımlarının
# tek komutluk hâli. Droplet'te root olarak, piyasa kapalıyken çalıştırın:
#
#   cd /root/yatirim && git pull --ff-only origin main
#   sudo bash deploy/enable_sqlite.sh
#
# Yaptıkları:
#   1. Açık timer'ları kapatır, arayüzü ve eşitlemeyi durdurur, çalışan işin
#      bitmesini bekler.
#   2. Push edilemeyip bekleyen state (unpushed/* dalları) varsa DURUR.
#   3. Güncel run_job.sh'yi kurar, arayüz kopyasını main ile eşitler.
#   4. main'deki JSON'ları /var/lib/yatirim/yatirim.db'ye aktarır.
#   5. YATIRIM_DB_PATH'i /etc/yatirim/env'e ekler, arayüz servisine bu dosyayı
#      okutan drop-in'i kurar.
#   6. Arayüzü ve daha önce açık olan timer'ları yeniden başlatır, arayüzün
#      değişkeni gördüğünü doğrular.
# Herhangi bir adım başarısız olursa yaptığı ayar değişikliklerini geri alır ve
# servisleri/timer'ları başladığı hâline döndürür.
#
#   --force   piyasa saati kontrolünü atla
set -euo pipefail

BASE="${YATIRIM_BASE:-/opt/yatirim}"
ENV_FILE="${YATIRIM_ENV_FILE:-/etc/yatirim/env}"
DB_DIR="${YATIRIM_DB_DIR:-/var/lib/yatirim}"
SYSTEMD_DIR="${YATIRIM_SYSTEMD_DIR:-/etc/systemd/system}"
JOB_WAIT_SECONDS="${YATIRIM_JOB_WAIT_SECONDS:-900}"
APP="$BASE/app"
DB="$DB_DIR/yatirim.db"
DROPIN_DIR="$SYSTEMD_DIR/yatirim-streamlit.service.d"
DROPIN="$DROPIN_DIR/env.conf"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

log() { echo "==> $*"; }
die() { echo "HATA: $*" >&2; exit 1; }
as_yatirim() { sudo -u yatirim -H "$@"; }

# --- Ön kontroller (henüz hiçbir şey değişmedi) --------------------------------
[ "$(id -u)" -eq 0 ] || die "root olarak çalıştırın: sudo bash $0"
for p in "$BASE/bin/yatirim-timers" "$BASE/bin/jobs.sh" "$BASE/venv/bin/python" "$ENV_FILE" "$APP/.git"; do
  [ -e "$p" ] || die "$p bulunamadı - Droplet kurulumu beklenen yapıda değil (bkz. deploy/README.md)."
done
grep -q '^YATIRIM_DB_PATH=' "$ENV_FILE" && die "YATIRIM_DB_PATH zaten $ENV_FILE içinde - SQLite zaten açık."
grep -q 'storage.load_json' "$SRC/run_job.sh" \
  || die "$SRC/run_job.sh eski. Önce: cd $(dirname "$SRC") && git pull --ff-only origin main"

if [ "$FORCE" -eq 0 ]; then
  # ABD: hafta içi 02:30-20:30 ET (gece işleri + uzatılmış seans), BIST/TEFAS: hafta içi 08:30-19:30 TRT.
  ny_dow=$(TZ=America/New_York date +%u); ny_hm=$((10#$(TZ=America/New_York date +%H%M)))
  ist_dow=$(TZ=Europe/Istanbul date +%u); ist_hm=$((10#$(TZ=Europe/Istanbul date +%H%M)))
  if { [ "$ny_dow" -le 5 ] && [ "$ny_hm" -ge 230 ] && [ "$ny_hm" -lt 2030 ]; } \
     || { [ "$ist_dow" -le 5 ] && [ "$ist_hm" -ge 830 ] && [ "$ist_hm" -lt 1930 ]; }; then
    die "İşlerin çalıştığı saatlerdesiniz (New York $(TZ=America/New_York date +%a\ %H:%M), İstanbul $(TZ=Europe/Istanbul date +%a\ %H:%M)).
       Hafta sonu ya da hafta içi 20:30-02:30 ET arasında çalıştırın; emin iseniz --force ekleyin."
  fi
fi

# --- Mevcut durumu kaydet; hata olursa geri dönülecek -------------------------
# shellcheck source=deploy/jobs.sh
source "$BASE/bin/jobs.sh"
enabled_timers=()
for j in "${ALL_JOBS[@]}"; do
  systemctl is-enabled --quiet "yatirim-$j.timer" 2>/dev/null && enabled_timers+=("yatirim-$j.timer")
done
streamlit_was_active=0; systemctl is-active --quiet yatirim-streamlit && streamlit_was_active=1
sync_was_active=0; systemctl is-active --quiet yatirim-app-sync.timer && sync_was_active=1

env_added=0
dropin_added=0
done_ok=0

restore() {
  [ "$done_ok" -eq 1 ] && return
  echo >&2
  echo "!!! Bir adım başarısız oldu; değişiklikler geri alınıyor..." >&2
  if [ "$env_added" -eq 1 ]; then sed -i '/^YATIRIM_DB_PATH=/d' "$ENV_FILE"; fi
  if [ "$dropin_added" -eq 1 ]; then rm -f "$DROPIN"; rmdir "$DROPIN_DIR" 2>/dev/null || true; fi
  systemctl daemon-reload || true
  [ "$sync_was_active" -eq 1 ] && systemctl start yatirim-app-sync.timer || true
  # restart: arayüz bu çalışmada değişkenle başlatıldıysa eski ortamla yeniden açılsın.
  if [ "$streamlit_was_active" -eq 1 ]; then systemctl restart yatirim-streamlit || true; else systemctl stop yatirim-streamlit || true; fi
  [ "${#enabled_timers[@]}" -gt 0 ] && systemctl enable --now "${enabled_timers[@]}" || true
  echo "!!! Servisler ve timer'lar eski hâline döndü; SQLite KAPALI. ($DB dosyası silinmedi.)" >&2
}
trap restore EXIT

# --- 1) Durdur ----------------------------------------------------------------
log "Timer'lar kapatılıyor: ${enabled_timers[*]:-(açık timer yok)}"
[ "${#enabled_timers[@]}" -gt 0 ] && systemctl disable --now "${enabled_timers[@]}"
log "Arayüz ve eşitleme durduruluyor"
systemctl stop yatirim-app-sync.timer yatirim-streamlit

log "Çalışan iş varsa bitmesi bekleniyor (en fazla $JOB_WAIT_SECONDS sn)"
waited=0
while running="$(systemctl list-units --no-legend --plain --state=active,activating 'yatirim-job@*' | awk '{print $1}')" \
      && [ -n "$running" ]; do
  [ "$waited" -ge "$JOB_WAIT_SECONDS" ] && die "Şu işler hâlâ çalışıyor: $running"
  [ "$waited" -eq 0 ] && echo "    bekleniyor: $running"
  sleep 5; waited=$((waited + 5))
done

# --- 2) Push edilmemiş state ----------------------------------------------------
log "Push edilmemiş state kontrol ediliyor"
for d in "$BASE"/work/*/; do
  [ -d "$d/.git" ] || continue
  unpushed="$(as_yatirim git -C "$d" branch --list 'unpushed/*')"
  [ -z "$unpushed" ] || die "$d içinde push edilememiş state var:
$unpushed
       Önce bu commit'leri main'e alın; aksi halde veritabanı eksik state ile başlar."
done

# --- 3) run_job.sh ve arayüz kopyası -------------------------------------------
log "Güncel run_job.sh kuruluyor"
install -m 755 -o root -g root "$SRC/run_job.sh" "$BASE/bin/"
log "Arayüz kopyası main ile eşitleniyor"
systemctl start yatirim-app-sync.service
[ -f "$APP/scripts/migrate_json_to_sqlite.py" ] && grep -q 'def load_json' "$APP/storage.py" \
  || die "$APP içindeki kod eski; main ile eşitlenemedi (journalctl -u yatirim-app-sync)."

# --- 4) Veritabanına aktar ---------------------------------------------------------
log "Veritabanı klasörü: $DB_DIR"
install -d -o yatirim -g yatirim -m 750 "$DB_DIR"
log "JSON'lar aktarılıyor"
(cd "$APP" && as_yatirim env YATIRIM_DB_PATH="$DB" "$BASE/venv/bin/python" scripts/migrate_json_to_sqlite.py --overwrite) \
  || die "Taşıma betiği başarısız oldu (çıktı yukarıda)."

# --- 5) Değişken ve drop-in --------------------------------------------------------
log "YATIRIM_DB_PATH $ENV_FILE dosyasına ekleniyor"
[ -n "$(tail -c1 "$ENV_FILE")" ] && echo >> "$ENV_FILE"
echo "YATIRIM_DB_PATH=$DB" >> "$ENV_FILE"
env_added=1
log "Arayüz servisine $ENV_FILE okutuluyor ($DROPIN)"
mkdir -p "$DROPIN_DIR"
printf '[Service]\nEnvironmentFile=%s\n' "$ENV_FILE" > "$DROPIN"
dropin_added=1
systemctl daemon-reload

# --- 6) Başlat ve doğrula -------------------------------------------------------------
log "Arayüz ve eşitleme başlatılıyor"
systemctl start yatirim-app-sync.timer yatirim-streamlit
sleep 3
pid="$(systemctl show -p MainPID --value yatirim-streamlit)"
[ -n "$pid" ] && [ "$pid" != "0" ] || die "yatirim-streamlit başlamadı (journalctl -u yatirim-streamlit)."
tr '\0' '\n' < "/proc/$pid/environ" | grep -qx "YATIRIM_DB_PATH=$DB" \
  || die "Arayüz YATIRIM_DB_PATH'i görmüyor."

if [ "${#enabled_timers[@]}" -gt 0 ]; then
  log "Timer'lar yeniden açılıyor"
  systemctl enable --now "${enabled_timers[@]}"
fi

done_ok=1
cat <<EOF

SQLite açıldı. Veritabanı: $DB

Kontrol:
  sqlite3 $DB "SELECT username, name, updated_at FROM settings ORDER BY updated_at DESC LIMIT 10;"
  journalctl -u 'yatirim-job@*' --since "-30 min" | grep -E "Commit'lenecek|push|HATA"
İşlerin logunda "Commit'lenecek state değişikliği yok." görünmeli; GitHub'a yeni state commit'i gelmemeli.
Geri dönmek için: deploy/README.md -> "Geri dönmek".
EOF
