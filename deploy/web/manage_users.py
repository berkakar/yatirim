"""Arayüz giriş kullanıcılarını sunucudan yönetir (veritabanı - bkz. user_registry.py).

Kullanıcıların asıl yönetimi arayüzde (👤 Hesap → 🛡️ Kullanıcı Yönetimi); bu
script ilk yöneticiyi oluşturmak, acil durumda şifre atamak ve eski
secrets.toml kullanıcılarını veritabanına taşımak (`tasi`) içindir.

`tasi`: Streamlit'in okuduğu iki secrets dosyasındaki
[credentials.usernames.*] kullanıcılarını ve [alpaca.<kullanıcı>] anahtarlarını
veritabanına yazar, doğrular, sonra bu bölümleri secrets dosyalarından siler.
Her dosya yazımından önce yedek alınır; yazılan dosya yeniden okunup yalnızca
istenen bölümlerin silindiği doğrulanır, değilse dosyaya dokunulmaz.

Doğrudan değil deploy/web/users.sh üzerinden çalıştırın (yatirim kullanıcısıyla
ve /etc/yatirim/env'deki YATIRIM_DB_PATH / YATIRIM_SECRET_KEY ile çalıştırır).
"""
import argparse
import getpass
import os
import re
import shutil
import sys
import time

APP_DIR = os.environ.get("APP_DIR") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, APP_DIR)

try:
    import tomllib

    def _loads(text):
        return tomllib.loads(text)
except ModuleNotFoundError:  # Python < 3.11: Streamlit'in bağımlılığı olan toml paketi
    import toml

    def _loads(text):
        return toml.loads(text)

import alpaca_keys  # noqa: E402
import storage  # noqa: E402
import user_registry as reg  # noqa: E402

BASE = os.environ.get("BASE", "/opt/yatirim")
GLOBAL_FILE = os.environ.get("SECRETS_GLOBAL", f"{BASE}/.streamlit/secrets.toml")
PROJECT_FILE = os.environ.get("SECRETS_PROJECT", f"{BASE}/app/.streamlit/secrets.toml")
FILES = [GLOBAL_FILE, PROJECT_FILE]  # Streamlit'in okuma sırası: sonraki kazanır
DEFAULT_ADMINS = ["berkakar"]
ACTOR = "users.sh"

HEADER_RE = re.compile(r"^\s*\[(?!\[)\s*([^\]]+?)\s*\]\s*(#.*)?$")
MIGRATED_SECTIONS = ("credentials", "alpaca")


class Hata(Exception):
    pass


# ------------------------------------------------------------------ secrets.toml

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


def remove_sections_text(text, roots):
    """[kök] ve [kök.*] bölümlerinin hepsini siler (roots: ör. ("credentials", "alpaca"))."""
    lines = text.splitlines(keepends=True)
    drop = set()
    for key, start, end in sections(lines):
        if any(key == r or key.startswith(r + ".") for r in roots):
            drop.update(range(start, end))
    return "".join(l for i, l in enumerate(lines) if i not in drop)


def write_checked(path, new_text, expected_data):
    """Yeni metni ayrıştırıp beklenen veriyle birebir aynıysa yedekleyip yazar."""
    try:
        parsed = _loads(new_text)
    except Exception as e:
        raise Hata(f"{path} için üretilen içerik geçersiz TOML ({e}); dosyaya dokunulmadı.")
    if parsed != expected_data:
        raise Hata(f"{path} düzenlemesi beklenmeyen başka değişikliklere yol açacaktı (bölümler satır içi "
                   "tablo ya da noktalı anahtar olarak yazılmış olabilir); dosyaya dokunulmadı. "
                   "[credentials] ve [alpaca] bölümlerini elle silin.")
    backup = f"{path}.bak.{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, backup)
    st = os.stat(path)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new_text)
    os.chmod(tmp, st.st_mode & 0o777)
    os.replace(tmp, path)
    print(f"  yedek: {backup}")
    print(f"  yazıldı: {path}")


