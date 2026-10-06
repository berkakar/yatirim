"""Arayüz giriş kullanıcılarını yönetir (secrets.toml [credentials.usernames.*]).

Streamlit iki secrets dosyası okur ve bunları ÜST DÜZEY anahtar bazında birleştirir
(sonraki dosya öncekini ezer - streamlit/runtime/secrets.py, dict.update):
  1) ~/.streamlit/secrets.toml          -> /opt/yatirim/.streamlit/secrets.toml
  2) <çalışma klasörü>/.streamlit/...    -> /opt/yatirim/app/.streamlit/secrets.toml
Yani [credentials] ikinci dosyada da varsa birinci dosyadaki kullanıcıların HİÇBİRİ
görünmez. Bu script kullanıcı listesinin gerçekte hangi dosyadan geldiğini bulur,
değişikliği oraya yazar, `duzelt` ile listeleri tek dosyada toplar.

Her yazmadan önce yedek alınır; yazılan dosya yeniden okunup yalnızca istenen
değişikliğin yapıldığı (başka hiçbir ayarın bozulmadığı) doğrulanır, değilse
dosyaya dokunulmaz. Doğrudan değil deploy/web/users.sh üzerinden çalıştırın.
"""
import argparse
import copy
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time

try:
    import tomllib

    def _loads(text):
        return tomllib.loads(text)
except ModuleNotFoundError:  # Python < 3.11: Streamlit'in bağımlılığı olan toml paketi
    import toml

    def _loads(text):
        return toml.loads(text)

import bcrypt

BASE = os.environ.get("BASE", "/opt/yatirim")
GLOBAL_FILE = os.environ.get("SECRETS_GLOBAL", f"{BASE}/.streamlit/secrets.toml")
PROJECT_FILE = os.environ.get("SECRETS_PROJECT", f"{BASE}/app/.streamlit/secrets.toml")
FILES = [GLOBAL_FILE, PROJECT_FILE]  # Streamlit'in okuma sırası: sonraki kazanır
SERVICE = os.environ.get("SERVICE", "yatirim-streamlit")
APP_USER = os.environ.get("APP_USER", "yatirim")
VENV_PY = os.environ.get("PY", f"{BASE}/venv/bin/python")

USERNAME_RE = re.compile(r"^[a-z0-9_]+$")
HEADER_RE = re.compile(r"^\s*\[(?!\[)\s*([^\]]+?)\s*\]\s*(#.*)?$")
PASSWORD_LINE_RE = re.compile(r"^\s*password\s*=")
BCRYPT_RE = re.compile(r"^\$2[aby]\$\d+\$.{53}$")


class Hata(Exception):
    pass


# ------------------------------------------------------------------ okuma

def read_file(path):
    """(metin, ayrıştırılmış dict) - dosya yoksa (None, None)."""
    if not os.path.exists(path):
        return None, None
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        return text, _loads(text)
    except Exception as e:
        raise Hata(f"{path} geçerli TOML değil: {e}")


def users_of(data):
    if not data or "credentials" not in data:
        return None
    return dict((data["credentials"] or {}).get("usernames", {}) or {})


def load_all():
    files = {}
    for path in FILES:
        text, data = read_file(path)
        files[path] = {"text": text, "data": data, "users": users_of(data)}
    return files


def active_file(files):
    """Kullanıcı listesinin gerçekte geldiği dosya: [credentials] içeren SON dosya."""
    active = None
    for path in FILES:
        if files[path]["users"] is not None:
            active = path
    return active or GLOBAL_FILE


def effective_users(files):
    return files[active_file(files)]["users"] or {}


# ------------------------------------------------------------------ metin düzenleme

def norm_key(raw):
    return ".".join(p.strip().strip('"').strip("'") for p in raw.split("."))


def sections(lines):
    """[(normalize_başlık, başlangıç, bitiş)] - bitiş hariç; başlıksız giriş kısmı dahil değil."""
    heads = [(i, norm_key(m.group(1))) for i, l in enumerate(lines) if (m := HEADER_RE.match(l))]
    out = []
    for n, (i, key) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        out.append((key, i, end))
    return out


def find_user_section(lines, username):
    for key, start, end in sections(lines):
        if key == f"credentials.usernames.{username}":
            return start, end
    return None


def toml_str(s):
    return json.dumps(s, ensure_ascii=False)  # TOML temel dizgisiyle uyumlu kaçış


def user_block(username, fields):
    lines = [f"[credentials.usernames.{username}]"]
    for k in ("name", "email", "password"):
        if fields.get(k):
            lines.append(f"{k} = {toml_str(fields[k])}")
    return lines


def append_block(text, block):
    text = text or ""
    if text and not text.endswith("\n"):
        text += "\n"
    return text + "\n" + "\n".join(block) + "\n"


