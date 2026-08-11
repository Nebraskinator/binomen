"""Shared HTTP plumbing for live authorities.

Every request goes through the cache. `BINOMEN_OFFLINE=1` disables network
access entirely and serves only cached entries -- which is the mode eval runs
should use, so that a reported number does not silently depend on what a
remote service returned that afternoon.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

from ..cache import Cache

_cache: Cache | None = None
USER_AGENT = "binomen/0.1 (biological name resolution for agents; +https://github.com/)"


def cache() -> Cache:
    global _cache
    if _cache is None:
        _cache = Cache()
    return _cache


def offline() -> bool:
    return os.environ.get("BINOMEN_OFFLINE", "").lower() in {"1", "true", "yes"}


def get_json(source: str, url: str, params: dict | None = None, timeout: float = 20.0):
    """Return (payload, retrieved_iso, from_cache).

    Raises `LookupError` when offline with no cached entry, so callers can
    report "not consulted" rather than "not found". The distinction matters:
    "GBIF says no such name" and "we could not reach GBIF" are different facts
    and collapsing them would be exactly the kind of silent failure this
    project is about.
    """
    c = cache()
    k = Cache.key(source, url, params)
    hit = c.get(k)
    if hit:
        payload, fetched = hit
        return payload, _iso(fetched), True
    if offline():
        raise LookupError(f"{source}: offline and no cached entry for {url} {params or ''}")
    with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        payload = r.json()
    fetched = c.put(k, payload, source)
    return payload, _iso(fetched), False


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat()
