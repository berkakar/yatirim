#!/usr/bin/env bash
# Piyasa Duyarlılığı servisini (market_sentiment.py) Droplet'te devreye alır.
#
# Kod sunucuda /opt/yatirim/app altında dakikada bir main ile eşitlendiği için
# (yatirim-app-sync.timer) bu betik dosyaları oradan alır - /root/yatirim gibi ayrı
# bir klona gerek yok. Root olarak:
#
#   sudo bash /opt/yatirim/app/deploy/enable_sentiment.sh            # kur + timer'ı aç + ilk hesaplama
#   sudo bash /opt/yatirim/app/deploy/enable_sentiment.sh --no-run   # kur + timer'ı aç, hesaplama yapma
#   sudo bash /opt/yatirim/app/deploy/enable_sentiment.sh run        # yalnızca şimdi hesapla
#   sudo bash /opt/yatirim/app/deploy/enable_sentiment.sh status     # timer + kayıtlı skorlar
#   sudo bash /opt/yatirim/app/deploy/enable_sentiment.sh disable    # timer'ı kapat
#
# Yaptıkları (kur):
#   1. Arayüz kopyasını hemen main ile eşitler, kodun güncel olduğunu doğrular.
#   2. SQLite'ın açık olduğunu (YATIRIM_DB_PATH) doğrular - servis buna yazar.
#   3. jobs.sh, yatirim-timers ve yatirim-market-sentiment.timer'ı kurar
#      (diğer işlerin timer'larına dokunmaz).
#   4. Timer'ı açar ve sonraki tetiklenme saatini gösterir.
#   5. İlk hesaplamayı ön planda çalıştırır (~1 dk) ve sonucu gösterir.
#
# Tekrar tekrar çalıştırılabilir.
set -euo pipefail

BASE="${YATIRIM_BASE:-/opt/yatirim}"
APP="$BASE/app"
VENV="$BASE/venv"
ENV_FILE="${YATIRIM_ENV_FILE:-/etc/yatirim/env}"
SYSTEMD_DIR="${YATIRIM_SYSTEMD_DIR:-/etc/systemd/system}"
TIMER=yatirim-market-sentiment.timer

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
  as_job python - <<'PY' || echo "(kayıt okunamadı)"
import market_sentiment as ms
data = ms.load_all()
for cfg in ms.MARKETS.values():
    s = data.get(cfg["market"])
    if not s:
        print(f"{cfg['market']:<11} henüz hesaplanmadı")
        continue
    print(f"{cfg['market']:<11} {s['score']:>5} {s['icon']} {s['label']:<17} kapanış: {s['as_of']} "
          f"| hesaplandı: {s['updated_at']} | {s.get('universe_size', 0)} hisse")
sec = ms.load_sectors()
if sec.get("sectors"):
    print(f"\nSektör ETF'leri (kapanış: {sec['as_of']}, 1 hafta %):")
    for r in sec["sectors"]:
        print(f"  {r['symbol']:<5} {r['name']:<22} {r['week_pct']:>+7.2f}")
else:
    print("\nSektör ETF'leri henüz hesaplanmadı")
PY
}

run_now() {
  log "Piyasa duyarlılığı hesaplanıyor (Yahoo Finance, ~1 dk)"
  if as_job python market_sentiment.py; then
    echo
    show_status
  else
    echo "UYARI: hesaplama hata verdi (yukarıdaki log). Timer yine de açık; ilk tetiklemede tekrar denenir." >&2
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
    echo "Kapatıldı. Kayıtlı skorlar silinmedi; Giriş Sayfası son değerleri göstermeye devam eder."
    exit 0
    ;;
esac

# --- 1) Kodu güncelle ve doğrula ---------------------------------------------------
[ -d "$APP/.git" ] || die "$APP bulunamadı - arayüz kurulumu beklenen yapıda değil (bkz. deploy/README.md)."
log "Arayüz kopyası main ile eşitleniyor"
systemctl start yatirim-app-sync.service || die "eşitleme başarısız (journalctl -u yatirim-app-sync -n 50)."
SRC="$APP/deploy"
[ -f "$APP/market_sentiment.py" ] || die "$APP/market_sentiment.py yok - PR henüz main'e alınmamış olabilir."
[ -f "$SRC/systemd/$TIMER" ] || die "$SRC/systemd/$TIMER yok."
grep -q "market-sentiment" "$SRC/jobs.sh" || die "$SRC/jobs.sh eski."

# --- 2) SQLite ----------------------------------------------------------------------
DB="$(sed -n 's/^YATIRIM_DB_PATH=//p' "$ENV_FILE" | tail -n1 | tr -d '"'"'")"
[ -n "$DB" ] || die "YATIRIM_DB_PATH $ENV_FILE içinde yok. Servis SQLite'a yazar; önce: sudo bash $SRC/enable_sqlite.sh"
[ -f "$DB" ] || die "$DB bulunamadı."

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

# --- 5) İlk hesaplama --------------------------------------------------------------------
if [ "$RUN" -eq 1 ]; then
  echo
  run_now || true
fi

cat <<EOF

Piyasa Duyarlılığı servisi açık (hafta içi 16:40 ET; NASDAQ 100, NYSE, BIST 100 + ABD sektör ETF'leri).
  Durum:          sudo bash $SRC/enable_sentiment.sh status
  Şimdi hesapla:  sudo bash $SRC/enable_sentiment.sh run
  Loglar:         journalctl -u yatirim-job@market-sentiment --since today
  Kapatmak:       sudo bash $SRC/enable_sentiment.sh disable
EOF
