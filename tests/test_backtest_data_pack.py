import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

import backtest_data_pack as pack

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))


class _Client:
    def __init__(self):
        self.calls = []

    def get_raw_bars_multi(self, symbols, timeframe, start_iso, chunk_size=200, adjustment="raw"):
        self.calls.append((tuple(symbols), timeframe, start_iso, adjustment))
        bar = {"t": "2026-01-02T05:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10, "n": 3, "vw": 1.2}
        return {"AAA": [bar]}


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Session:
    """Git Data API'yi taklit eder: data dalı yok, main var."""
    def __init__(self, branch_exists=False):
        self.branch_exists = branch_exists
        self.log = []

    def get(self, url, headers=None):
        self.log.append(("GET", url))
        if url.endswith("/git/ref/heads/backtest-data"):
            return _Resp(200, {"object": {"sha": "data1"}}) if self.branch_exists else _Resp(404, {})
        if url.endswith("/git/ref/heads/main"):
            return _Resp(200, {"object": {"sha": "main1"}})
        if "/git/commits/" in url:
            return _Resp(200, {"tree": {"sha": "tree0"}})
        return _Resp(200, {"default_branch": "main"})

    def post(self, url, headers=None, json=None):
        self.log.append(("POST", url, json))
        if url.endswith("/git/blobs"):
            return _Resp(201, {"sha": f"blob{len(self.log)}"})
        if url.endswith("/git/trees"):
            return _Resp(201, {"sha": "tree1"})
        if url.endswith("/git/commits"):
            return _Resp(201, {"sha": "commit1"})
        return _Resp(201, {})

    def patch(self, url, headers=None, json=None):
        self.log.append(("PATCH", url, json))
        return _Resp(200, {})


class FetchPackTest(unittest.TestCase):
    def test_writes_cache_shaped_files_with_adjusted_bars(self):
        client = _Client()
        now = datetime(2026, 10, 5, tzinfo=timezone.utc)
        files = pack.fetch_pack(client, ["AAA", "BBB"], {"1Day": 10}, now=now)
        self.assertEqual(list(files), ["backtest_data/1Day/AAA.json"])
        data = json.loads(files["backtest_data/1Day/AAA.json"])
        self.assertEqual(data["series"]["AAA:1Day"]["bars"]["2026-01-02T05:00:00Z"],
                         {"o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10})
        self.assertEqual(client.calls[0][3], "all")
        self.assertTrue(client.calls[0][2].startswith("2026-09-25"))

    def test_parse_symbols(self):
        self.assertEqual(pack.parse_symbols("aapl, msft\nAAPL  nvda,"), ["AAPL", "MSFT", "NVDA"])


class CommitFilesTest(unittest.TestCase):
    def test_creates_branch_from_default_and_commits_once(self):
        session = _Session(branch_exists=False)
        sha = pack.commit_files("o/r", "tok", {"backtest_data/1Day/A.json": "{}", "backtest_data/1Day/B.json": "{}"},
                                "msg", session=session)
        self.assertEqual(sha, "commit1")
        posts = [e for e in session.log if e[0] == "POST"]
        self.assertEqual(posts[0][2], {"ref": "refs/heads/backtest-data", "sha": "main1"})
        self.assertEqual(sum(1 for e in posts if e[1].endswith("/git/blobs")), 2)
        commit = next(e for e in posts if e[1].endswith("/git/commits"))
        self.assertEqual(commit[2]["parents"], ["main1"])
        self.assertEqual(session.log[-1], ("PATCH", "https://api.github.com/repos/o/r/git/refs/heads/backtest-data",
                                           {"sha": "commit1"}))

    def test_existing_branch_is_extended(self):
        session = _Session(branch_exists=True)
        pack.commit_files("o/r", "tok", {"x.json": "{}"}, "msg", session=session)
        self.assertFalse(any(e[1].endswith("/git/refs") for e in session.log if e[0] == "POST"))
        commit = next(e for e in session.log if e[0] == "POST" and e[1].endswith("/git/commits"))
        self.assertEqual(commit[2]["parents"], ["data1"])


class ScriptLoadsPackTest(unittest.TestCase):
    def test_load_pack_reads_written_files(self):
        import backtest_adaptive_stop as script
        client = _Client()
        files = pack.fetch_pack(client, ["AAA"], {"1Day": 10})
        with tempfile.TemporaryDirectory() as tmp:
            for path, content in files.items():
                full = os.path.join(tmp, path)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8") as f:
                    f.write(content)
            bars = script.load_pack(os.path.join(tmp, "backtest_data"), "1Day", 1)
        self.assertEqual(list(bars), ["AAA"])
        self.assertEqual(bars["AAA"][0].c, 1.5)


if __name__ == "__main__":
    unittest.main()
