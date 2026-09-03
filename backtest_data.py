"""Backtest sonuçlarının kalıcı saklanması. valuation.py'deki paylaşımlı
önbellek deseniyle aynı: önce GitHub'daki (Streamlit Cloud restart'larında
kaybolmayan kalıcı) kopyayı dener, yoksa yerel dosyaya düşer, her ikisine
de yazar.

Her çalıştırma sonucu listeye EKLENİR - daha önce kaydedilmiş analizler
hiçbir zaman silinmez veya üzerine yazılmaz (append_results okur, yeni
kayıtları ekler, geri yazar)."""
import json
import os
from datetime import datetime, timezone

import streamlit as st

from github_config import read_json_from_github, write_json_to_github

GITHUB_REPO = "berkakar/yatirim"


def _results_file(username: str) -> str:
    return f"backtest_results_{username}.json"


def load_results(username: str) -> list[dict]:
    """Her zaman en güncel (GitHub'daki kalıcı) kopyayı döner - Streamlit
    Cloud'un aynı anda birden fazla container çalıştırabileceği ve yerel
    dosyanın bayat kalabileceği ihtimaline karşı, mümkünse yerel dosyaya
    hiç bakmadan doğrudan GitHub'dan okur."""
    save_file = _results_file(username)
    token = st.secrets.get("GITHUB_TOKEN")
    if token:
        try:
            return read_json_from_github(GITHUB_REPO, token, save_file, [])
        except Exception:
            pass

    if os.path.exists(save_file):
        try:
            with open(save_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def append_results(username: str, new_runs: list[dict]) -> list[dict]:
    """Mevcut sonuçları okur, new_runs'ı sonuna ekler, hem GitHub'a (kalıcı,
    mümkünse) hem yerel dosyaya yazar. Güncellenmiş tam listeyi döner."""
    results = load_results(username)
    results.extend(new_runs)

    save_file = _results_file(username)
    token = st.secrets.get("GITHUB_TOKEN")
    if token:
        try:
            write_json_to_github(GITHUB_REPO, token, save_file, results, f"Add backtest run(s) ({username})")
        except Exception as e:
            st.warning(f"⚠️ Backtest sonuçları GitHub'a kalıcı olarak kaydedilemedi (sadece bu oturumda geçerli olacak): {e}")

    with open(save_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    return results


def new_run_id(symbol: str, algorithm: str, timeframe: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{symbol}-{algorithm}-{timeframe}-{ts}"


def group_by_algorithm(results: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for run in results:
        grouped.setdefault(run.get("algorithm", "bilinmiyor"), []).append(run)
    for runs in grouped.values():
        runs.sort(key=lambda r: r.get("run_at", ""), reverse=True)
    return grouped
