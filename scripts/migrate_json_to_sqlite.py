"""
Repo kökündeki JSON dosyalarını SQLite veritabanına (storage.py) aktarır.

Dosya adının sonu bilinen bir kullanıcı adıysa kayıt o kullanıcıya, değilse
ortak alana (storage.SHARED) yazılır:

    selected_tickers_berkakar.json  ->  ("berkakar", "selected_tickers")
    tefas_fonlari_cache.json        ->  ("_shared",  "tefas_fonlari_cache")

Kullanıcı listesi --users ile verilir; verilmezse .streamlit/secrets.toml
içindeki [credentials.usernames] bölümünden okunur.

Kullanım (Droplet'te, repo klasöründe):
    YATIRIM_DB_PATH=/var/lib/yatirim/yatirim.db venv/bin/python scripts/migrate_json_to_sqlite.py --dry-run
    YATIRIM_DB_PATH=/var/lib/yatirim/yatirim.db venv/bin/python scripts/migrate_json_to_sqlite.py

Veritabanında zaten olan kayıtlara dokunmaz; --overwrite ile üzerine yazar.
Tekrar çalıştırmak güvenlidir. Kaynak JSON dosyalarını değiştirmez veya silmez.
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402

# Uygulama verisi olmayan JSON'lar: sürüm bilgisi koda bağlı (her push'ta
# yeniden üretilir), debug dökümü ise geçici.
SKIP_FILES = {"version_info.json", "holdings_debug_dump.json"}


def users_from_secrets(path: str) -> list:
    if not os.path.exists(path):
        return []
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        import tomli as tomllib
    with open(path, "rb") as f:
        secrets = tomllib.load(f)
    return list(secrets.get("credentials", {}).get("usernames", {}).keys())


def parse_filename(filename: str, users) -> tuple:
    """Dosya adından (username, name) çıkarır. En uzun eşleşen kullanıcı adı
    seçilir (biri diğerinin son eki olan kullanıcı adları için)."""
    stem = os.path.basename(filename)[: -len(".json")]
    for user in sorted(users, key=len, reverse=True):
        suffix = f"_{user}"
        if stem.endswith(suffix) and len(stem) > len(suffix):
            return user, stem[: -len(suffix)]
    return storage.SHARED, stem


def plan(source_dir: str, users) -> list:
    """Aktarılacak dosyalar: [(path, username, name), ...]"""
    items = []
    for path in sorted(glob.glob(os.path.join(source_dir, "*.json"))):
        if os.path.basename(path) in SKIP_FILES:
            continue
        username, name = parse_filename(path, users)
        items.append((path, username, name))
    return items


def migrate(source_dir: str, users, dry_run: bool = False, overwrite: bool = False) -> dict:
    """Dosyaları aktarır. Sonuç: {"migrated": [...], "skipped": [...], "failed": [(path, hata), ...]}"""
    result = {"migrated": [], "skipped": [], "failed": []}
    seen = {}
    for path, username, name in plan(source_dir, users):
        key = (username, name)
        if key in seen:
            result["failed"].append((path, f"{seen[key]} ile aynı kayda ({username}/{name}) denk geliyor"))
            continue
        seen[key] = os.path.basename(path)

        try:
            with open(path, encoding="utf-8") as f:
                value = json.load(f)
        except (OSError, ValueError) as e:
            result["failed"].append((path, f"okunamadı: {e}"))
            continue

        if not overwrite and storage.exists(name, username):
            result["skipped"].append((path, username, name))
            continue

        if not dry_run:
            storage.write(name, username, value)
            if storage.read(name, username) != value:
                result["failed"].append((path, "yazıldıktan sonra okunan değer farklı"))
                continue
        result["migrated"].append((path, username, name))
    return result


def main(argv=None) -> int:
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-dir", default=repo_dir, help="JSON dosyalarının bulunduğu klasör (varsayılan: repo kökü)")
    parser.add_argument("--users", help="Virgülle ayrılmış kullanıcı adları (varsayılan: secrets.toml'dan)")
    parser.add_argument("--dry-run", action="store_true", help="Hiçbir şey yazmadan ne yapılacağını göster")
    parser.add_argument("--overwrite", action="store_true", help="Veritabanında olan kayıtların üzerine yaz")
    args = parser.parse_args(argv)

    if args.users:
        users = [u.strip() for u in args.users.split(",") if u.strip()]
    else:
        users = users_from_secrets(os.path.join(args.source_dir, ".streamlit", "secrets.toml"))
    if not users:
        print("Kullanıcı listesi bulunamadı: --users verin veya .streamlit/secrets.toml ekleyin.", file=sys.stderr)
        return 2

    print(f"Veritabanı : {storage.db_path()}")
    print(f"Kaynak     : {os.path.abspath(args.source_dir)}")
    print(f"Kullanıcılar: {', '.join(users)}")
    if args.dry_run:
        print("(deneme modu - hiçbir şey yazılmayacak)")
    print()

    result = migrate(args.source_dir, users, dry_run=args.dry_run, overwrite=args.overwrite)

    for path, username, name in result["migrated"]:
        print(f"  aktarıldı  {os.path.basename(path):55s} -> {username}/{name}")
    for path, username, name in result["skipped"]:
        print(f"  atlandı    {os.path.basename(path):55s} -> {username}/{name} (zaten var)")
    for path, error in result["failed"]:
        print(f"  HATA       {os.path.basename(path):55s} {error}")

    print()
    print(f"{len(result['migrated'])} aktarıldı, {len(result['skipped'])} atlandı, {len(result['failed'])} hata.")
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
