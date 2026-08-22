"""GBIF Backbone Taxonomy -- Tier 2.

Chosen over Catalogue of Life and Open Tree for one reason: its fuzzy matching
is good, and fuzzy matching is what agents actually need. Agents produce
misspellings, truncations, and names with the authority string glued on. An
exact-match-only resolver returns "not found" for those, and "not found" is
the input most likely to make a model fall back on its own memory.

License: CC BY 4.0. The backbone is redistributable with attribution; we cache
responses rather than redistributing a derived copy.
"""

from __future__ import annotations

from ..codes import Code, normalize_status
from ..models import Provenance
from ._http import get_json
from .base import AuthorityResult, register

BASE = "https://api.gbif.org/v1"


class GBIF:
    name = "gbif"
    tier = 2
    codes = (Code.ICNP, Code.ICNAFP, Code.ICZN, Code.UNDETERMINED)
    license_note = "CC BY 4.0 (attribution required)"
    redistributable = True
    homepage = "https://www.gbif.org/"

    def _prov(self, retrieved: str, dataset_version: str | None = None, note: str | None = None):
        return Provenance(
            source="GBIF Backbone Taxonomy",
            version=dataset_version or "backbone (rolling; GBIF does not expose a build id on /species/match)",
            retrieved=retrieved,
            url=f"{BASE}/species/match",
            license=self.license_note,
            note=note,
        )

    def lookup(self, name: str, *, fuzzy: bool = False) -> AuthorityResult:
        try:
            payload, retrieved, cached = get_json(
                self.name, f"{BASE}/species/match",
                {"name": name, "strict": str(not fuzzy).lower(), "verbose": "true"},
            )
        except Exception as e:  # noqa: BLE001
            return AuthorityResult(authority=self.name, found=False, error=f"{type(e).__name__}: {e}")

        match_type = (payload.get("matchType") or "NONE").lower()
        if match_type == "none" or not payload.get("usageKey"):
            return AuthorityResult(
                authority=self.name, found=False, match_type="none",
                provenance=self._prov(retrieved), raw=payload,
            )

        native = payload.get("status") or payload.get("taxonomicStatus") or "UNKNOWN"
        status = normalize_status("gbif", native)
        lineage = [payload.get(r) for r in
                   ("kingdom", "phylum", "class", "order", "family", "genus")]
        lineage = [x for x in lineage if x]

        # GBIF distinguishes the name you matched from the accepted name it
        # points to -- but /species/match does NOT reliably return an `accepted`
        # field. It usually gives only `status: SYNONYM` plus an
        # `acceptedUsageKey`. The old expression fell through to
        # `canonicalName`, which is the name you asked about, so every synonym
        # was reported as its own accepted name. In a comparison against NCBI
        # that reads as "the authority says this retired name is still current"
        # -- it produced 46 false positives out of 48 in one 100-name run.
        #
        # So: when GBIF says SYNONYM, resolve the key it points at. One extra
        # request, and only on synonyms.
        accepted = payload.get("accepted")
        if not accepted and "synonym" in str(native).lower():
            accepted = self._resolve_accepted(payload.get("acceptedUsageKey"))
        return AuthorityResult(
            authority=self.name,
            found=True,
            # Still no accepted name after resolving? Report None. Never the
            # query: silence is recoverable, a wrong name is not.
            accepted_name=accepted or (None if "synonym" in str(native).lower()
                                       else payload.get("canonicalName")),
            identifier=str(payload.get("acceptedUsageKey") or payload.get("usageKey")),
            rank=(payload.get("rank") or "").lower() or None,
            author_citation=self._authorship(payload),
            status=status,
            lineage=lineage,
            provenance=self._prov(retrieved, note=None if not cached else "served from local cache"),
            match_type="fuzzy" if match_type == "fuzzy" else ("exact" if match_type == "exact" else match_type),
            confidence=(payload.get("confidence") or 0) / 100 if payload.get("confidence") else None,
            raw=payload,
        )

    def _resolve_accepted(self, key) -> str | None:
        """Follow acceptedUsageKey to the name GBIF actually accepts."""
        if not key:
            return None
        try:
            payload, _, _ = get_json(self.name, f"{BASE}/species/{key}")
        except Exception:  # noqa: BLE001
            return None
        return payload.get("canonicalName") or payload.get("scientificName")

    @staticmethod
    def _authorship(payload: dict) -> str | None:
        sci, canon = payload.get("scientificName"), payload.get("canonicalName")
        if sci and canon and sci.startswith(canon) and len(sci) > len(canon):
            return sci[len(canon):].strip() or None
        return None

    def synonyms(self, usage_key: str) -> tuple[list[str], str | None, str | None]:
        """Returns (names, retrieved, error)."""
        try:
            payload, retrieved, _ = get_json(
                self.name, f"{BASE}/species/{usage_key}/synonyms", {"limit": 200})
        except Exception as e:  # noqa: BLE001
            return [], None, f"{type(e).__name__}: {e}"
        names = []
        for r in payload.get("results", []):
            n = r.get("canonicalName") or r.get("scientificName")
            if n:
                names.append(n)
        return names, retrieved, None


register(GBIF())
