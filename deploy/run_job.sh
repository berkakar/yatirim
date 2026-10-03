#!/usr/bin/env bash
# Tek bir zamanlanmış işi çalıştırır: repoyu güncelle → komutları çalıştır →
# değişen state dosyalarını commit'le ve main'e push'la. GitHub Actions'taki
# workflow'ların Droplet karşılığı; systemd'deki yatirim-job@<iş>.service
# tarafından çağrılır (elle: `sudo systemctl start yatirim-job@<iş>`).
#
# Her işin kendi klonu var (/opt/yatirim/work/<iş>) - böylece aynı anda
# çalışan iki iş birbirinin çalışma ağacına/git index'ine dokunmaz (Actions'ta
# her çalıştırmanın ayrı checkout'u olması gibi). Aynı işin üst üste binmesini
# ise systemd engeller: servis hâlâ çalışırken gelen timer tetiklemesi atlanır.
set -uo pipefail

JOB="${1:?kullanım: run_job.sh <iş-adı>}"

BASE="${YATIRIM_BASE:-/opt/yatirim}"
REPO_URL="${YATIRIM_REPO_URL:-git@github.com:berkakar/yatirim.git}"
BRANCH="${YATIRIM_BRANCH:-main}"
WORK="$BASE/work/$JOB"
VENV="$BASE/venv"

# shellcheck source=deploy/jobs.sh
source "$BASE/bin/jobs.sh"
if ! job_define "$JOB"; then
  echo "Bilinmeyen iş: $JOB (bkz. $BASE/bin/jobs.sh)" >&2
  exit 2
fi

ENV_FILE="${YATIRIM_ENV_FILE:-/etc/yatirim/env}"