def secrets_users_and_keys():
    """İki secrets dosyasındaki tüm (kullanıcılar, alpaca bölümleri). Streamlit üst düzey
    anahtar bazında birleştirdiği için ikinci dosyadaki [credentials] birincidekileri
    gizliyordu; taşırken hiçbiri kaybolmasın diye kullanıcı bazında birleştirilir
    (aynı kullanıcı iki dosyada varsa sonraki - Streamlit'in gördüğü - kazanır)."""
    users, alpaca = {}, {}
    for path in FILES:
        _text, data = read_file(path)
        if data:
            users.update(((data.get("credentials") or {}).get("usernames") or {}))
            alpaca.update(data.get("alpaca") or {})
    return users, alpaca


def leftover_sections():
    out = []
    for path in FILES:
        _text, data = read_file(path)
        if data:
            found = [r for r in MIGRATED_SECTIONS if r in data]
            if found:
                out.append((path, found))
    return out


# ------------------------------------------------------------------ yardımcılar

def ask_password():
    p1 = getpass.getpass("Şifre: ")
    p2 = getpass.getpass("Şifre (tekrar): ")
    try:
        reg.validate_password(p1, p2)
    except reg.RegistryError as e:
        raise Hata(str(e))
    return p1


def confirm(msg, assume_yes):
    if assume_yes:
        return True
    return input(f"{msg} [e/H] ").strip().lower() in ("e", "evet", "y", "yes")


def require_db():
    if not storage.enabled():
        raise Hata("YATIRIM_DB_PATH tanımlı değil - önce SQLite'ı açın (deploy/enable_sqlite.sh).")


# ------------------------------------------------------------------ komutlar

def cmd_durum(args):
    require_db()
    problems = 0
    print(f"Veritabanı: {storage.db_path()}")
    users = reg.list_users()
    if not users:
        problems += 1
        print("SORUN: Veritabanında hiç kullanıcı yok - kimse giriş yapamaz. "
              "Taşımak için: users.sh tasi   ya da   users.sh ekle <kullanıcı> \"Ad Soyad\" e-posta --yonetici")
    for u in users:
        keys = "/".join(m for m in (alpaca_keys.PAPER, alpaca_keys.LIVE) if alpaca_keys.has_keys(u["username"], m))
        print(f"  - {u['username']:<20} {reg.STATUS_LABELS[u['status']]:<14} "
              f"{'yönetici' if u['role'] == reg.ADMIN else 'kullanıcı':<10} alpaca: {keys or '-'}")
    if users and reg.admin_count() == 0:
        problems += 1
        print("SORUN: Aktif yönetici yok - başvurular onaylanamaz. Çözüm: users.sh yonetici <kullanıcı>")
    pending = [u["username"] for u in users if u["status"] == reg.PENDING]
    if pending:
        print(f"Onay bekleyenler: {', '.join(pending)}")
    if not alpaca_keys.encryption_available():
        problems += 1
        print(f"SORUN: {alpaca_keys.ENV_KEY} tanımlı değil - Alpaca anahtarları kaydedilemez. Çözüm: users.sh anahtar")
    for path, found in leftover_sections():
        print(f"UYARI: {path} içinde hâlâ {', '.join('[' + f + ']' for f in found)} var; artık kullanılmıyor. "
              "Taşımak ve silmek için: users.sh tasi")
    if not problems:
        print("\nSorun bulunmadı.")
    return 1 if problems else 0


def cmd_ekle(args):
    require_db()
    username = reg.normalize_username(args.kullanici)
    try:
        reg.validate_username(username)
        if args.eposta:
            reg.validate_email(reg.normalize_email(args.eposta))
    except reg.RegistryError as e:
        raise Hata(str(e))
    password = ask_password()
    try:
        reg.create_user(username, args.ad, args.eposta, reg.hash_password(password),
                        role=reg.ADMIN if args.yonetici else reg.USER, actor=ACTOR)
    except reg.RegistryError as e:
        raise Hata(str(e))
    print(f"Tamam: '{username}' eklendi ({'yönetici' if args.yonetici else 'kullanıcı'}).")


def cmd_sifre(args):
    require_db()
    if not reg.get_user(args.kullanici):
        raise Hata(f"'{args.kullanici}' tanımlı değil.")
    password = ask_password()
    reg.set_password(args.kullanici, password, ACTOR, must_change=args.gecici)
    print(f"Tamam: '{args.kullanici}' şifresi değiştirildi"
          + (" (ilk girişte değiştirmesi istenecek)." if args.gecici else "."))


