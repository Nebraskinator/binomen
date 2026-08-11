"""LPSN -- List of Prokaryotic names with Standing in Nomenclature. Tier 3, ICNP.

Why this authority is not optional for bacteria: under ICNP a name has no
standing until it is validly published in IJSEM or on a Validation List. NCBI
and GBIF will both happily return a bacterial name that has never been validly
published, with no indication of the fact. LPSN is the only source that can
answer "does this name have standing?", which is a different question from
"does this name exist?" and a different question again from "is this the
correct name?".

LICENSING: LPSN's API requires registration and its terms restrict bulk
redistribution. binomen therefore queries and cites; it never ships derived
LPSN data. Set BINOMEN_LPSN_USER / BINOMEN_LPSN_PASSWORD to enable. Without
credentials this authority reports itself as not consulted, which is honest and
visible, rather than silently degrading.
"""

from __future__ import annotations

import os

from ..codes import Code, normalize_status
from ..models import Provenance
from ._http import get_json
from .base import AuthorityResult, register

BASE = "https://api.lpsn.dsmz.de"


class LPSN:
    name = "lpsn"
    tier = 3
    codes = (Code.ICNP,)
    license_note = "DSMZ terms; query-and-cite only, derived data not redistributed"
    redistributable = False
    homepage = "https://lpsn.dsmz.de/"

    @property
    def configured(self) -> bool:
        return bool(os.environ.get("BINOMEN_LPSN_USER") and os.environ.get("BINOMEN_LPSN_PASSWORD"))

    def lookup(self, name: str, *, fuzzy: bool = False) -> AuthorityResult:
        if not self.configured:
            return AuthorityResult(
                authority=self.name, found=False,
                error=("not configured: set BINOMEN_LPSN_USER and BINOMEN_LPSN_PASSWORD. "
                       "Without LPSN, ICNP validity status ('validly published' vs 'not "
                       "validly published') is unavailable and must not be asserted."),
            )
        try:
            payload, retrieved, _ = get_json(self.name, f"{BASE}/fetch/{name.replace(' ', '/')}")
        except Exception as e:  # noqa: BLE001
            return AuthorityResult(authority=self.name, found=False, error=f"{type(e).__name__}: {e}")

        results = payload.get("results") or []
        if not results:
            return AuthorityResult(authority=self.name, found=False, match_type="none")
        r = results[0]
        native = r.get("nomenclatural_status") or r.get("status") or "unknown"
        return AuthorityResult(
            authority=self.name,
            found=True,
            accepted_name=r.get("correct_name") or r.get("full_name"),
            identifier=str(r.get("id")) if r.get("id") else None,
            rank=r.get("category"),
            author_citation=r.get("authority"),
            status=normalize_status("lpsn", native),
            provenance=Provenance(
                source="LPSN (DSMZ)", version=r.get("lpsn_taxonomic_status", "live"),
                retrieved=retrieved, url=self.homepage, license=self.license_note,
            ),
            match_type="exact",
            raw=r,
        )


register(LPSN())
