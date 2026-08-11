"""Read-only access layer over the built backbone index."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .build.build_index import normalize_name
from .models import Provenance

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "binomen.sqlite"


class IndexNotBuilt(RuntimeError):
    """Raised with instructions rather than a bare failure.

    An agent that gets a stack trace will fall back to its own memory, which is
    the behavior we are trying to prevent. The message has to tell it what to do.
    """

    def __init__(self, path: Path):
        super().__init__(
            f"binomen index not found at {path}. Build it with:\n"
            f"    binomen-build-index\n"
            f"or point BINOMEN_DB at an existing index. Until then no lookup is "
            f"possible and any answer would be unverified."
        )


@dataclass
class TaxonRow:
    taxid: int
    name: str
    rank: str
    parent: int


class Backbone:
    """NCBI Taxonomy backbone, Tier 1.

    Covers all cellular life and viruses from a single public-domain download,
    which is why it is the backbone: the cross-kingdom generalization is free
    at this tier and only the code-specific authorities cost integration work.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("BINOMEN_DB") or DEFAULT_DB)
        if not self.path.exists():
            raise IndexNotBuilt(self.path)
        self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._meta = {r["key"]: r["value"] for r in self.conn.execute("SELECT key, value FROM meta")}

    # -- provenance ---------------------------------------------------------
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
        """All index rows matching a name, exactly or case-folded."""
        return list(self.conn.execute(
            "SELECT norm, taxid, name, name_class FROM name_norm WHERE norm = ?",
            (normalize_name(name),),
        ))

    def prefix_lookup(self, name: str, limit: int = 25) -> list[sqlite3.Row]:
        """Prefix match. Used for genus queries and for 'did you mean'.

        Not a fuzzy matcher -- fuzzy matching is delegated to GBIF (Tier 2),
        which does it far better than anything we would write here.
        """
        return list(self.conn.execute(
            "SELECT norm, taxid, name, name_class FROM name_norm "
            "WHERE norm LIKE ? ORDER BY length(norm) LIMIT ?",
            (normalize_name(name) + "%", limit),
        ))

    def node(self, taxid: int) -> TaxonRow | None:
        r = self.conn.execute(
            "SELECT n.taxid, n.parent_taxid, n.rank, "
            "(SELECT name FROM names WHERE taxid=n.taxid AND name_class='scientific name' LIMIT 1) AS sci "
            "FROM nodes n WHERE n.taxid = ?", (taxid,)
        ).fetchone()
        if not r:
            return None
        return TaxonRow(taxid=r["taxid"], name=r["sci"] or "", rank=r["rank"], parent=r["parent_taxid"])

    def scientific_name(self, taxid: int) -> str | None:
        r = self.conn.execute(
            "SELECT name FROM names WHERE taxid=? AND name_class='scientific name' LIMIT 1", (taxid,)
        ).fetchone()
        return r["name"] if r else None

    def authority(self, taxid: int) -> str | None:
        """The author citation, if taxdump carries one.

        Frequently absent. We return None rather than composing something
        plausible -- a fabricated authority string is worse than no authority
        string, because it is citable.
        """
        r = self.conn.execute(
            "SELECT name FROM names WHERE taxid=? AND name_class='authority' LIMIT 1", (taxid,)
        ).fetchone()
        return r["name"] if r else None

    def names_for(self, taxid: int) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT name, unique_name, name_class FROM names WHERE taxid = ? ORDER BY name_class, name",
            (taxid,),
        ))

    def lineage(self, taxid: int) -> list[list]:
        r = self.conn.execute("SELECT lineage FROM lineage_cache WHERE taxid = ?", (taxid,)).fetchone()
        if r:
            return json.loads(r["lineage"])
        # Fall back to a live walk if the cache is missing (partial builds).
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
            "SELECT taxid, parent_taxid, rank FROM nodes WHERE parent_taxid = ? AND taxid != ? LIMIT ?",
            (taxid, taxid, limit),
        ).fetchall()
        return [TaxonRow(r["taxid"], self.scientific_name(r["taxid"]) or "", r["rank"], r["parent_taxid"])
                for r in rows]

    def descendants_at_rank(self, taxid: int, rank: str, limit: int = 2000) -> list[TaxonRow]:
        """Breadth-first descent. Bounded, because 'descendants of Bacteria'
        is not a query anyone wants answered literally."""
        out, frontier, seen = [], [taxid], {taxid}
        while frontier and len(out) < limit:
            nxt = []
            for t in frontier:
                for c in self.children(t):
                    if c.taxid in seen:
                        continue
                    seen.add(c.taxid)
                    if c.rank == rank:
                        out.append(c)
                    else:
                        nxt.append(c.taxid)
                    if len(out) >= limit:
                        break
            frontier = nxt
        return out

    # -- change records -----------------------------------------------------
    def merged_into(self, taxid: int) -> int | None:
        r = self.conn.execute("SELECT new_taxid FROM merged WHERE old_taxid = ?", (taxid,)).fetchone()
        return r["new_taxid"] if r else None

    def merged_from(self, taxid: int) -> list[int]:
        """Which taxids were absorbed into this one.

        This is the lumping record: every entry is NCBI stating that things
        once counted separately are now counted once.
        """
        return [r["old_taxid"] for r in
                self.conn.execute("SELECT old_taxid FROM merged WHERE new_taxid = ?", (taxid,))]

    def is_deleted(self, taxid: int) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM deleted WHERE taxid = ?", (taxid,)).fetchone() is not None

    def resolve_taxid(self, taxid: int) -> tuple[int, bool]:
        """Follow merge chains to a live taxid. Returns (taxid, was_merged)."""
        seen, cur, moved = {taxid}, taxid, False
        while True:
            nxt = self.merged_into(cur)
            if nxt is None or nxt in seen:
                return cur, moved
            seen.add(nxt)
            cur, moved = nxt, True

    # -- overlay ------------------------------------------------------------
    def overlay(self, name: str) -> list[dict]:
        return [json.loads(r["payload"]) for r in self.conn.execute(
            "SELECT payload FROM overlay_notes WHERE name = ?", (normalize_name(name),))]

    def close(self) -> None:
        self.conn.close()
