#!/usr/bin/env bash
# Droplet'in kaynak kullanımını (CPU, RAM, swap, disk, servisler) Telegram'a
# gönderir. Python/pip gerektirmez; /proc, df ve systemctl'den okur.
#
#   server_monitor.sh report   # özet rapor gönder (günlük timer + elle)
#   server_monitor.sh check    # eşik aşıldıysa uyar, düzelince bildir (5 dk'da bir)
#   server_monitor.sh print    # raporu sadece ekrana yaz, Telegram'a gönderme
#
# systemd: yatirim-server-report.timer / yatirim-server-check.timer
# Eşikler /etc/yatirim/env içinden değiştirilebilir (MONITOR_* değişkenleri).
set -uo pipefail

MODE="${1:-report}"
BASE="${YATIRIM_BASE:-/opt/yatirim}"
VENV="$BASE/venv"
STATE_DIR="$BASE/.monitor"

DISK_PCT="${MONITOR_DISK_PCT:-85}"        # kök diskte kullanım % üst sınırı
MEM_PCT="${MONITOR_MEM_PCT:-90}"          # RAM kullanımı % üst sınırı (cache hariç)
SWAP_PCT="${MONITOR_SWAP_PCT:-80}"        # swap kullanımı % üst sınırı
LOAD_FACTOR="${MONITOR_LOAD_FACTOR:-2}"   # 5 dk'lık load > çekirdek × bu değer
REMIND_MIN="${MONITOR_REMIND_MIN:-180}"   # süren bir sorun için tekrar hatırlatma (dk)

HOST="$(hostname)"

# --- Ölçümler -------------------------------------------------------------------
meminfo() { awk -v k="$1:" '$1 == k { print $2 }' /proc/meminfo; }  # kB

human_kb() { awk -v k="$1" 'BEGIN {
  if (k >= 1048576) printf "%.1f GB", k / 1048576; else printf "%.0f MB", k / 1024 }'; }

pct() { awk -v a="$1" -v b="$2" 'BEGIN { if (b > 0) printf "%.0f", a * 100 / b; else print 0 }'; }

collect() {
  CORES="$(nproc)"
  read -r LOAD1 LOAD5 LOAD15 _ < /proc/loadavg
  LOAD_LIMIT="$(awk -v c="$CORES" -v f="$LOAD_FACTOR" 'BEGIN { print c * f }')"

  MEM_TOTAL="$(meminfo MemTotal)"
  MEM_AVAIL="$(meminfo MemAvailable)"
  MEM_USED=$((MEM_TOTAL - MEM_AVAIL))
  MEM_USED_PCT="$(pct "$MEM_USED" "$MEM_TOTAL")"

  SWAP_TOTAL="$(meminfo SwapTotal)"
  SWAP_USED=$((SWAP_TOTAL - $(meminfo SwapFree)))
  SWAP_USED_PCT="$(pct "$SWAP_USED" "$SWAP_TOTAL")"

  # df -P: 1K blok; kullanım yüzdesi df'nin kendi hesabı (rezerv bloklar dahil).
  read -r DISK_TOTAL DISK_USED DISK_AVAIL DISK_USED_PCT < <(
    df -Pk / | awk 'NR == 2 { gsub("%", "", $5); print $2, $3, $4, $5 }')

  UPTIME="$(uptime -p 2>/dev/null | sed 's/^up //')"

  # İki örnek arasındaki CPU kullanımı (1 sn).
  CPU_PCT="$(
    { head -1 /proc/stat; sleep 1; head -1 /proc/stat; } | awk '
      { idle = $5 + $6; total = 0; for (i = 2; i <= NF; i++) total += $i
        if (NR == 1) { i0 = idle; t0 = total } else { i1 = idle; t1 = total } }
      END { if (t1 > t0) printf "%.0f", (1 - (i1 - i0) / (t1 - t0)) * 100; else print 0 }')"

  FAILED_UNITS="$(systemctl list-units --failed --plain --no-legend 'yatirim-*' 2>/dev/null \
    | awk '{ print $1 }' | paste -sd ' ' -)"

  STREAMLIT_STATE=""
  if systemctl cat yatirim-streamlit.service >/dev/null 2>&1; then
    STREAMLIT_STATE="$(systemctl is-active yatirim-streamlit.service 2>/dev/null)"
  fi
}

