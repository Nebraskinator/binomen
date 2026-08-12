"""Read-only access layers over the two built indexes.

`Stage1` is small, always installed, and answers one cheap question.
`Backbone` is the full local index and is optional -- stage 1 degrades honestly
without it rather than failing.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .bloom import BloomFilter
from .build.build_index import normalize_name
from .models import Provenance

DATA = Path(__file__).resolve().parents[2] / "data"
DEFAULT_DB = DATA / "binomen.sqlite"
DEFAULT_STAGE1 = DATA / "binomen-stage1.sqlite"


SCHEMA_VERSION = "5"


class IndexStale(RuntimeError):
    """The index exists but was written by a different builder.

    Worth a distinct exception because the symptom is otherwise a raw
    `sqlite3.OperationalError: no such column`, thrown deep inside a query, far
    from the cause. Both the old and current `nodes` tables have four columns,
    so an insert against the wrong one succeeds silently and only the read
    fails -- which is precisely the kind of quiet mismatch this project exists
    to complain about, so it should not be quiet here either.
    """

    def __init__(self, path: Path, detail: str):
        super().__init__(
            f"binomen index at {path} is out of date or incomplete: {detail}\n"
            f"Rebuild it:\n"
            f"    binomen-build-index\n"
            f"Run `binomen-doctor` to see exactly what is installed. Do not answer name "
            f"questions from memory in the meantime."
        )


class IndexNotBuilt(RuntimeError):
    """Raised with instructions, not just a failure.

    An agent that gets a bare error concludes the tool is broken and falls back
    on its own memory -- the outcome the tool exists to prevent. The message has
    to say what to do and what not to do.
    """

    def __init__(self, path: Path, which: str = "index"):
        super().__init__(
            f"binomen {which} not found at {path}. Build it with:\n"
            f"    binomen-build-index\n"
            f"or set BINOMEN_DB / BINOMEN_STAGE1_DB to an existing one. Until then no lookup "
            f"is possible and any name you supply would be unverified."
        )


@dataclass
class TaxonRow:
    taxid: int
    name: str
    rank: str
    parent: int
    code: str


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _read_meta(conn: sqlite3.Connection, path: Path) -> dict[str, str]:
    try:
        return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    except sqlite3.OperationalError as e:
        raise IndexStale(path, f"no readable `meta` table ({e})") from e


def _require_columns(conn: sqlite3.Connection, table: str, expected: set[str], path: Path) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if not cols:
        raise IndexStale(path, f"table `{table}` is missing entirely")
    missing = expected - cols
    if missing:
        raise IndexStale(
            path, f"table `{table}` is missing column(s) {sorted(missing)}; found {sorted(cols)}. "
                  f"A stale database or an orphaned -wal/-shm sidecar was probably recovered into "
                  f"the build. Delete {path.name} and its sidecars first.")


class Stage1:
    """The small always-installed artifact. Tens of MB, ~2 ms per query.

    Exact for every name with recorded nomenclatural history; probabilistic only
    in certifying that a name has *no* such history. See bloom.py for why that
    direction is safe.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("BINOMEN_STAGE1_DB") or DEFAULT_STAGE1)
        if not self.path.exists():
            raise IndexNotBuilt(self.path, "stage-1 index")
        self.conn = _open(self.path)
        self._meta = _read_meta(self.conn, self.path)
        _require_columns(self.conn, "verdicts",
                         {"norm", "verdict", "code", "taxid", "accepted"}, self.path)
        self._blooms: dict[str, BloomFilter] | None = None

    @property
    def meta(self) -> dict[str, str]:
        return dict(self._meta)

    @property
    def blooms(self) -> dict[str, BloomFilter]:
        if self._blooms is None:
            self._blooms = {r["code"]: BloomFilter.loads(r["blob"])
                            for r in self.conn.execute("SELECT code, blob FROM bloom")}
        return self._blooms

    def verdict(self, name: str) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT norm, verdict, code, taxid, accepted FROM verdicts WHERE norm = ?",
            (normalize_name(name),)))

    def codes_matching(self, name: str) -> list[str]:
        """Which per-code stable-name filters claim this string.

        Zero  -> no record of the name (certain: no false negatives).
        One   -> no nomenclatural history recorded under that code.
        Many  -> genuine cross-code homonym, or a filter false positive.
                 Either way, escalate; stage 2 answers exactly.
        """
        n = normalize_name(name)
        return [code for code, bf in self.blooms.items() if n in bf]

    def provenance(self) -> Provenance:
        return Provenance(
            source=self._meta.get("source", "NCBI Taxonomy"),
            version=self._meta.get("version", "unknown"),
            retrieved=self._meta.get("retrieved", "unknown"),
            license=self._meta.get("source_license"),
        )

    def close(self) -> None:
        self.conn.close()