def cmd_dene(args):
    require_db()
    user = reg.get_user(args.kullanici)
    if not user:
        print(f"'{args.kullanici}' tanımlı değil.")
        return 1
    print(f"Durum: {reg.STATUS_LABELS[user['status']]}")
    ok = reg.check_password(getpass.getpass("Denenecek şifre: "), user["password_hash"])
    print("Şifre DOĞRU." if ok else "Şifre YANLIŞ.")
    return 0 if ok else 1


def cmd_onayla(args):
    require_db()
    try:
        reg.approve(args.kullanici, ACTOR)
    except reg.RegistryError as e:
        raise Hata(str(e))
    print(f"Tamam: '{args.kullanici}' onaylandı.")


def cmd_yonetici(args):
    require_db()
    user = reg.get_user(args.kullanici)
    if not user:
        raise Hata(f"'{args.kullanici}' tanımlı değil.")
    try:
        if user["status"] in (reg.PENDING, reg.REJECTED):
            reg.approve(args.kullanici, ACTOR)
        elif user["status"] == reg.DISABLED:
            reg.enable(args.kullanici, ACTOR)
        reg.set_role(args.kullanici, reg.USER if args.kaldir else reg.ADMIN, ACTOR)
    except reg.RegistryError as e:
        raise Hata(str(e))
    print(f"Tamam: '{args.kullanici}' artık {'kullanıcı' if args.kaldir else 'yönetici'}.")


def cmd_sil(args):
    require_db()
    if not reg.get_user(args.kullanici):
        raise Hata(f"'{args.kullanici}' tanımlı değil.")
    if not confirm(f"'{args.kullanici}' ve tüm uygulama verileri silinsin mi?", args.yes):
        print("Vazgeçildi.")
        return 1
    try:
        purged = reg.delete_user(args.kullanici, ACTOR, purge_data=not args.veri_kalsin)
    except reg.RegistryError as e:
        raise Hata(str(e))
    alpaca_keys.delete_all(reg.normalize_username(args.kullanici))
    print(f"Tamam: '{args.kullanici}' silindi ({purged} ayar kaydı).")


def cmd_tasi(args):
    """secrets.toml kullanıcılarını ve Alpaca anahtarlarını veritabanına taşır, sonra siler."""
    require_db()
    if not alpaca_keys.encryption_available():
        raise Hata(f"{alpaca_keys.ENV_KEY} tanımlı değil (users.sh tasi bunu otomatik üretir; "
                   "manage_users.py'yi doğrudan çalıştırmayın).")
    users, alpaca = secrets_users_and_keys()
    leftovers = leftover_sections()
    if not users and not alpaca and not leftovers:
        print("secrets.toml'da taşınacak kullanıcı ya da Alpaca bölümü yok.")
        return 0
    admins = [reg.normalize_username(a) for a in (args.yonetici or DEFAULT_ADMINS)]

    print("Taşınacaklar:")
    for u, v in users.items():
        u = reg.normalize_username(u)
        state = "veritabanında var, atlanacak" if reg.get_user(u) else ("yönetici" if u in admins else "kullanıcı")
        print(f"  - kullanıcı {u}: {state}")
    for u, v in alpaca.items():
        modes = [m for m, (k, s) in ((alpaca_keys.PAPER, ("key_id", "secret_key")),
                                     (alpaca_keys.LIVE, ("live_key_id", "live_secret_key")))
                 if (v or {}).get(k) and (v or {}).get(s)]
        print(f"  - alpaca {u}: {', '.join(modes) or 'anahtar yok'}")
    if not any(reg.normalize_username(u) in admins for u in users) and reg.admin_count() == 0:
        raise Hata(f"Yönetici olacak kullanıcı ({', '.join(admins)}) secrets.toml'da yok; "
                   "--yonetici <kullanıcı> ile belirtin.")
    if not confirm("Devam edilsin mi? (Sonunda bu bölümler secrets.toml'dan silinecek)", args.yes):
        print("Vazgeçildi.")
        return 1

    # 1) Veritabanına yaz.
    for u, v in users.items():
        v = dict(v or {})
        u = reg.normalize_username(u)
        if reg.get_user(u):
            continue
        pw = str(v.get("password") or "")
        if not pw:
            raise Hata(f"'{u}' için şifre yok; elle ekleyin (users.sh ekle).")
        password_hash = pw if pw.startswith("$2") else reg.hash_password(pw)
        try:
            reg.create_user(u, str(v.get("name") or u), str(v.get("email") or ""), password_hash,
                            role=reg.ADMIN if u in admins else reg.USER, actor=ACTOR, allow_reserved=True)
        except reg.RegistryError as e:
            raise Hata(f"'{u}' taşınamadı: {e}")
        print(f"  veritabanına eklendi: {u}")
    for u, v in alpaca.items():
        v = dict(v or {})
        u = reg.normalize_username(u)
        for mode, (k, s) in ((alpaca_keys.PAPER, ("key_id", "secret_key")),
                             (alpaca_keys.LIVE, ("live_key_id", "live_secret_key"))):
            if v.get(k) and v.get(s) and not alpaca_keys.has_keys(u, mode):
                alpaca_keys.set_keys(u, mode, str(v[k]), str(v[s]))
                print(f"  alpaca {mode} anahtarı şifreli kaydedildi: {u}")

    # 2) Doğrula: herkes giriş yapabilir durumda ve anahtarlar çözülebiliyor.
    for u, v in users.items():
        u = reg.normalize_username(u)
        if not reg.get_user(u):
            raise Hata(f"Doğrulama başarısız: '{u}' veritabanında yok; secrets.toml'a dokunulmadı.")
    for u, v in alpaca.items():
        u = reg.normalize_username(u)
        v = dict(v or {})
        if v.get("key_id") and alpaca_keys.get_keys(u, alpaca_keys.PAPER)[0] is None:
            raise Hata(f"Doğrulama başarısız: '{u}' paper anahtarı okunamadı; secrets.toml'a dokunulmadı.")

    # 3) secrets.toml'dan sil.
    for path, found in leftover_sections():
        text, data = read_file(path)
        expected = {k: v for k, v in data.items() if k not in MIGRATED_SECTIONS}
        write_checked(path, remove_sections_text(text, MIGRATED_SECTIONS), expected)
    print("Tamam: kullanıcılar ve Alpaca anahtarları veritabanında; secrets.toml'dan silindi.")
    print("Not: .bak.* yedekleri eski şifre/anahtarları içeriyor; girişleri doğruladıktan sonra silin.")
    return 0


