"""LPSN -- List of Prokaryotic names with Standing in Nomenclature. Tier 3, ICNP.

Why this authority is not optional for bacteria: under ICNP a name has no
standing until it is validly published in IJSEM or on a Validation List. NCBI
and GBIF will both happily return a bacterial name that has never been validly
published, with no indication. LPSN is the only source that can answer "does
this name have standing?", which is a different question from "does this name
exist?" and a different question again from "is this the correct name?".

LICENSING (checked against https://lpsn.dsmz.de/text/copyright on 2026-08-15;
an earlier draft of this file asserted the opposite and was wrong, which is why
the terms are now quoted rather than summarised):

  licence       CC BY-SA 4.0.
  harvesting    "Automated download of material from LPSN is forbidden unless
                it is done via the LPSN download page or via the LPSN API."
                Both routes are sanctioned; scraping the website is not.
  redistribute  Permitted. "If you redistribute material originating from LPSN
                electronically, you must include a link to the specific LPSN
                page from which this material was obtained."
  display       "If you display information from LPSN about a certain taxon
                name on your website, you must link from this page to the LPSN
                page about that taxon name." Hence the per-taxon `url` in
                Provenance below -- the homepage does not discharge this.
  share-alike   The obligation with teeth. An index that embeds LPSN content is
                an adaptation and must itself be CC BY-SA 4.0. The MIT licence
                on this repository covers source code only (docs/DATA.md
                already draws that line for NCBI); the shipped index needs its
                own licence field once LPSN is baked in.

Registration is free and gives API credentials. Set BINOMEN_LPSN_USER /
BINOMEN_LPSN_PASSWORD. These are needed to BUILD an index, not to use one:
end users of a release should never have to hold credentials. Without them this
authority reports itself as not consulted, which is honest and visible, rather
than silently degrading.

Two things were wrong here until 2026-08-15, and the combination is why this
authority had never once returned a record:

  auth      The first draft assumed HTTP Basic. LPSN uses an OIDC password
            grant against DSMZ's Keycloak, and the bearer token expires after
            about fifteen minutes. Worse, `get_json` had no auth parameter at
            all, so `configured` returned True on the strength of the
            environment variables while the request went out anonymous.

  endpoint  The first draft called `fetch/{genus}/{species}`. `fetch` takes
            LPSN numeric ids, not names; finding the id is a separate
            `advanced_search` call. The wrong URL returned 404, which this
            class reported as "not found" -- indistinguishable from LPSN
            saying the name does not exist.

Both were invisible because nothing ever ran against the live service. The
shapes below are read from DSMZ's own client (`pip install lpsn`), not guessed.
"""

from __future__ import annotations

import os
import time
from urllib.parse import quote

import httpx

from ..codes import Code, normalize_status
from ..models import Provenance
from ._http import get_json
from .base import AuthorityResult, register

BASE = "https://api.lpsn.dsmz.de"
TOKEN_URL = "https://sso.dsmz.de/auth/realms/dsmz/protocol/openid-connect/token"
CLIENT_ID = "api.lpsn.public"

# The token lives ~15 minutes. Refresh a little early rather than discovering
# expiry as a 401 in the middle of a sampling run.
_TOKEN_MARGIN_S = 120


def _taxon_url(r: dict) -> str:
    """Link to the LPSN page this record came from.

    Prefers whatever address the API supplies; falls back to LPSN's slug shape
    only when it does not, because a constructed URL that 404s is worse than a
    generic one for a term that exists to let a reader verify the claim.
    """
    for key in ("lpsn_address", "url", "address"):
        v = (r.get(key) or "").strip()
        if v.startswith("http"):
            return v
    full = (r.get("full_name") or "").strip().lower().split()
    if len(full) == 2:
        return f"https://lpsn.dsmz.de/species/{full[0]}-{full[1]}"
    if len(full) == 1:
        return f"https://lpsn.dsmz.de/genus/{full[0]}"
    return "https://lpsn.dsmz.de/"


def _standing_note(r: dict, extra: str | None = None) -> str | None:
    """ICNP standing and medical-use recommendation, as words."""
    bits = []
    vp = r.get("nomenclatural_status") or r.get("validly_published")
    if vp:
        bits.append(str(vp))
    if (r.get("lorn_status") or "").strip():
        bits.append(f"LPSN medical-use status: {r['lorn_status']}")
    if extra:
        bits.append(extra)
    return "; ".join(bits) or None