class Backbone:
    """Full local index (stage 2).

    NCBI Taxonomy covers all cellular life and viruses from one public-domain
    download, which is why it is the backbone: the cross-kingdom generalization
    comes free at this tier and only the code-specific authorities cost
    integration work.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("BINOMEN_DB") or DEFAULT_DB)
        if not self.path.exists():
            raise IndexNotBuilt(self.path)
        self.conn = _open(self.path)
        self._meta = _read_meta(self.conn, self.path)
        # Checked once at open, not per query, so a stale index fails with an
        # actionable message before it can produce a single wrong answer.
        _require_columns(self.conn, "nodes",
                         {"taxid", "parent_taxid", "rank", "code"}, self.path)

    @property
    def meta(self) -> dict[str, str]:
        return dict(self._meta)

    def provenance(self, note: str | None = None) -> Provenance:
        return Provenance(
            source=self._meta.get("source", "NCBI Taxonomy"),
            version=self._meta.get("version", "unknown"),
            retrieved=self._meta.get("retrieved", "unknown"),
            url=self._meta.get("source_url"),
            license=self._meta.get("source_license"),
            note=note,
        )

    # -- lookups ------------------------------------------------------------
    def lookup(self, name: str) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT norm, taxid, name, name_class FROM name_norm WHERE norm = ?",
            (normalize_name(name),)))

    def prefix_lookup(self, name: str, limit: int = 25) -> list[sqlite3.Row]:
        """Prefix match, for 'did you mean'. Not a fuzzy matcher -- fuzzy
        matching is delegated to GBIF at stage 3, which does it far better."""
        return list(self.conn.execute(
            "SELECT norm, taxid, name, name_class FROM name_norm "
            "WHERE norm LIKE ? ORDER BY length(norm) LIMIT ?",
            (normalize_name(name) + "%", limit)))

    def node(self, taxid: int) -> TaxonRow | None:
        r = self.conn.execute(
            "SELECT n.taxid, n.parent_taxid, n.rank, n.code, "
            "(SELECT name FROM names WHERE taxid=n.taxid AND name_class='scientific name' LIMIT 1) "
            "AS sci FROM nodes n WHERE n.taxid = ?", (taxid,)).fetchone()
        if not r:
            return None
        return TaxonRow(r["taxid"], r["sci"] or "", r["rank"], r["parent_taxid"], r["code"])

    def code_for(self, taxid: int) -> str | None:
        r = self.conn.execute("SELECT code FROM nodes WHERE taxid = ?", (taxid,)).fetchone()
        return r["code"] if r else None

    def scientific_name(self, taxid: int) -> str | None:
        r = self.conn.execute(
            "SELECT name FROM names WHERE taxid=? AND name_class='scientific name' LIMIT 1",
            (taxid,)).fetchone()
        return r["name"] if r else None

    def unique_name(self, taxid: int) -> str | None:
        r = self.conn.execute(
            "SELECT unique_name FROM names WHERE taxid=? AND name_class='scientific name' "
            "AND unique_name IS NOT NULL AND unique_name != '' LIMIT 1", (taxid,)).fetchone()
        return r["unique_name"] if r else None

    def authority(self, taxid: int) -> str | None:
        """The author citation, when taxdump carries one.

        Frequently absent. We return None rather than composing something
        plausible: a fabricated authority string is worse than a missing one,
        because it is citable.
        """
        r = self.conn.execute(
            "SELECT name FROM names WHERE taxid=? AND name_class='authority' LIMIT 1",
            (taxid,)).fetchone()
        return r["name"] if r else None

    def names_for(self, taxid: int) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT name, unique_name, name_class FROM names WHERE taxid = ? "
            "ORDER BY name_class, name", (taxid,)))

    def lineage(self, taxid: int) -> list[list]:
        """Walk parent pointers on demand.

        This used to be a materialized JSON blob per taxon. Measured, it was 62%
        of the index and about 840 bytes per taxon at real lineage depth -- to
        support a code lookup whose answer is now one column. Walking costs ~25
        indexed reads and is not the bottleneck.
        """
        chain, seen, t = [], set(), taxid
        while t and t not in seen:
            seen.add(t)
            n = self.node(t)
            if not n:
                break
            chain.append([n.taxid, n.name, n.rank])
            if n.parent == n.taxid:
                break
            t = n.parent
        return chain[::-1]

    def lineage_names(self, taxid: int) -> list[str]:
        return [e[1] for e in self.lineage(taxid)]

    def children(self, taxid: int, limit: int = 500) -> list[TaxonRow]:
        rows = self.conn.execute(
            "SELECT taxid FROM nodes WHERE parent_taxid = ? AND taxid != ? LIMIT ?",
            (taxid, taxid, limit)).fetchall()
        out = []
        for r in rows:
            n = self.node(r["taxid"])
            if n:
                out.append(n)
        return out

    def descendants_at_rank(self, taxid: int, rank: str, limit: int = 2000) -> list[TaxonRow]:
        """Bounded breadth-first descent. 'Descendants of Bacteria' is not a
        query anyone wants answered literally."""
        out, frontier, seen = [], [taxid], {taxid}
        while frontier and len(out) < limit:
            nxt = []
            for t in frontier:
                for c in self.children(t):
                    if c.taxid in seen:
                        continue
                    seen.add(c.taxid)
                    (out if c.rank == rank else nxt).append(c if c.rank == rank else c.taxid)
                    if len(out) >= limit:
                        break
            frontier = [x for x in nxt if isinstance(x, int)]
        return out

    # -- change records -----------------------------------------------------
    def merged_into(self, taxid: int) -> int | None:
        r = self.conn.execute("SELECT new_taxid FROM merged WHERE old_taxid = ?",
                              (taxid,)).fetchone()
        return r["new_taxid"] if r else None

    def merged_from(self, taxid: int) -> list[int]:
        """Which taxids were absorbed into this one -- the lumping record."""
        return [r["old_taxid"] for r in
                self.conn.execute("SELECT old_taxid FROM merged WHERE new_taxid = ?", (taxid,))]

    def is_deleted(self, taxid: int) -> bool:
        return self.conn.execute("SELECT 1 FROM deleted WHERE taxid = ?",
                                 (taxid,)).fetchone() is not None

    def resolve_taxid(self, taxid: int) -> tuple[int, bool]:
        seen, cur, moved = {taxid}, taxid, False
        while True:
            nxt = self.merged_into(cur)
            if nxt is None or nxt in seen:
                return cur, moved
            seen.add(nxt)
            cur, moved = nxt, True

    def overlay(self, name: str) -> list[dict]:
        import json
        return [json.loads(r["payload"]) for r in self.conn.execute(
            "SELECT payload FROM overlay_notes WHERE name = ?", (normalize_name(name),))]

    def close(self) -> None:
        self.conn.close()
