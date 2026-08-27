"""
Read/write small JSON config files via GitHub's Contents API, so they survive
a Streamlit Cloud reboot (the container re-clones the repo from scratch on every
restart - a plain local file write is lost at that point).

Streamlit is the only caller - it needs a repo-scoped, Contents:
read-and-write personal access token to write; GitHub Actions never calls
this module at all, it just reads the same files directly from its own
fresh checkout of the repo.
"""

import base64
import copy
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


def read_json_from_github(repo: str, token: str, path: str, default):
    """Read a JSON file from the repo's default branch. Returns `default` if the file doesn't exist yet.
    `default` can be a dict or a list - whatever shape the stored JSON is."""
    url = API_URL_TEMPLATE.format(repo=repo, path=path)
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    r = requests.get(url, headers=headers)
    if r.status_code == 404:
        return copy.deepcopy(default)
    r.raise_for_status()
    content = base64.b64decode(r.json()["content"]).decode()
    return json.loads(content)


def write_json_to_github(repo: str, token: str, path: str, config, message: str) -> None:
    """Write a JSON file to the repo as a commit, so it survives a Streamlit Cloud reboot
    (the container re-clones the repo from scratch on every restart)."""
    url = API_URL_TEMPLATE.format(repo=repo, path=path)
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}

    existing = requests.get(url, headers=headers)
    sha = existing.json()["sha"] if existing.status_code == 200 else None

    body = {
        "message": message,
        "content": base64.b64encode(json.dumps(config, indent=2, ensure_ascii=False).encode()).decode(),
    }
    if sha:
        body["sha"] = sha

    r = requests.put(url, headers=headers, json=body)
    if r.status_code >= 500:
        time.sleep(1)
        r = requests.put(url, headers=headers, json=body)  # GitHub's API occasionally 500s transiently
    r.raise_for_status()


def read_portfolio_config(repo: str, token: str, username: str) -> dict:
    return read_json_from_github(repo, token, f"portfolio_config_{username}.json", DEFAULT_CONFIG)


def write_portfolio_config(repo: str, token: str, config: dict, username: str) -> None:
    write_json_to_github(repo, token, f"portfolio_config_{username}.json", config, f"Update portfolio config ({username})")