# Son 24 saatte hata veren işler (run_job.sh'nin "HATA:" satırları). Journal okuma
# izni yoksa (systemd-journal grubu) boş döner.
failed_jobs_24h() {
  journalctl -q --no-pager -o cat --since "-24h" -u 'yatirim-job@*' 2>/dev/null \
    | sed -n 's/^\[\([^]]*\)\] HATA:.*/\1/p' | sort | uniq -c | sort -rn \
    | awk '{ printf "%s%s ×%s", (NR > 1 ? ", " : ""), $2, $1 }'
}

top_mem_procs() {
  ps -eo rss=,comm= --sort=-rss 2>/dev/null | head -3 \
    | awk '{ printf "%s%s %.0f MB", (NR > 1 ? ", " : ""), $2, $1 / 1024 }'
}

# Açık iş timer'larının sayısı (izleme timer'ları hariç) - 0 ise işler çalışmıyor.
active_job_timers() {
  systemctl list-timers --no-pager --no-legend 'yatirim-*.timer' 2>/dev/null \
    | grep -v -e 'yatirim-server-' -e 'yatirim-app-sync' | grep -c . || true
}

build_report() {
  local failed_jobs; failed_jobs="$(failed_jobs_24h)"
  printf '🖥️ Sunucu raporu: %s\n' "$HOST"
  printf '⏱️ Açık kalma: %s\n' "${UPTIME:-?}"
  printf '⚙️ CPU: %%%s · Load: %s / %s / %s (%s çekirdek)\n' \
    "$CPU_PCT" "$LOAD1" "$LOAD5" "$LOAD15" "$CORES"
  printf '🧠 RAM: %s / %s (%%%s)\n' "$(human_kb "$MEM_USED")" "$(human_kb "$MEM_TOTAL")" "$MEM_USED_PCT"
  if [ "$SWAP_TOTAL" -gt 0 ]; then
    printf '🔁 Swap: %s / %s (%%%s)\n' "$(human_kb "$SWAP_USED")" "$(human_kb "$SWAP_TOTAL")" "$SWAP_USED_PCT"
  fi
  printf '💾 Disk (/): %s / %s (%%%s, boş %s)\n' "$(human_kb "$DISK_USED")" \
    "$(human_kb "$DISK_TOTAL")" "$DISK_USED_PCT" "$(human_kb "$DISK_AVAIL")"
  printf '📊 En çok RAM: %s\n' "$(top_mem_procs)"
  [ -n "$STREAMLIT_STATE" ] && printf '🌐 Arayüz (streamlit): %s\n' "$STREAMLIT_STATE"
  printf '⏰ Açık iş timer'"'"'ı: %s\n' "$(active_job_timers)"
  if [ -n "$failed_jobs" ]; then
    printf '❌ Son 24 saatte hata veren işler: %s\n' "$failed_jobs"
  else
    printf '✅ Son 24 saatte hata veren iş yok\n'
  fi
  [ -n "$FAILED_UNITS" ] && printf '⚠️ failed durumdaki birimler: %s\n' "$FAILED_UNITS"
  return 0
}

