"""Backtest Veri Paketi - stop/alım algoritmalarını uzun geçmişle doğrulamak
için Alpaca'dan geçmiş bar verisi çekip repoya yazar.

Neden: canlı bar önbellekleri (alpaca_bars_cache.py) en fazla 400 gün tutuyor
ve canlı botlar kullanıyor; doğrulama betikleri (scripts/backtest_adaptive_stop.py)
ise yıllarca veri istiyor. Claude Code oturumları da dış veri kaynaklarına
erişemeyebiliyor - uygulama zaten Alpaca anahtarlarına sahip.

Nasıl: seçilen hisselerin barları çok sembollü uç noktadan (bölünme ve
temettü düzeltmeli - 5 yıllık seride bölünmeler sahte çöküş gibi görünmesin)
çekilir ve her hisse+periyot için backtest_data/<periyot>/<HİSSE>.json
dosyası, `main`'i şişirmemek için ayrı DATA_BRANCH dalına TEK bir commit
olarak yazılır (Git Data API). Dosya biçimi canlı önbellekle aynı:
{"series": {"SYM:1Day": {"bars": {t: {o,h,l,c,v}}}}, "meta": {...}}.

Kullanım (doğrulama tarafı):
    git fetch origin backtest-data
    git checkout origin/backtest-data -- backtest_data   # .gitignore'da
    python scripts/backtest_adaptive_stop.py --pack backtest_data --intraday
"""

import json
from datetime import datetime, timedelta, timezone

import requests

DATA_BRANCH = "backtest-data"
DATA_DIR = "backtest_data"
API = "https://api.github.com/repos/{repo}"

# Mevcut günlük önbellekte en az ~6 ay verisi olan hisseler (2026-10-05 doğrulamasında kullanılanlar).
DEFAULT_SYMBOLS = [
    "AMZN", "MSFT", "MU", "NOW", "MDB", "PAYX", "UROY", "CUZ", "GBCI", "TREX", "NHI", "OMCL", "AAON", "BE",
    "CRWD", "ILMN", "MRNA", "MRVL", "PAYC", "ZS", "CMDB", "EIG", "HVT", "KRUS", "MPTI", "ACAD", "FBIZ", "NECB",
    "NUTX", "UFPT",
]
# periyot -> varsayılan geçmiş (gün)
DEFAULT_SPECS = {"1Day": 5 * 365, "30Min": 365}
_BAR_FIELDS = ("o", "h", "l", "c", "v")


def parse_symbols(text: str) -> list[str]:
    seen: list[str] = []
    for part in text.replace("\n", ",").replace(" ", ",").split(","):
        symbol = part.strip().upper()
        if symbol and symbol not in seen:
            seen.append(symbol)
    return seen


def fetch_pack(client, symbols: list[str], specs: dict[str, int], now: datetime | None = None,
               progress=None) -> dict[str, str]:
    """{repo yolu: JSON metni}. progress(i, toplam, mesaj) verilirse her periyotta çağrılır."""
    now = now or datetime.now(timezone.utc)
    files: dict[str, str] = {}
    for i, (timeframe, days) in enumerate(specs.items()):
        if progress:
            progress(i, len(specs), f"{timeframe}: {len(symbols)} hisse, {days} gün")
        start = (now - timedelta(days=days)).isoformat()
        raw = client.get_raw_bars_multi(symbols, timeframe, start, chunk_size=50, adjustment="all")
        for symbol in symbols:
            bars = raw.get(symbol) or []
            if not bars:
                continue
            series = {b["t"]: {k: b[k] for k in _BAR_FIELDS if k in b} for b in bars}
            payload = {
                "meta": {"symbol": symbol, "timeframe": timeframe, "days": days, "bars": len(series),
                         "adjustment": "all", "feed": "iex", "created_at": now.isoformat()},
                "series": {f"{symbol}:{timeframe}": {"bars": series}},
            }
            files[f"{DATA_DIR}/{timeframe}/{symbol}.json"] = json.dumps(payload, separators=(",", ":"))
    if progress:
        progress(len(specs), len(specs), "tamamlandı")
    return files


def _headers(token: str) -> dict:
    return {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}


def commit_files(repo: str, token: str, files: dict[str, str], message: str, branch: str = DATA_BRANCH,
                 session=requests) -> str:
    """files'ı `branch` dalına tek commit olarak yazar (dal yoksa varsayılan
    daldan açılır). Önceki paket dosyaları üzerine yazılır. Commit sha'sını döner."""
    base = API.format(repo=repo)
    headers = _headers(token)

    r = session.get(f"{base}/git/ref/heads/{branch}", headers=headers)
    if r.status_code == 404:
        default_branch = session.get(base, headers=headers).json()["default_branch"]
        r = session.get(f"{base}/git/ref/heads/{default_branch}", headers=headers)
        r.raise_for_status()
        head_sha = r.json()["object"]["sha"]
        created = session.post(f"{base}/git/refs", headers=headers,
                               json={"ref": f"refs/heads/{branch}", "sha": head_sha})
        created.raise_for_status()
    else:
        r.raise_for_status()
        head_sha = r.json()["object"]["sha"]

    commit = session.get(f"{base}/git/commits/{head_sha}", headers=headers)
    commit.raise_for_status()
    base_tree = commit.json()["tree"]["sha"]

    tree_items = []
    for path, content in files.items():
        blob = session.post(f"{base}/git/blobs", headers=headers, json={"content": content, "encoding": "utf-8"})
        blob.raise_for_status()
        tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob.json()["sha"]})
    tree = session.post(f"{base}/git/trees", headers=headers, json={"base_tree": base_tree, "tree": tree_items})
    tree.raise_for_status()

    new_commit = session.post(f"{base}/git/commits", headers=headers,
                              json={"message": message, "tree": tree.json()["sha"], "parents": [head_sha]})
    new_commit.raise_for_status()
    sha = new_commit.json()["sha"]
    updated = session.patch(f"{base}/git/refs/heads/{branch}", headers=headers, json={"sha": sha})
    updated.raise_for_status()
    return sha