class LPSN:
    name = "lpsn"
    tier = 3
    codes = (Code.ICNP,)
    license_note = "CC BY-SA 4.0 (DSMZ/LPSN); redistribution permitted with attribution and a link to the source page; derived indexes inherit share-alike"
    redistributable = False
    homepage = "https://lpsn.dsmz.de/"

    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(os.environ.get("BINOMEN_LPSN_USER") and os.environ.get("BINOMEN_LPSN_PASSWORD"))

    # -- auth ---------------------------------------------------------------
    def _bearer(self) -> str:
        """Fetch or reuse an access token.

        Deliberately not routed through `_http.get_json`: that caches by URL,
        and a credential exchange is the one request in this package that must
        never be written to a cache on disk.
        """
        if self._token and time.time() < self._expires_at:
            return self._token
        r = httpx.post(TOKEN_URL, timeout=30.0, data={
            "grant_type": "password",
            "client_id": CLIENT_ID,
            "username": os.environ["BINOMEN_LPSN_USER"],
            "password": os.environ["BINOMEN_LPSN_PASSWORD"],
        })
        r.raise_for_status()
        payload = r.json()
        self._token = payload["access_token"]
        self._expires_at = time.time() + max(60, int(payload.get("expires_in", 900)) - _TOKEN_MARGIN_S)
        return self._token

    def _call(self, path: str) -> tuple[dict, str]:
        """Returns (payload, retrieved_iso).

        `get_json` returns a 3-tuple, not a payload. Unpacking it here rather
        than at each call site also means provenance carries the timestamp the
        cache recorded, which on a cache hit is when the record was actually
        fetched from DSMZ -- not when this process happened to ask.
        """
        payload, retrieved, _ = get_json(
            self.name, f"{BASE}/{path}",
            headers={"Authorization": f"Bearer {self._bearer()}",
                     "Accept": "application/json"})
        return payload, retrieved

    def _correct_name(self, r: dict) -> tuple[str | None, str | None]:
        """LPSN points at the correct name by ID, never by string.

        There is no `correct_name` field. There is `lpsn_correct_name_id`, an
        integer. When it equals the record's own id the name IS correct; when
        it differs the record is a synonym and the target must be fetched.

        An earlier draft looked for `correct_name`, found nothing, and fell
        back to `full_name` -- which reported every synonym as its own accepted
        name. In a 100-name run that produced 38 rows carrying no information
        and, before that, 46 false "this retired name is still current".
        """
        rid, cid = r.get("id"), r.get("lpsn_correct_name_id")
        if cid is None:
            return (r.get("full_name") or None), None
        if cid == rid:
            return (r.get("full_name") or None), None
        try:
            payload, _ = self._call(f"fetch/{cid}")
        except Exception as e:  # noqa: BLE001
            return None, f"correct name is LPSN id {cid}; could not fetch it ({type(e).__name__})"
        res = payload.get("results") or []
        if isinstance(res, dict):
            res = list(res.values())
        for x in res:
            if isinstance(x, dict) and x.get("id") == cid:
                return (x.get("full_name") or None), None
        return None, f"correct name is LPSN id {cid}, which returned no readable record"

    # -- lookup -------------------------------------------------------------
    def lookup(self, name: str, *, fuzzy: bool = False) -> AuthorityResult:
        if not self.configured:
            return AuthorityResult(
                authority=self.name, found=False,
                error=("not configured: set BINOMEN_LPSN_USER and BINOMEN_LPSN_PASSWORD. "
                       "Without LPSN, ICNP validity status ('validly published' vs 'not "
                       "validly published') is unavailable and must not be asserted."),
            )
        try:
            found, _ = self._call(f"advanced_search?taxon-name={quote(name)}")
        except Exception as e:  # noqa: BLE001
            return AuthorityResult(authority=self.name, found=False,
                                   error=f"search failed: {type(e).__name__}: {e}")

        ids = (found or {}).get("results") or []
        if not ids:
            # LPSN really has no such name. Distinct from a failed request, and
            # for a bacterial name it is itself informative: a name absent from
            # LPSN has no standing under ICNP.
            # Not a coverage gap. Under ICNP a name has no standing until it
            # is validly published, and LPSN is the register of record -- so
            # "no record" is itself the answer, and it is the one answer NCBI
            # cannot give. Obsolete pre-1980 names land here by design:
            # Streptoverticillium salmonicida, Phytomonas campestris,
            # Coccobacillus perfoetens.
            return AuthorityResult(
                authority=self.name, found=False, match_type="none",
                error=None,
                status=normalize_status(
                    "lpsn", "no standing",
                    note="no record in LPSN: not validly published under the ICNP, "
                         "or published under a name LPSN does not index"),
            )

        try:
            payload, retrieved = self._call("fetch/" + ";".join(str(i) for i in ids[:5]))
        except Exception as e:  # noqa: BLE001
            return AuthorityResult(authority=self.name, found=False,
                                   error=f"fetch failed: {type(e).__name__}: {e}")

        results = (payload or {}).get("results") or []
        if isinstance(results, dict):
            results = list(results.values())
        records = [r for r in results if isinstance(r, dict)]
        if not records:
            return AuthorityResult(authority=self.name, found=False, match_type="none")

        # Prefer an exact string hit over whatever the search ranked first;
        # `advanced_search` on a binomial can return infraspecific children.
        r = next((x for x in records
                  if (x.get("full_name") or "").strip().lower() == name.strip().lower()),
                 records[0])
        accepted, accepted_note = self._correct_name(r)
        native = r.get("lpsn_taxonomic_status") or r.get("nomenclatural_status") or "unknown"
        return AuthorityResult(
            authority=self.name,
            found=True,
            # NOT `correct_name or full_name`. On a synonym record LPSN may
            # leave correct_name empty, and falling back to the queried string
            # then reports the synonym as though it were the accepted name --
            # the precise error this package exists to catch, committed by the
            # package. Observed live: Pseudomonas carboxydohydrogena came back
            # "synonym (and not recommended for medical use)" while this field
            # echoed the query.
            accepted_name=accepted,
            identifier=str(r.get("id")) if r.get("id") else None,
            rank=r.get("category"),
            author_citation=r.get("authority"),
            # The two facts only LPSN can supply, promoted out of `raw` into the
            # note: whether the name is validly published under the ICNP, and
            # whether LPSN recommends it for medical use. Both are clean fields
            # on the record -- `nomenclatural_status` and `lorn_status` -- and
            # reading them beats pattern-matching the status prose.
            status=normalize_status("lpsn", native, note=_standing_note(r, accepted_note)),
            provenance=Provenance(
                source="LPSN (DSMZ)", version=str(native),
                retrieved=retrieved,
                # The specific taxon page, not the homepage. LPSN's terms
                # require a link to the page the material came from, and a
                # generic homepage link does not satisfy that -- so this is a
                # licence obligation wearing the clothes of a provenance field.
                url=_taxon_url(r),
                license=self.license_note,
            ),
            match_type="exact",
            raw=r,
        )


register(LPSN())
