"""
SQLite veritabanındaki kayıtları (storage.py) JSON dosyalarına geri yazar -
migrate_json_to_sqlite.py'nin tersi. SQLite'tan eski düzene (JSON + git)
dönerken kullanılır: SQLite açıkken holdings/state/ayarlar yalnızca veritabanında
güncellendiği için, dönmeden önce bu dosyalar repoya yazılıp main'e push
edilmezse işler günler önceki state'le çalışır.

    berkakar/orb_scan_holdings   ->  orb_scan_holdings_berkakar.json
    _shared/tefas_fonlari_cache  ->  tefas_fonlari_cache.json

Kullanım (Droplet'te, geri dönüş adımları için bkz. deploy/README.md):
    cd /root/yatirim
    YATIRIM_DB_PATH=/var/lib/yatirim/yatirim.db /opt/yatirim/venv/bin/python scripts/export_sqlite_to_json.py --dry-run
    YATIRIM_DB_PATH=/var/lib/yatirim/yatirim.db /opt/yatirim/venv/bin/python scripts/export_sqlite_to_json.py

Varsayılan hedef repo kökü; --dest ile değiştirilebilir. Mevcut dosyaların
üzerine yazar.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402


def filename_for(username: str, name: str) -> str:
    return f"{name}.json" if username == storage.SHARED else f"{name}_{username}.json"


def export(dest_dir: str, dry_run: bool = False) -> list:
    """Kayıtları dest_dir'e yazar; yazılan (veya yazılacak) dosya adlarını döner."""
    written = []
    for username in [storage.SHARED] + storage.list_users():
        for name in storage.list_names(username):
            filename = filename_for(username, name)
            if not dry_run:
                # save_json bilinen adları SQLite açıkken veritabanına yazar; burada
                # dosyaya yazmak istendiği için doğrudan dosya yolu kullanılıyor.
                value = storage.read(name, username)
                path = os.path.join(dest_dir, filename)
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(value, f, ensure_ascii=False, indent=2)
                os.replace(tmp, path)
            written.append(filename)
    return written


def main(argv=None) -> int:
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", default=repo_dir, help="JSON dosyalarının yazılacağı klasör (varsayılan: repo kökü)")
    parser.add_argument("--dry-run", action="store_true", help="Hiçbir şey yazmadan hangi dosyaların yazılacağını göster")
    args = parser.parse_args(argv)

    if not os.path.exists(storage.db_path()):
        print(f"Veritabanı bulunamadı: {storage.db_path()} (YATIRIM_DB_PATH doğru mu?)", file=sys.stderr)
        return 2

    print(f"Veritabanı: {storage.db_path()}")
    print(f"Hedef     : {os.path.abspath(args.dest)}")
    if args.dry_run:
        print("(deneme modu - hiçbir şey yazılmayacak)")
    files = export(args.dest, dry_run=args.dry_run)
    for f in files:
        print(f"  {f}")
    print(f"\n{len(files)} dosya {'yazılacak' if args.dry_run else 'yazıldı'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