def remove_user_text(text, username):
    lines = text.splitlines(keepends=True)
    sec = find_user_section(lines, username)
    if not sec:
        raise Hata(f"'{username}' bloğu [credentials.usernames.{username}] başlığıyla yazılmamış; "
                   "bu biçimi script düzenleyemiyor, dosyayı elle düzenleyin.")
    start, end = sec
    return "".join(lines[:start] + lines[end:])


def set_password_text(text, username, hashed):
    lines = text.splitlines(keepends=True)
    sec = find_user_section(lines, username)
    if not sec:
        raise Hata(f"'{username}' bloğu [credentials.usernames.{username}] başlığıyla yazılmamış; "
                   "bu biçimi script düzenleyemiyor, dosyayı elle düzenleyin.")
    start, end = sec
    new = f"password = {toml_str(hashed)}\n"
    for i in range(start + 1, end):
        if PASSWORD_LINE_RE.match(lines[i]):
            lines[i] = new
            break
    else:
        lines.insert(start + 1, new)
    return "".join(lines)


def remove_credentials_text(text):
    """[credentials] ve [credentials.*] bölümlerinin hepsini siler."""
    lines = text.splitlines(keepends=True)
    drop = set()
    for key, start, end in sections(lines):
        if key == "credentials" or key.startswith("credentials."):
            drop.update(range(start, end))
    return "".join(l for i, l in enumerate(lines) if i not in drop)


# ------------------------------------------------------------------ güvenli yazma

def write_checked(path, new_text, expected_data):
    """Yeni metni ayrıştırıp beklenen veriyle birebir aynıysa yedekleyip yazar."""
    try:
        parsed = _loads(new_text)
    except Exception as e:
        raise Hata(f"{path} için üretilen içerik geçersiz TOML ({e}); dosyaya dokunulmadı.")
    if parsed != expected_data:
        raise Hata(f"{path} düzenlemesi beklenmeyen başka değişikliklere yol açacaktı; "
                   "dosyaya dokunulmadı. Dosyayı elle düzenleyin.")
    if os.path.exists(path):
        backup = f"{path}.bak.{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(path, backup)
        st = os.stat(path)
        mode, uid, gid = st.st_mode & 0o777, st.st_uid, st.st_gid
        print(f"  yedek: {backup}")
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode, uid, gid = 0o600, None, None
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_text)
    os.chmod(tmp, mode)
    if uid is not None:
        os.chown(tmp, uid, gid)
    else:
        shutil.chown(tmp, APP_USER, APP_USER)
    os.replace(tmp, path)
    print(f"  yazıldı: {path}")


# ------------------------------------------------------------------ yardımcılar

def ask_password():
    p1 = getpass.getpass("Şifre: ")
    p2 = getpass.getpass("Şifre (tekrar): ")
    if not p1:
        raise Hata("Şifre boş olamaz.")
    if p1 != p2:
        raise Hata("Şifreler eşleşmiyor.")
    return bcrypt.hashpw(p1.encode(), bcrypt.gensalt()).decode()


def check_username(u):
    if not USERNAME_RE.match(u):
        raise Hata(f"Kullanıcı adı yalnızca küçük harf, rakam ve _ içerebilir: '{u}'")


def confirm(msg, assume_yes):
    if assume_yes:
        return True
    return input(f"{msg} [e/H] ").strip().lower() in ("e", "evet", "y", "yes")


def restart(args):
    if args.no_restart:
        print(f"(--no-restart) {SERVICE} yeniden başlatılmadı.")
        return
    r = subprocess.run(["systemctl", "restart", SERVICE])
    print(f"{SERVICE} yeniden başlatıldı." if r.returncode == 0
          else f"UYARI: {SERVICE} yeniden başlatılamadı (çıkış {r.returncode}).")


