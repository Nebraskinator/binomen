"""ICTV Master Species List -- Tier 3, viruses.

Viruses are the clearest illustration of the four-codes argument. ICTV taxonomy
is not an accumulation of individual publications adjudicated by priority; it
is a versioned artifact released annually as a numbered Master Species List
(MSL). The right provenance string for a virus name is therefore not a year and
an author, it is an MSL number -- "as of MSL39" is a complete and checkable
statement in a way that "currently" never is.

The binomial rollout means a large fraction of virus species names changed by
committee decision rather than by any new evidence about the viruses. An agent
that reasons "the name changed, so the taxonomy must have been revised" draws
the wrong inference here.

Ships as a loader for a locally downloaded MSL spreadsheet rather than a live
client: ICTV publishes the MSL as a versioned file, and pinning the version is
the entire point. Point BINOMEN_ICTV_MSL at the downloaded file.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from ..codes import Code, normalize_status
from ..models import Provenance
from .base import AuthorityResult, register


class ICTV:
    name = "ictv"
    tier = 3
    codes = (Code.ICTV,)
    license_note = "ICTV publishes the MSL openly; cite the MSL number"
    redistributable = True
    homepage = "https://ictv.global/msl"

    def __init__(self) -> None:
        self._table: dict[str, dict] | None = None
        self._version = "not loaded"

    @property
    def configured(self) -> bool:
        return bool(os.environ.get("BINOMEN_ICTV_MSL"))

    def _load(self) -> dict[str, dict]:
        if self._table is not None:
            return self._table
        path = os.environ.get("BINOMEN_ICTV_MSL")
        self._table = {}
        if not path or not Path(path).exists():
            return self._table
        p = Path(path)
        self._version = os.environ.get("BINOMEN_ICTV_VERSION") or p.stem
        with open(p, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                key = (row.get("Species") or row.get("species") or "").strip().lower()
                if key:
                    self._table[key] = row
        return self._table

    def lookup(self, name: str, *, fuzzy: bool = False) -> AuthorityResult:
        table = self._load()
        if not table:
            return AuthorityResult(
                authority=self.name, found=False,
                error=("not configured: download an ICTV Master Species List and set "
                       "BINOMEN_ICTV_MSL (and optionally BINOMEN_ICTV_VERSION, e.g. 'MSL39'). "
                       "Without it, virus names cannot be pinned to a release."),
            )
        row = table.get(name.strip().lower())
        if not row:
            return AuthorityResult(authority=self.name, found=False, match_type="none")
        return AuthorityResult(
            authority=self.name,
            found=True,
            accepted_name=row.get("Species") or name,
            identifier=row.get("Sort") or row.get("Taxon History URL"),
            rank="species",
            status=normalize_status("ictv", "current"),
            lineage=[row.get(r, "") for r in ("Realm", "Kingdom", "Phylum", "Class", "Order", "Family", "Genus") if row.get(r)],
            provenance=Provenance(
                source="ICTV Master Species List", version=self._version,
                retrieved="from local MSL file", url=self.homepage, license=self.license_note,
                note="ICTV taxonomy is committee-assigned per annual release; cite the MSL number, not a year.",
            ),
            match_type="exact",
            raw=row,
        )


register(ICTV())
