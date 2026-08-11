"""HGNC gene symbols -- Tier 4.

Included to test whether the architecture generalizes past taxonomy. It does,
because the failure is not about biology: a stable-looking string is used as a
join key, the string changes, and nothing in the data announces it.

The septin and MARCH renames of 2020 are the most instructive case in the whole
project. SEPT2 became SEPTIN2 and MARCH1 became MARCHF1 partly because
spreadsheet software silently coerced the old symbols into dates -- a
documented case of a data format corrupting the published record. Supplementary
tables in the literature contain "2-Sep" where a gene symbol should be.

HGNC provides a free REST service and explicitly records `prev_symbol` and
`alias_symbol`, which is exactly the previous-name record that taxonomic
sources make us reconstruct.
"""

from __future__ import annotations

from ..codes import Code, normalize_status
from ..models import Provenance
from ._http import get_json
from .base import AuthorityResult, register

BASE = "https://rest.genenames.org"


class HGNC:
    name = "hgnc"
    tier = 4
    codes = (Code.HGNC,)
    license_note = "HGNC data are freely available; cite genenames.org"
    redistributable = True
    homepage = "https://www.genenames.org/"

    def lookup(self, name: str, *, fuzzy: bool = False) -> AuthorityResult:
        sym = name.strip()
        record, retrieved, how = None, None, None
        for route, label in (("symbol", "current symbol"),
                             ("prev_symbol", "previous symbol"),
                             ("alias_symbol", "alias symbol")):
            try:
                payload, retrieved, _ = get_json(self.name, f"{BASE}/fetch/{route}/{sym}")
            except Exception as e:  # noqa: BLE001
                return AuthorityResult(authority=self.name, found=False, error=f"{type(e).__name__}: {e}")
            docs = ((payload or {}).get("response") or {}).get("docs") or []
            if docs:
                record, how = docs[0], label
                break

        if not record:
            return AuthorityResult(authority=self.name, found=False, match_type="none")

        prev = record.get("prev_symbol") or []
        alias = record.get("alias_symbol") or []
        return AuthorityResult(
            authority=self.name,
            found=True,
            accepted_name=record.get("symbol"),
            identifier=record.get("hgnc_id"),
            rank="gene",
            author_citation=record.get("name"),
            status=normalize_status(
                "hgnc", record.get("status", "Approved"),
                note=f"input matched as {how}" if how != "current symbol" else None,
            ),
            synonyms=[*prev, *alias],
            provenance=Provenance(
                source="HGNC", version=record.get("date_modified") or "live",
                retrieved=retrieved or "unknown", url=self.homepage, license=self.license_note,
                note="date_modified is the record's last change date, HGNC has no global release id",
            ),
            match_type="exact" if how == "current symbol" else "exact_previous",
            raw=record,
        )


register(HGNC())
