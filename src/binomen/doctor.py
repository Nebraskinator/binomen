"""`binomen-doctor` -- say exactly what is installed and what it contains.

Written because a wrong answer from this tool is not supposed to be a mystery.
When a name resolves oddly the useful question is never "is it broken" but
"which file was read, what release is it, and what does that file actually say
about this name" -- and that should take one command, not a debugging session.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from .build.build_index import CANARIES, JUNK_NAME_RE, normalize_name
from .db import DEFAULT_DB, DEFAULT_STAGE1

EXPECTED = {
    "nodes": {"taxid", "parent_taxid", "rank", "code"},
    "names": {"taxid", "name", "unique_name", "name_class"},
    "name_norm": {"norm", "taxid", "name", "name_class"},
    "verdicts": {"norm", "verdict", "code", "taxid", "accepted"},
    "bloom": {"code", "n", "blob"},
}


def _report_file(label: str, path: Path, tables: list[str]) -> sqlite3.Connection | None:
    print(f"\n{label}")
    print(f"  path      {path}")
    if not path.exists():
        print("  status    NOT PRESENT")
        return None
    size = path.stat().st_size
    print(f"  size      {size/1e6:.1f} MB")
    for suffix in ("-wal", "-shm", "-journal"):
        side = Path(str(path) + suffix)
        if side.exists():
            print(f"  WARNING   stale sidecar present: {side.name} ({side.stat().st_size} bytes). "
                  f"A reader may see different content than the writer wrote. Rebuild.")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta"))
    except sqlite3.OperationalError as e:
        print(f"  status    UNREADABLE: {e}")
        return conn
    print(f"  release   {meta.get('version')}   built {meta.get('retrieved')}")
    print(f"  profile   {meta.get('build_profile', '?')}  "
          f"schema v{meta.get('schema_version', '?')}  builder {meta.get('builder_version', '?')}")
    print(f"  journal   {conn.execute('PRAGMA journal_mode').fetchone()[0]}")
    ok = True
    for t in tables:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
        if not cols:
            print(f"  TABLE     {t}: MISSING")
            ok = False
            continue
        missing = EXPECTED.get(t, set()) - cols
        n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        if missing:
            print(f"  TABLE     {t}: {n} rows, MISSING COLUMNS {sorted(missing)} "
                  f"(found {sorted(cols)}) -- REBUILD REQUIRED")
            ok = False
        else:
            print(f"  table     {t}: {n} rows")
    print(f"  status    {'OK' if ok else 'BROKEN -- delete this file and rebuild'}")
    return conn


def _explain(s2: sqlite3.Connection | None, s1: sqlite3.Connection | None, name: str) -> None:
    """Why does this name resolve the way it does?"""
    norm = normalize_name(name)
    print(f"\n  {name!r}  (normalized: {norm!r})")
    if s1:
        try:
            v = s1.execute("SELECT verdict, code, taxid, accepted FROM verdicts WHERE norm=?",
                           (norm,)).fetchall()
            print(f"    stage 1 verdicts : {v or 'none'}")
        except sqlite3.OperationalError as e:
            print(f"    stage 1          : unreadable ({e})")
    if s2:
        try:
            rows = s2.execute(
                "SELECT taxid, name, name_class FROM name_norm WHERE norm=?", (norm,)).fetchall()
            if rows:
                print(f"    stage 2 name_norm: {rows}")
                for taxid, _n, _k in rows:
                    sci = s2.execute("SELECT name FROM names WHERE taxid=? AND "
                                     "name_class='scientific name'", (taxid,)).fetchone()
                    node = s2.execute("SELECT rank, code FROM nodes WHERE taxid=?",
                                      (taxid,)).fetchone()
                    print(f"      txid{taxid}: accepted={sci[0] if sci else None!r} "
                          f"rank={node[0] if node else '?'} code={node[1] if node else '?'}")
            else:
                print("    stage 2 name_norm: ABSENT")
                any_name = s2.execute("SELECT taxid FROM names WHERE lower(name)=? LIMIT 1",
                                      (name.lower(),)).fetchone()
                if any_name:
                    print(f"      but present in `names` under txid{any_name[0]} -- the "
                          f"normalization or the name_norm build dropped it")
                elif JUNK_NAME_RE.search(name):
                    m = JUNK_NAME_RE.search(name)
                    print(f"      the junk filter matches this name ({m.group(0)!r}); it would "
                          f"only be kept if the taxon carries nomenclatural history")
                else:
                    print("      not in `names` either: the taxon was filtered out at build "
                          "time, or is absent from the source archive")
        except sqlite3.OperationalError as e:
            print(f"    stage 2          : unreadable ({e})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Diagnose a binomen installation.")
    ap.add_argument("names", nargs="*", help="names to explain (defaults to the canaries)")
    a = ap.parse_args(argv)

    s1p = Path(os.environ.get("BINOMEN_STAGE1_DB") or DEFAULT_STAGE1)
    s2p = Path(os.environ.get("BINOMEN_DB") or DEFAULT_DB)

    print("binomen doctor")
    print(f"  python    {sys.executable}")
    print(f"  package   {Path(__file__).resolve().parent}")
    overrides = {v: os.environ[v] for v in
                 ("BINOMEN_STAGE1_DB", "BINOMEN_DB", "BINOMEN_OFFLINE", "BINOMEN_DESCRIPTIONS")
                 if os.environ.get(v)}
    for var, val in overrides.items():
        print(f"  env       {var}={val}")
    for var in ("BINOMEN_STAGE1_DB", "BINOMEN_DB"):
        val = overrides.get(var)
        if val and ("fixture" in val.lower() or "test" in val.lower() or "tmp" in val.lower()):
            print(f"\n  !! {var} points at what looks like a TEST database:\n"
                  f"     {val}\n"
                  f"     A leftover environment variable silently overrides the index you built.\n"
                  f"     Clear it (Windows: `set {var}=`) or set it deliberately in your\n"
                  f"     Claude Desktop config, where it is visible.")

    s1 = _report_file("STAGE 1", s1p, ["verdicts", "bloom"])
    s2 = _report_file("STAGE 2", s2p, ["nodes", "names", "name_norm", "merged"])

    print("\nNAME PROBES")
    names = a.names or [n for n, _ in CANARIES]
    for n in names:
        _explain(s2, s1, n)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