# APCA_*, TELEGRAM_BOT_TOKEN, YATIRIM_DB_PATH vb. - systemd EnvironmentFile ile de
# yükleniyor, elle çalıştırmalar için burada da okunuyor. Dosya kabuk komutu gibi
# `source` EDİLMİYOR: değerler systemd'deki gibi olduğu gibi alınır ($ vb. yorumlanmaz)
# ve ANAHTAR=değer biçiminde olmayan satırlar atlanır. Aksi halde tek bir bozuk satır
# (örn. 2026-10-02'de dosyaya yanlışlıkla yapıştırılan secrets.toml içeriği) `set -u`
# ile run_job.sh'yi daha başlamadan düşürüp trailing stop dahil TÜM işleri durdurmuştu.
env_bad_lines=()
if [ -r "$ENV_FILE" ]; then
  n=0
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    line="${line%$'\r'}"
    if [[ -z "${line//[[:space:]]/}" || "$line" =~ ^[[:space:]]*# ]]; then
      continue
    fi
    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      val="${BASH_REMATCH[2]}"
      # systemd gibi: değerin tamamı tırnak içindeyse tırnaklar atılır.
      if [[ "$val" =~ ^\"(.*)\"$ || "$val" =~ ^\'(.*)\'$ ]]; then
        val="${BASH_REMATCH[1]}"
      fi
      export "$key=$val"
    else
      env_bad_lines+=("$n")
    fi
  done < "$ENV_FILE"
fi

export GIT_SSH_COMMAND="ssh -i $BASE/.ssh/deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$BASE/.ssh/known_hosts"
export PATH="$VENV/bin:$PATH"
export PYTHONUNBUFFERED=1

log() { echo "[$JOB] $*"; }

send_telegram() {
  # TELEGRAM_CHAT_ID tanımlı değilse uygulamanın kendi bildirim ayarındaki chat_id
  # kullanılır (alpaca_trailing_stop.py ile aynı kaynak: storage.load_json, SQLite
  # açıksa veritabanından, değilse repodaki dosyadan).
  local text="$1"
  local chat_id="${TELEGRAM_CHAT_ID:-}"
  if [ -z "$chat_id" ] && [ -f "$WORK/storage.py" ]; then
    chat_id="$(cd "$WORK" && "$VENV/bin/python" -c \
      'import storage; print((storage.load_json("bildirim_ayarlari_berkakar.json", {}).get("telegram_chat_id") or "").strip())' \
      2>/dev/null || true)"
  fi
  if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "$chat_id" ]; then
    curl -fsS -m 15 -o /dev/null "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=$chat_id" --data-urlencode "text=$text" \
      || log "Telegram bildirimi gönderilemedi"
  fi
}

notify() {
  send_telegram "⚠️ Droplet işi başarısız: $JOB
$1"
}

# Ortam dosyasında atlanan satırlar varsa uyar; iş yine de devam eder. Her iş 5 dk'da
# bir çalıştığı için aynı dosya içeriği için en fazla saatte bir mesaj gönderilir.
warn_bad_env() {
  [ "${#env_bad_lines[@]}" -gt 0 ] || return 0
  local msg="$ENV_FILE içinde ANAHTAR=değer biçiminde olmayan ${#env_bad_lines[@]} satır atlandı (satır: ${env_bad_lines[*]}). İşler çalışmaya devam ediyor ama bu satırlardaki ayarlar okunmuyor. secrets.toml içeriği bu dosyaya girmiş olabilir; satırlar boşluksuz ANAHTAR=değer olmalı."
  log "UYARI: $msg"
  local stamp
  stamp="$BASE/.env-warning-$(sha256sum "$ENV_FILE" 2>/dev/null | cut -c1-16)"
  if [ -z "$(find "$stamp" -mmin -60 2>/dev/null)" ]; then
    send_telegram "⚠️ Droplet ortam dosyası bozuk (ilk fark eden iş: $JOB)
$msg"
    touch "$stamp" 2>/dev/null || true
  fi
}

die() {
  log "HATA: $1"
  notify "$1"
  exit 1
}

# --- 1) Repoyu origin/main'e eşitle (Actions'taki temiz checkout gibi) -------
if [ ! -d "$WORK/.git" ]; then
  mkdir -p "$BASE/work"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$WORK" || die "git clone başarısız"
fi
cd "$WORK" || die "$WORK bulunamadı"
git fetch --quiet origin "$BRANCH" || die "git fetch başarısız"
git reset --quiet --hard "origin/$BRANCH" || die "git reset başarısız"
# Chat ID'yi storage.py ile okuyabilmesi için klon güncellendikten sonra.
warn_bad_env
git clean --quiet -fd

# --- 2) requirements.txt değiştiyse venv'i güncelle ----------------------------
# Paylaşılan tek venv; aynı anda iki işin pip çalıştırmaması için global kilit.
want_hash="$(sha256sum requirements.txt | cut -d' ' -f1)"
if [ "$(cat "$VENV/.requirements.sha256" 2>/dev/null)" != "$want_hash" ]; then
  (
    flock 9
    if [ "$(cat "$VENV/.requirements.sha256" 2>/dev/null)" != "$want_hash" ]; then
      log "requirements.txt değişmiş, paketler kuruluyor..."
      "$VENV/bin/pip" install --quiet --upgrade -r requirements.txt \
        && echo "$want_hash" > "$VENV/.requirements.sha256"
    fi
  ) 9>"$BASE/.pip.lock" || die "pip install başarısız"
fi

# --- 3) Komutları çalıştır ------------------------------------------------------
# Actions'tan farklı olarak bir komut başarısız olsa da sonrakiler çalışır ve
# o ana kadar değişen state yine commit'lenir - örn. alım yapıldıktan sonra
# bir hata çıkarsa pozisyon kaydı kaybolmasın. Hata en sonda raporlanır.
failures=()
for cmd in "${JOB_CMDS[@]}"; do
  log "▶ $cmd"
  started=$SECONDS
  timeout --kill-after=30 "$JOB_TIMEOUT" bash -c "$cmd"
  rc=$?
  log "◀ çıkış kodu $rc ($((SECONDS - started)) sn)"
  if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    failures+=("$cmd → ${JOB_TIMEOUT} sn zaman aşımı")
  elif [ "$rc" -ne 0 ]; then
    failures+=("$cmd → çıkış kodu $rc")
  fi
done

# --- 4) State dosyalarını commit'le ve push'la ---------------------------------
if [ "${#JOB_FILES[@]}" -gt 0 ]; then
  for f in "${JOB_FILES[@]}"; do
    [ -f "$f" ] && git add -- "$f"
  done
  if git diff --cached --quiet; then
    log "Commit'lenecek state değişikliği yok."
  else
    # [skip ci]: update_version.yml'in her state commit'inde tetiklenmemesi için
    # (Actions'ın GITHUB_TOKEN push'ları zaten tetiklemiyordu).
    git -c user.name="yatirim-droplet" -c user.email="yatirim-droplet@users.noreply.github.com" \
      commit --quiet -m "$JOB_MSG [skip ci]"
    pushed=0
    for attempt in 1 2 3 4 5; do
      if git push --quiet origin "HEAD:$BRANCH"; then
        pushed=1
        break
      fi
      log "Push reddedildi (deneme $attempt), rebase edilip tekrar denenecek..."
      if ! git pull --quiet --rebase --autostash origin "$BRANCH"; then
        git rebase --abort 2>/dev/null
        break
      fi
      sleep "$attempt"
    done
    if [ "$pushed" -ne 1 ]; then
      # Bir sonraki çalıştırmanın reset --hard'ı bu commit'i silmesin diye
      # yerel bir branch'te sakla - elle kurtarılabilir.
      keep="unpushed/$(date -u +%Y%m%dT%H%M%SZ)"
      git branch --quiet "$keep" HEAD
      failures+=("state push edilemedi - commit $WORK içinde '$keep' branch'inde saklandı")
    else
      log "State push edildi."
    fi
  fi
fi

if [ "${#failures[@]}" -gt 0 ]; then
  die "$(printf '%s\n' "${failures[@]}")"
fi
log "Tamamlandı."