def live_users():
    """Uygulamanın gerçekte gördüğü kullanıcılar: Streamlit'in kendisiyle, servisle aynı
    kullanıcı/klasör/HOME ile okunur. Çalıştırılamazsa None."""
    app_dir = os.path.dirname(os.path.dirname(PROJECT_FILE))
    code = "import json,streamlit as st;print(json.dumps(list(st.secrets['credentials']['usernames'])))"
    cmd = [VENV_PY, "-c", code]
    if os.geteuid() == 0:
        cmd = ["runuser", "-u", APP_USER, "--", "env", f"HOME={os.path.dirname(os.path.dirname(GLOBAL_FILE))}"] + cmd
    try:
        r = subprocess.run(cmd, cwd=app_dir, capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else None
    except Exception:
        return None


def verify_live(expect_present=(), expect_absent=()):
    users = live_users()
    if users is None:
        print("(Uygulamanın gördüğü kullanıcılar Streamlit ile doğrulanamadı.)")
        return
    missing = [u for u in expect_present if u not in users]
    extra = [u for u in expect_absent if u in users]
    print(f"Uygulamanın gördüğü kullanıcılar: {', '.join(users)}")
    if missing or extra:
        raise Hata(f"Doğrulama başarısız - görünmeyen: {missing}, hâlâ görünen: {extra}. "
                   "`users.sh durum` çıktısına bakın.")


# ------------------------------------------------------------------ komutlar

def cmd_durum(args):
    files = load_all()
    active = active_file(files)
    print("Secrets dosyaları (Streamlit bu sırayla okur, sonraki öncekini ezer):")
    for path in FILES:
        f = files[path]
        if f["text"] is None:
            print(f"  - {path}: yok")
            continue
        users = f["users"]
        desc = "kullanıcı bölümü yok" if users is None else (", ".join(users) or "(boş)")
        mark = "  <- GEÇERLİ LİSTE" if path == active and users is not None else ""
        print(f"  - {path}: {desc}{mark}")
    eff = effective_users(files)
    print(f"\nGiriş yapabilecek kullanıcılar: {', '.join(eff) or '(yok)'}")

    problems = 0
    for path in FILES:
        users = files[path]["users"]
        if path != active and users:
            hidden = [u for u in users if u not in eff]
            problems += 1
            print(f"\nSORUN: {path} içindeki kullanıcı listesi {active} tarafından eziliyor.")
            if hidden:
                print(f"  Bu yüzden görünmeyen kullanıcılar: {', '.join(hidden)}")
            print("  Çözüm: sudo bash deploy/web/users.sh duzelt")
    for u, v in eff.items():
        pw = (v or {}).get("password", "")
        if not pw:
            problems += 1
            print(f"SORUN: '{u}' için şifre tanımlı değil.")
        elif not BCRYPT_RE.match(pw):
            print(f"UYARI: '{u}' şifresi düz metin duruyor (çalışır ama güvenli değil). "
                  f"Hash'lemek için: users.sh sifre {u}")
        if not (v or {}).get("name"):
            print(f"UYARI: '{u}' için name alanı yok.")

    live = live_users()
    if live is not None:
        print(f"\nStreamlit ile kontrol - uygulamanın gördüğü: {', '.join(live)}")
        if sorted(live) != sorted(eff):
            problems += 1
            print("SORUN: Hesaplanan liste ile Streamlit'in gördüğü farklı; başka bir secrets "
                  "kaynağı olabilir (ör. secrets.files ayarı).")
    if not problems:
        print("\nSorun bulunmadı.")
    return 1 if problems else 0


def _apply(path, files, new_text, mutate):
    expected = copy.deepcopy(files[path]["data"] or {})
    mutate(expected)
    write_checked(path, new_text, expected)


def cmd_ekle(args):
    check_username(args.kullanici)
    files = load_all()
    path = active_file(files)
    if args.kullanici in effective_users(files):
        raise Hata(f"'{args.kullanici}' zaten tanımlı. Şifresini değiştirmek için: users.sh sifre {args.kullanici}")
    print(f"Kullanıcı listesinin geçerli olduğu dosya: {path}")
    fields = {"name": args.ad, "email": args.eposta, "password": ask_password()}

    def mutate(d):
        d.setdefault("credentials", {}).setdefault("usernames", {})[args.kullanici] = \
            {k: v for k, v in fields.items() if v}

    _apply(path, files, append_block(files[path]["text"], user_block(args.kullanici, fields)), mutate)
    restart(args)
    verify_live(expect_present=[args.kullanici])
    print(f"Tamam: '{args.kullanici}' eklendi.")


def cmd_sifre(args):
    files = load_all()
    path = active_file(files)
    if args.kullanici not in effective_users(files):
        raise Hata(f"'{args.kullanici}' tanımlı değil. Eklemek için: users.sh ekle {args.kullanici} \"Ad Soyad\"")
    hashed = ask_password()

    def mutate(d):
        d["credentials"]["usernames"][args.kullanici]["password"] = hashed

    _apply(path, files, set_password_text(files[path]["text"], args.kullanici, hashed), mutate)
    restart(args)
    print(f"Tamam: '{args.kullanici}' şifresi değiştirildi.")


def cmd_sil(args):
    files = load_all()
    path = active_file(files)
    if args.kullanici not in effective_users(files):
        raise Hata(f"'{args.kullanici}' tanımlı değil.")
    if not confirm(f"'{args.kullanici}' silinsin mi?", args.yes):
        print("Vazgeçildi.")
        return 1

    def mutate(d):
        del d["credentials"]["usernames"][args.kullanici]

    _apply(path, files, remove_user_text(files[path]["text"], args.kullanici), mutate)
    restart(args)
    verify_live(expect_absent=[args.kullanici])
    print(f"Tamam: '{args.kullanici}' silindi.")


def cmd_dene(args):
    users = effective_users(load_all())
    if args.kullanici not in users:
        print(f"'{args.kullanici}' uygulamada tanımlı değil (görünenler: {', '.join(users)}).")
        return 1
    stored = (users[args.kullanici] or {}).get("password", "")
    pw = getpass.getpass("Denenecek şifre: ")
    ok = bcrypt.checkpw(pw.encode(), stored.encode()) if BCRYPT_RE.match(stored) else pw == stored
    print("Şifre DOĞRU." if ok else "Şifre YANLIŞ - bu kullanıcının kayıtlı şifresi farklı.")
    return 0 if ok else 1


def cmd_duzelt(args):
    """Kullanıcı listelerini GLOBAL_FILE'da toplar, uygulama klasöründeki kopyayı siler."""
    files = load_all()
    proj = files[PROJECT_FILE]
    if proj["users"] is None:
        print(f"{PROJECT_FILE} içinde kullanıcı bölümü yok; düzeltilecek bir şey yok.")
        return 0
    glob_users = files[GLOBAL_FILE]["users"] or {}
    merged = dict(glob_users)
    merged.update(proj["users"])  # şu an geçerli olan (proje dosyası) çakışmada kazanır
    newly_visible = [u for u in glob_users if u not in proj["users"]]
    changed = [u for u in proj["users"] if glob_users.get(u) != proj["users"][u]]

    print(f"Kullanıcılar {GLOBAL_FILE} dosyasında toplanacak ve")
    print(f"{PROJECT_FILE} içindeki [credentials] bölümü silinecek.")
    print(f"  Sonuç listesi: {', '.join(merged)}")
    if newly_visible:
        print(f"  Yeniden görünür olacak: {', '.join(newly_visible)}")
    if changed:
        print(f"  {PROJECT_FILE}'dan taşınacak/güncellenecek: {', '.join(changed)}")
    if not confirm("Devam edilsin mi?", args.yes):
        print("Vazgeçildi.")
        return 1

    # 1) Eksik/farklı kullanıcıları global dosyaya yaz (önce, ki hiçbir an kullanıcı kaybolmasın).
    if changed:
        text = files[GLOBAL_FILE]["text"] or ""
        lines = text.splitlines(keepends=True)
        for u in changed:
            if find_user_section(lines, u):
                text = remove_user_text(text, u)
            elif u in glob_users:
                raise Hata(f"{GLOBAL_FILE} içindeki '{u}' bloğu başlıklı biçimde değil; elle düzenleyin.")
            fields = proj["users"][u] or {}
            extra = set(fields) - {"name", "email", "password"}
            if extra or any(not isinstance(fields.get(k, ""), str) for k in ("name", "email", "password")):
                raise Hata(f"'{u}' bloğunda script'in taşıyamadığı alanlar var ({sorted(extra)}); elle taşıyın.")
            text = append_block(text, user_block(u, fields))
            lines = text.splitlines(keepends=True)

        def mutate(d):
            d.setdefault("credentials", {})["usernames"] = merged

        _apply(GLOBAL_FILE, files, text, mutate)

    # 2) Proje dosyasından kullanıcı bölümünü kaldır.
    def mutate_proj(d):
        d.pop("credentials", None)

    _apply(PROJECT_FILE, files, remove_credentials_text(proj["text"]), mutate_proj)
    restart(args)
    verify_live(expect_present=list(merged))
    print("Tamam: kullanıcı listesi artık tek dosyada.")


def main():
    p = argparse.ArgumentParser(prog="users.sh", description="Arayüz giriş kullanıcılarını yönetir.")
    p.add_argument("--no-restart", action="store_true", help="Streamlit'i yeniden başlatma")
    p.add_argument("-y", "--yes", action="store_true", help="Onay sorma")
    sub = p.add_subparsers(dest="komut", required=True)
    sub.add_parser("durum", help="Dosyaları, görünen kullanıcıları ve sorunları göster")
    a = sub.add_parser("ekle", help="Yeni kullanıcı ekle")
    a.add_argument("kullanici")
    a.add_argument("ad", help='Ad Soyad, ör. "Volkan Erdoğan"')
    a.add_argument("eposta", nargs="?", default="")
    for name, hlp in (("sifre", "Şifre değiştir"), ("sil", "Kullanıcı sil"), ("dene", "Şifreyi dene")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("kullanici")
    sub.add_parser("duzelt", help="Kullanıcı listelerini tek dosyada topla")
    args = p.parse_args()
    if hasattr(args, "kullanici"):
        args.kullanici = args.kullanici.strip().lower()
    try:
        return globals()[f"cmd_{args.komut}"](args) or 0
    except Hata as e:
        print(f"Hata: {e}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nVazgeçildi.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
