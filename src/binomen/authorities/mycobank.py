"""Fungal authorities -- Tier 3, ICNafp.

Fungi are the highest-value Tier 3 group for this project: the changes are
recent, clinically consequential, and in the Candidozyma case actively
disputed, which is the only category where "flag the disagreement" is the
correct answer.

Two ICNafp-specific properties that neither NCBI nor GBIF surfaces well:

  Basionym. Under ICNafp the original name is preserved inside the current
  one via the parenthetical author citation. Candidozyma auris (Satoh &
  Makimura) Liu et al. tells you, in the name itself, that the species was
  first described by Satoh & Makimura in another genus. That is a machine-
  readable change record and it is thrown away by any resolver that returns
  a bare binomial.

  One fungus, one name. Before the 2011 Melbourne Code a fungus could hold two
  legitimate names, one for its asexual (anamorph) and one for its sexual
  (teleomorph) stage. That was abolished wholesale, so a large body of
  mycological literature uses names that are no longer permissible -- not
  because they were wrong, but because the rule that made them valid was
  repealed. NCBI still carries 'anamorph' and 'teleomorph' name classes, which
  we surface rather than flatten to 'synonym'.

LICENSING: MycoBank and Index Fungorum permit querying; bulk redistribution is
not clearly granted. Query-and-cite. Left as a documented extension point --
see docs/EXTENDING.md -- with the interface fully specified so adding it is a
contained change.
"""

from __future__ import annotations

import os

from ..codes import Code, normalize_status
from ..models import Provenance
from ._http import get_json
from .base import AuthorityResult, register

INDEX_FUNGORUM_BASE = "https://www.indexfungorum.org/ixfwebservice/fungus.asmx"
MYCOBANK_BASE = "https://www.mycobank.org/api"


class MycoBank:
    name = "mycobank"
    tier = 3
    codes = (Code.ICNAFP,)
    license_note = "MycoBank terms; query-and-cite only"
    redistributable = False
    homepage = "https://www.mycobank.org/"

    @property
    def configured(self) -> bool:
        return bool(os.environ.get("BINOMEN_MYCOBANK_ENDPOINT"))

    def lookup(self, name: str, *, fuzzy: bool = False) -> AuthorityResult:
        endpoint = os.environ.get("BINOMEN_MYCOBANK_ENDPOINT")
        if not endpoint:
            return AuthorityResult(
                authority=self.name, found=False,
                error=("not configured: set BINOMEN_MYCOBANK_ENDPOINT to a MycoBank-compatible "
                       "JSON endpoint. Without it, basionym and ICNafp nomenclatural status are "
                       "unavailable for fungi and must not be asserted."),
            )
        try:
            payload, retrieved, _ = get_json(self.name, endpoint, {"name": name})
        except Exception as e:  # noqa: BLE001
            return AuthorityResult(authority=self.name, found=False, error=f"{type(e).__name__}: {e}")

        rec = (payload.get("results") or [None])[0]
        if not rec:
            return AuthorityResult(authority=self.name, found=False, match_type="none")
        native = rec.get("nomenclaturalStatus") or "accepted"
        return AuthorityResult(
            authority=self.name,
            found=True,
            accepted_name=rec.get("currentName") or rec.get("name"),
            identifier=str(rec.get("mycobankNumber") or rec.get("id") or ""),
            rank=rec.get("rank"),
            author_citation=rec.get("authors"),
            status=normalize_status("mycobank", native),
            provenance=Provenance(
                source="MycoBank", version=str(rec.get("version", "live")),
                retrieved=retrieved, url=self.homepage, license=self.license_note,
            ),
            match_type="exact",
            raw=rec,
        )


register(MycoBank())
