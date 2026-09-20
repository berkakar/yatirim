import json
from datetime import datetime
from zoneinfo import ZoneInfo

TR_TZ = ZoneInfo("Europe/Istanbul")
VERSION_FILE = "version_info.json"


def get_version_label() -> str:
    """version_info.json, .github/workflows/update_version.yml tarafından her
    main'e push'ta o push'un zamanıyla yeniden yazılır - burada sadece okunup
    TR saatine çevrilir. Dosya yoksa veya bozuksa "—" döner."""
    try:
        with open(VERSION_FILE, "r") as f:
            data = json.load(f)
        dt = datetime.fromisoformat(data["pushed_at"].replace("Z", "+00:00")).astimezone(TR_TZ)
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return "—"
