"""On-disk cache for live authority queries.

Live sources are cached for two reasons that are not about latency. First,
reproducibility: an eval run that hits a live API is not reproducible, and a
result that cannot be reproduced is not a result. Second, courtesy: these are
free public services and an eval harness can generate a lot of requests.

Cached entries record the retrieval timestamp, which flows into provenance.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "http.sqlite"


class Cache:
    def __init__(self, path: str | Path | None = None, ttl_seconds: int = 30 * 86400):
        self.path = Path(path or os.environ.get("BINOMEN_CACHE") or DEFAULT_CACHE)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_seconds
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS entries ("
            " k TEXT PRIMARY KEY, v TEXT NOT NULL, fetched REAL NOT NULL, source TEXT)"
        )
        self.conn.commit()

    @staticmethod
    def key(source: str, url: str, params: dict | None = None) -> str:
        blob = json.dumps([source, url, params or {}], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def get(self, k: str) -> tuple[dict, float] | None:
        r = self.conn.execute("SELECT v, fetched FROM entries WHERE k = ?", (k,)).fetchone()
        if not r:
            return None
        v, fetched = r
        if self.ttl and (time.time() - fetched) > self.ttl:
            return None
        return json.loads(v), fetched

    def put(self, k: str, value: dict, source: str) -> float:
        now = time.time()
        self.conn.execute(
            "INSERT OR REPLACE INTO entries VALUES (?,?,?,?)",
            (k, json.dumps(value), now, source),
        )
        self.conn.commit()
        return now

    def stats(self) -> dict:
        rows = self.conn.execute("SELECT source, COUNT(*) FROM entries GROUP BY source").fetchall()
        return {s or "unknown": n for s, n in rows}