def main():
    p = argparse.ArgumentParser(prog="users.sh", description="Arayüz giriş kullanıcılarını yönetir (veritabanı).")
    p.add_argument("-y", "--yes", action="store_true", help="Onay sorma")
    sub = p.add_subparsers(dest="komut", required=True)
    sub.add_parser("durum", help="Kullanıcıları ve sorunları göster")
    a = sub.add_parser("ekle", help="Aktif kullanıcı ekle")
    a.add_argument("kullanici")
    a.add_argument("ad", help='Ad Soyad, ör. "Volkan Erdoğan"')
    a.add_argument("eposta", nargs="?", default="")
    a.add_argument("--yonetici", action="store_true", help="Yönetici olarak ekle")
    s = sub.add_parser("sifre", help="Şifre ata")
    s.add_argument("kullanici")
    s.add_argument("--gecici", action="store_true", help="İlk girişte değiştirmesi istensin")
    for name, hlp in (("dene", "Şifreyi dene"), ("onayla", "Bekleyen başvuruyu onayla")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("kullanici")
    s = sub.add_parser("yonetici", help="Yönetici yap (--kaldir ile geri al)")
    s.add_argument("kullanici")
    s.add_argument("--kaldir", action="store_true")
    s = sub.add_parser("sil", help="Kullanıcıyı sil")
    s.add_argument("kullanici")
    s.add_argument("--veri-kalsin", action="store_true", help="Uygulama verilerini silme")
    t = sub.add_parser("tasi", help="secrets.toml kullanıcılarını/Alpaca anahtarlarını veritabanına taşı")
    t.add_argument("--yonetici", action="append", help=f"Yönetici olacak kullanıcı (varsayılan: {DEFAULT_ADMINS})")
    args = p.parse_args()
    if hasattr(args, "kullanici"):
        args.kullanici = reg.normalize_username(args.kullanici)
    try:
        return globals()[f"cmd_{args.komut}"](args) or 0
    except (Hata, reg.RegistryError, alpaca_keys.KeyStoreError) as e:
        print(f"Hata: {e}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nVazgeçildi.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
