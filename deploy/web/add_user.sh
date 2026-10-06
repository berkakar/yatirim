#!/usr/bin/env bash
# Arayüze yeni giriş kullanıcısı ekler: şifreyi sorar, bcrypt ile hash'ler,
# secrets.toml'a [credentials.usernames.<kullanıcı>] bloğunu yazar ve
# Streamlit'i yeniden başlatır. Kullanıcı zaten varsa hiçbir şeyi değiştirmez.
#
# Kullanım:
#   sudo bash deploy/web/add_user.sh <kullanıcı_adı> "<Ad Soyad>" [e-posta]
# Örnek:
#   sudo bash deploy/web/add_user.sh volkanerdogan "Volkan Erdoğan" volkan@ornek.com
set -euo pipefail

BASE="${BASE:-/opt/yatirim}"
SECRETS="${SECRETS:-$BASE/.streamlit/secrets.toml}"
PY="${PY:-$BASE/venv/bin/python}"
SERVICE="yatirim-streamlit"

if [ $# -lt 2 ]; then
  sed -n '6,9p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi
USERNAME="$1"
FULLNAME="$2"
EMAIL="${3:-}"

if ! [[ "$USERNAME" =~ ^[a-z0-9_]+$ ]]; then
  echo "Hata: kullanıcı adı yalnızca küçük harf, rakam ve _ içerebilir: '$USERNAME'" >&2
  exit 1
fi
[ "$(id -u)" -eq 0 ] || { echo "Hata: sudo ile çalıştırın." >&2; exit 1; }
[ -f "$SECRETS" ] || { echo "Hata: $SECRETS bulunamadı." >&2; exit 1; }
[ -x "$PY" ] || { echo "Hata: Python bulunamadı: $PY" >&2; exit 1; }

read -rsp "Şifre: " PASS1; echo
read -rsp "Şifre (tekrar): " PASS2; echo
[ -n "$PASS1" ] || { echo "Hata: şifre boş olamaz." >&2; exit 1; }
[ "$PASS1" = "$PASS2" ] || { echo "Hata: şifreler eşleşmiyor." >&2; exit 1; }

BACKUP="$SECRETS.bak.$(date +%Y%m%d-%H%M%S)"
cp -p "$SECRETS" "$BACKUP"

# Şifre komut satırında görünmesin diye ortam değişkeniyle geçiliyor.
# Python: kullanıcı var mı kontrol et, hash'le, bloğu ekle, sonucu TOML olarak doğrula.
if ! YATIRIM_PASS="$PASS1" "$PY" - "$SECRETS" "$USERNAME" "$FULLNAME" "$EMAIL" <<'PYEOF'
import os, sys, tomllib, bcrypt, json

path, username, fullname, email = sys.argv[1:5]
with open(path, "rb") as f:
    data = tomllib.load(f)
if username in data.get("credentials", {}).get("usernames", {}):
    sys.exit(f"Hata: '{username}' zaten tanımlı, dosya değiştirilmedi.")

hashed = bcrypt.hashpw(os.environ["YATIRIM_PASS"].encode(), bcrypt.gensalt()).decode()
# json.dumps TOML temel dizgileriyle uyumlu tırnaklama/kaçış üretir.
q = lambda s: json.dumps(s, ensure_ascii=False)
block = f"\n[credentials.usernames.{username}]\nname = {q(fullname)}\n"
if email:
    block += f"email = {q(email)}\n"
block += f"password = {q(hashed)}\n"

with open(path, "rb") as f:
    original = f.read()
with open(path, "ab") as f:
    if original and not original.endswith(b"\n"):
        f.write(b"\n")
    f.write(block.encode())
with open(path, "rb") as f:
    tomllib.load(f)  # bozulduysa hata verir, kabuk yedeği geri yükler
PYEOF
then
  cp -p "$BACKUP" "$SECRETS"
  rm -f "$BACKUP"
  exit 1
fi
unset PASS1 PASS2

chown yatirim:yatirim "$SECRETS"
chmod 600 "$SECRETS"
echo "Eklendi: $USERNAME ($FULLNAME) → $SECRETS"
echo "Yedek:   $BACKUP"

systemctl restart "$SERVICE"
echo "$SERVICE yeniden başlatıldı. '$USERNAME' artık giriş yapabilir."