# --- Telegram -----------------------------------------------------------------
# run_job.sh ile aynı kaynak: TELEGRAM_CHAT_ID boşsa uygulamanın bildirim
# ayarındaki chat_id (storage.py; SQLite açıksa veritabanından).
resolve_chat_id() {
  if [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
    echo "$TELEGRAM_CHAT_ID"
    return
  fi
  local d
  for d in "$BASE/app" "$BASE"/work/*; do
    [ -f "$d/storage.py" ] || continue
    (cd "$d" && "$VENV/bin/python" -c \
      'import storage; print((storage.load_json("bildirim_ayarlari_berkakar.json", {}).get("telegram_chat_id") or "").strip())' \
      2>/dev/null) && return
  done
}

send_telegram() {
  local chat_id
  chat_id="$(resolve_chat_id)"
  if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "$chat_id" ]; then
    echo "TELEGRAM_BOT_TOKEN ya da chat ID tanımlı değil, mesaj gönderilmedi:" >&2
    echo "$1" >&2
    return 1
  fi
  curl -fsS -m 15 -o /dev/null "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=$chat_id" --data-urlencode "text=$1"
}

# --- Eşik kontrolü --------------------------------------------------------------
# Her sorun için bir damga dosyası tutulur: sorun ilk görüldüğünde uyarı, sürdükçe
# REMIND_MIN dakikada bir hatırlatma, düzelince tek bir "düzeldi" mesajı gider.
check_one() {
  local key="$1" bad="$2" msg="$3"
  local stamp="$STATE_DIR/$key"
  if [ "$bad" = 1 ]; then
    if [ ! -f "$stamp" ]; then
      ALERTS+=("$msg"); touch "$stamp"
    elif [ -n "$(find "$stamp" -mmin "+$REMIND_MIN" 2>/dev/null)" ]; then
      ALERTS+=("(sürüyor) $msg"); touch "$stamp"
    fi
  elif [ -f "$stamp" ]; then
    RECOVERED+=("$key"); rm -f "$stamp"
  fi
}

run_check() {
  mkdir -p "$STATE_DIR"
  ALERTS=(); RECOVERED=()
  local over

  over=$(( DISK_USED_PCT >= DISK_PCT ))
  check_one disk "$over" "💾 Disk %$DISK_USED_PCT dolu (boş $(human_kb "$DISK_AVAIL"), sınır %$DISK_PCT)"

  over=$(( MEM_USED_PCT >= MEM_PCT ))
  check_one ram "$over" "🧠 RAM %$MEM_USED_PCT kullanımda (boş $(human_kb "$MEM_AVAIL"), sınır %$MEM_PCT)"

  over=0; [ "$SWAP_TOTAL" -gt 0 ] && over=$(( SWAP_USED_PCT >= SWAP_PCT ))
  check_one swap "$over" "🔁 Swap %$SWAP_USED_PCT kullanımda (sınır %$SWAP_PCT)"

  over="$(awk -v l="$LOAD5" -v m="$LOAD_LIMIT" 'BEGIN { print (l > m) ? 1 : 0 }')"
  check_one load "$over" "⚙️ Yük yüksek: 5 dk load $LOAD5 (sınır $LOAD_LIMIT, $CORES çekirdek)"

  over=0
  [ -n "$STREAMLIT_STATE" ] && [ "$STREAMLIT_STATE" != "active" ] && over=1
  check_one streamlit "$over" "🌐 Arayüz servisi çalışmıyor (yatirim-streamlit: $STREAMLIT_STATE)"

  local text=""
  if [ "${#ALERTS[@]}" -gt 0 ]; then
    text="🚨 Sunucu uyarısı: $HOST"$'\n'"$(printf '%s\n' "${ALERTS[@]}")"
  fi
  if [ "${#RECOVERED[@]}" -gt 0 ]; then
    [ -n "$text" ] && text+=$'\n'
    text+="✅ Düzeldi ($HOST): ${RECOVERED[*]}"
  fi
  if [ -n "$text" ]; then
    echo "$text"
    send_telegram "$text" || true
  else
    echo "Eşik aşımı yok (disk %$DISK_USED_PCT, RAM %$MEM_USED_PCT, load5 $LOAD5)."
  fi
}

collect
case "$MODE" in
  report) report="$(build_report)"; echo "$report"; send_telegram "$report" ;;
  print)  build_report ;;
  check)  run_check ;;
  *) echo "kullanım: $0 {report|check|print}" >&2; exit 2 ;;
esac
