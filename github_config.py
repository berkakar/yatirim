"""
Read/write portfolio_config.json (total budget + per-symbol weight %) via
GitHub's Contents API. This is the only piece of portfolio state that
can't live in an Alpaca watchlist (which has no room for custom fields).

Streamlit is the only caller - it needs a repo-scoped, Contents:
read-and-write personal access token to write; GitHub Actions never calls
this module at all, it just reads the same file directly from its own
fresh checkout of the repo.
"""

import base64
import json
import sys
import time

if sys.platform == "win32":
    import truststore
    truststore.inject_into_ssl()

import requests

CONFIG_PATH = "portfolio_config.json"
API_URL_TEMPLATE = "https://api.github.com/repos/{repo}/contents/{path}"

DEFAULT_CONFIG = {"budget": 0, "weights": {}}


def read_portfolio_config(repo: str, token: str) -> dict:
    url = API_URL_TEMPLATE.format(repo=repo, path=CONFIG_PATH)
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    r = requests.get(url, headers=headers)
    if r.status_code == 404:
        return dict(DEFAULT_CONFIG)
    r.raise_for_status()
    content = base64.b64decode(r.json()["content"]).decode()
    return json.loads(content)


def write_portfolio_config(repo: str, token: str, config: dict) -> None:
    url = API_URL_TEMPLATE.format(repo=repo, path=CONFIG_PATH)
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}

    existing = requests.get(url, headers=headers)
    sha = existing.json()["sha"] if existing.status_code == 200 else None

    body = {
        "message": "Update portfolio config",
        "content": base64.b64encode(json.dumps(config, indent=2).encode()).decode(),
    }
    if sha:
        body["sha"] = sha

    r = requests.put(url, headers=headers, json=body)
    if r.status_code >= 500:
        time.sleep(1)
        r = requests.put(url, headers=headers, json=body)  # GitHub's API occasionally 500s transiently
    r.raise_for_status()
