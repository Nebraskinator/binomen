"""Three-stage resolver.

    stage 1  check_name            local, ~2 ms, ~20 tokens   always callable
    stage 2  resolve_name & co.    local, ~50 ms, ~250 tokens reached by escalation
    stage 3  consult_authorities   network, ~1 s, variable    reached by escalation

The escalation contract is the mechanism: each stage returns `escalate`,
`reason`, and `next`. **The data decides the policy, not the model.** An agent
does not have to judge whether a given name is worth a closer look -- stage 1
tells it, from the index. That is also what makes it honest to describe stage 1
as "call this whenever an organism name appears": it is cheap enough that the
advice costs nothing to follow.

Three rules still govern every return value:

1. Nothing is generated. Every value is a lookup or a rule applied to versioned
   reference data. Where we do not know something the field is null and a
   warning says why. A fabricated authority or year is worse than a missing one
   because it is citable.
2. Disagreement is never collapsed. There is deliberately no top-level
   `current_name` string; if one existed every caller would read it and the
   contested cases would silently degrade to whichever answer sorted first.
3. Provenance rides with the value. A response assembled from three sources
   carries three provenance records.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import date

from .authorities import authorities_for
from .authorities.base import AuthorityResult
from .build.build_index import (
    is_bracketed,
    split_abbreviation,
    split_designation,
    strip_authority,
)
from .codes import (
    CODE_DESCRIPTIONS,
    Code,
    CodeAssignment,
    Status,
    TaxonStatus,
    detect_code,
    normalize_status,
    status_vocabulary_for,
)
from .db import DEFAULT_DB, Backbone, IndexNotBuilt, IndexStale, Stage1
from .models import Candidate, ChangeEvent, Provenance, Resolution

GENE_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9\-]{1,14}$")


def _capitalise_genus(norm: str) -> str:
    """Restore display form from a folded lookup key.

    Only the first letter: a binomial is a capitalised genus and a lowercase
    epithet, so `.title()` would corrupt every one of them.
    """
    return norm[:1].upper() + norm[1:] if norm else norm
# Open-nomenclature qualifiers and strain designations. Real strings in NCBI,
# useless as literature search terms.
_UNSEARCHABLE = re.compile(
    r"\b(sp|cf|aff|str|subsp\. \w+ str|nr)\.|\b(strain|isolate|clone|serovar)\b", re.I)

# Short, fixed strings instead of the paragraphs the previous version emitted
# inline on every response. Same information, ~10x fewer tokens.
_REASON = {
    "superseded": "recorded as a synonym; the accepted name differs",
    "has_synonyms": "accepted, but other names exist for this taxon",
    "homonym": "this string denotes more than one taxon",
    "contested": "authorities disagree about the accepted name",
    "unknown": "no record of this name in the indexed release",
    "multi_code": "matched under more than one nomenclatural code",
}


@dataclass
class _Resolved:
    query: str
    taxid: int | None
    matched_name: str | None
    accepted_names: list[str]
    code: Code
    created: float
    # Set only when the query was a strain name that the index did not contain
    # and we resolved it through its binomial. In that case, and only that case,
    # search terms need the designation reattached -- if the index held the
    # strain directly, its synonyms already carry it.
    designation: str | None = None


class Resolver:
    def __init__(self, backbone: Backbone | None = None, stage1: Stage1 | None = None,
                 *, use_live: bool = True, max_tier: int = 4):
        # Stage 1 is required; stage 2 is optional and degrades honestly.
        self.s1: Stage1 | None
        self._s1_error: str | None = None
        try:
            self.s1 = stage1 or Stage1()
        except (IndexNotBuilt, IndexStale) as e:
            self.s1 = None
            self._s1_error = str(e)
        self._db: Backbone | None = backbone
        self._db_error: str | None = None
        if self._db is None:
            try:
                self._db = Backbone()
            except (IndexNotBuilt, IndexStale) as e:
                self._db_error = str(e)
        self.use_live = use_live
        self.max_tier = max_tier
        self._resolutions: dict[str, _Resolved] = {}

    @property
    def db(self) -> Backbone:
        if self._db is None:
            raise IndexNotBuilt(DEFAULT_DB)
        return self._db

    @property
    def has_stage2(self) -> bool:
        return self._db is not None

    # ================================================================ stage 1
    def check_name(self, name: str) -> dict:
        """Is there anything about this name that needs a closer look?

        The whole point is that the usual answer is no, and saying no costs
        almost nothing. For a stable name the accepted name *is* the input, so
        there is nothing to return but the verdict and the code.
        """
        q = (name or "").strip()
        if not q:
            return {"name": q, "verdict": "unknown", "escalate": False,
                    "reason": "empty query"}
        if self.s1 is None:
            return {"name": q, "verdict": "unavailable", "escalate": True,
                    "next": "resolve_name",
                    "reason": "stage-1 index not built; run binomen-build-index",
                    "do_not": "Do not answer from memory in the meantime."}

        rows = self.s1.verdict(q)
        if not rows and not self.s1.codes_matching(q):
            # Abbreviation before strain. "C. difficile 630" is both shapes at
            # once, and the abbreviated genus is the part that cannot be looked
            # up as written, so it has to be settled first.
            abbrev = self._check_abbreviation(q)
            if abbrev:
                return abbrev
            # Might be a strain: binomial + laboratory designation. The
            # designation is not governed by any code and never changes; the
            # binomial is what moves, and every strain inherits its species'
            # transfer. Resolve the species and carry the suffix through.
            strain = self._check_strain(q)
            if strain:
                return strain
        if rows:
            r = rows[0]
            out = {"name": q, "verdict": r["verdict"], "code": r["code"],
                   "escalate": True, "reason": _REASON[r["verdict"]],
                   "next": "consult_authorities" if r["verdict"] == "contested"
                           else "resolve_name"}
            # One extra field, only where it saves the caller a whole stage-2
            # round trip: a plain superseded name whose replacement we know.
            if r["verdict"] == "superseded" and r["accepted"]:
                out["accepted_name"] = r["accepted"]
            return out

        codes = self.s1.codes_matching(q)
        if not codes:
            return {"name": q, "verdict": "unknown", "escalate": True,
                    "next": "consult_authorities",
                    "reason": _REASON["unknown"],
                    "do_not": ("Do not substitute a name you remember. The string may be a "
                               "misspelling, an infraspecific or strain designation, or newer "
                               f"than {self.s1.meta.get('version', 'the indexed release')}.")}
        if len(codes) > 1:
            return {"name": q, "verdict": "ambiguous", "codes": codes, "escalate": True,
                    "next": "resolve_name", "reason": _REASON["multi_code"]}
        return {"name": q, "verdict": "stable", "code": codes[0], "escalate": False,
                "as_of": self.s1.meta.get("version", "unknown")}

    _ABBREV_COVERAGE = (
        "Expansions cover names with recorded nomenclatural history. A binomial that has "
        "never moved is certified by a filter that cannot be enumerated, so it would not "
        "appear here -- absence from this list is not evidence of absence from the index.")

    def _check_abbreviation(self, q: str) -> dict | None:
        """Resolve an abbreviated genus by enumerating what it could stand for.

        The input schema has always promised this form and the lookup has never
        supported it: `check_name("C. difficile")` returned `unknown` alongside
        "Do not substitute a name you remember", which is the worst available
        answer -- indistinguishable from "no such organism" and offering no way
        forward. It also quietly wasted real invocations, since a model reaching
        for this tool writes "E. coli" about as often as it writes the binomial.

        Enumerating rather than guessing is the point. "S. aureus" matches
        twelve binomials in NCBI including a plant (Senecio), a fish (Stegastes)
        and a pothos (Scindapsus). Everyone reads it as Staphylococcus. That is
        the same conflation this package exists to catch, running in the
        opposite direction: one string, several organisms.
        """
        split = split_abbreviation(q)
        if not split:
            return None
        assert self.s1 is not None
        names = self.s1.expand_abbreviation(*split)
        if not names:
            return None

        if len(names) > 1:
            return {
                "name": q, "verdict": "ambiguous_abbreviation", "escalate": True,
                "next": "resolve_name",
                "reason": f"abbreviated genus; {len(names)} names in the index match it",
                "expansions": [_capitalise_genus(n) for n in names],
                "coverage_warning": self._ABBREV_COVERAGE,
                "do_not": ("Do not assume the familiar expansion. Abbreviations collide "
                           "across kingdoms, and the reader's organism may not be yours."),
            }

        expanded = names[0]
        via = {"abbreviation": q, "expanded": _capitalise_genus(expanded)}
        rows = self.s1.verdict(expanded)
        if not rows:
            codes = self.s1.codes_matching(expanded)
            if len(codes) != 1:
                return None
            return {"name": q, "verdict": "stable", "code": codes[0], "escalate": False,
                    "resolved_via": via, "coverage_warning": self._ABBREV_COVERAGE,
                    "as_of": self.s1.meta.get("version", "unknown")}
        r = rows[0]
        out = {"name": q, "verdict": r["verdict"], "code": r["code"], "escalate": True,
               "reason": _REASON[r["verdict"]],
               "next": "consult_authorities" if r["verdict"] == "contested"
                       else "resolve_name",
               "resolved_via": via, "coverage_warning": self._ABBREV_COVERAGE}
        if r["verdict"] == "superseded" and r["accepted"]:
            out["accepted_name"] = r["accepted"]
        return out

    def _check_strain(self, q: str) -> dict | None:
        """Resolve a strain name via its binomial, recombining the designation."""
        split = split_designation(q)
        if not split:
            return None
        binomial, designation = split
        assert self.s1 is not None
        rows = self.s1.verdict(binomial)
        if not rows:
            codes = self.s1.codes_matching(binomial)
            if len(codes) != 1:
                return None
            return {"name": q, "verdict": "stable", "code": codes[0], "escalate": False,
                    "resolved_via": {"binomial": binomial, "designation": designation},
                    "note": "resolved through the species name; the strain designation "
                            f"'{designation}' is not governed by a nomenclatural code",
                    "as_of": self.s1.meta.get("version", "unknown")}
        r = rows[0]
        out = {"name": q, "verdict": r["verdict"], "code": r["code"], "escalate": True,
               "reason": _REASON[r["verdict"]],
               "next": "consult_authorities" if r["verdict"] == "contested" else "resolve_name",
               "resolved_via": {"binomial": binomial, "designation": designation}}
        if r["verdict"] == "superseded" and r["accepted"]:
            # Constructed, not looked up -- and labelled as such. The rule is
            # exact (a strain keeps its designation through a transfer) but it
            # is a rule application, not a record, and the two should not be
            # presented identically.
            out["accepted_name"] = f"{r['accepted']} {designation}"
            out["note"] = (
                f"the species was transferred; the strain designation '{designation}' is "
                f"unchanged. This strain name is derived from the species record, not read "
                f"from one -- searches should cover both forms.")
        return out

    # ================================================================ stage 2
    def _looks_like_gene(self, name: str) -> bool:
        """Heuristic, and reported as one. A gene symbol and an abbreviated
        genus are not reliably separable from the string alone."""
        return " " not in name.strip() and bool(GENE_SYMBOL_RE.match(name.strip()))

    def _handle(self, r: _Resolved) -> str:
        h = hashlib.sha1(f"{r.query}|{r.taxid}|{r.created}".encode()).hexdigest()[:12]
        self._resolutions[h] = r
        return h

    def _no_stage2(self, name: str) -> Resolution:
        return Resolution(
            query=name, matched_name=None, match_type="none",
            governing_code=detect_code([]), candidates=[], contested=False,
            change_chain=[], provenance=[],
            warnings=[f"full index not installed. {self._db_error or ''} "
                      "check_name still works; consult_authorities can answer over the network.",
                      "Do not substitute a remembered name."])

    def resolve_name(self, name: str, group_hint: str | None = None) -> Resolution:
        """Local resolution against the full index. No network.

        Network authorities moved to `consult_authorities` (stage 3) so that
        this call has a predictable cost. Mixing a local lookup and four HTTP
        requests behind one tool made latency depend on the name, which is a bad
        property for something an agent is supposed to call freely.
        """
        name = (name or "").strip()
        if not name:
            return self._no_stage2(name) if not self.has_stage2 else Resolution(
                query=name, matched_name=None, match_type="none",
                governing_code=detect_code([]), candidates=[], contested=False,
                change_chain=[], provenance=[], warnings=["empty query"])
        if not self.has_stage2:
            return self._no_stage2(name)

        warnings: list[str] = []
        provs: list[Provenance] = []
        changes: list[ChangeEvent] = []
        consulted = ["NCBI Taxonomy"]

        rows = self.db.lookup(name)
        if not rows and self._looks_like_gene(name):
            return self._resolve_gene(name)

        # Strain fallback, as in check_name: resolve the binomial and carry the
        # designation through rather than failing on a name the index will
        # never contain.
        strain_designation: str | None = None
        if not rows:
            split = split_designation(name)
            if split and self.db.lookup(split[0]):
                strain_designation = split[1]
                warnings.append(
                    f"'{name}' was resolved through its species name '{split[0]}'; the "
                    f"designation '{split[1]}' is a laboratory identifier, not governed by any "
                    f"nomenclatural code. Names below are the species records; append the "
                    f"designation to each for the strain-level form.")
                rows = self.db.lookup(split[0])

        match_type = "exact" if rows else "none"
        if not rows:
            near = self.db.prefix_lookup(name, limit=5)
            if near:
                warnings.append("no exact match; nearest index entries: "
                                + ", ".join(sorted({r["name"] for r in near})))

        # NCBI's square brackets are an editorial signal, not noise: they mark
        # a species whose current generic placement is known to be wrong. That
        # is advance warning of a rename, so it is reported rather than folded
        # away with the characters.
        bracketed = [r["name"] for r in rows if is_bracketed(r["name"])]
        if bracketed:
            warnings.append(
                f"the source records this name as {bracketed[0]!r}: NCBI brackets a genus when "
                "the species is known to be misplaced in it. The generic placement is considered "
                "wrong and is a candidate for future reassignment, even where no formal transfer "
                "has been published yet.")

        taxids = sorted({r["taxid"] for r in rows})
        if len(taxids) > 1:
            warnings.append(
                f"homonym: '{name}' denotes {len(taxids)} distinct taxa "
                f"(txid {', '.join(str(t) for t in taxids)}). All returned; the correct one "
                f"depends on which is meant.")

        candidates: list[Candidate] = []
        input_statuses: list[TaxonStatus] = []
        code_assignment = detect_code([])
        primary_taxid: int | None = None
        prov = self.db.provenance()
        provs.append(prov)

        for taxid in taxids:
            live, was_merged = self.db.resolve_taxid(taxid)
            if primary_taxid is None:
                primary_taxid = live
            node = self.db.node(live)
            # Precomputed code column, not a lineage walk.
            code_str = node.code if node else Code.UNDETERMINED.value
            if code_assignment.code is Code.UNDETERMINED:
                code_assignment = CodeAssignment(
                    Code(code_str), "certain" if code_str != Code.UNDETERMINED.value
                    else "undetermined",
                    evidence="precomputed at build time from the lineage anchor")
            accepted = self.db.scientific_name(live) or name
            matched = next((r for r in rows if r["taxid"] == taxid), None)
            native_class = matched["name_class"] if matched else "scientific name"

            # Two distinct statuses. Conflating them yields the reading
            # "Clostridioides difficile is a synonym", which is backwards.
            ist = normalize_status("ncbi", native_class)
            if was_merged and ist.normalized is Status.ACCEPTED:
                ist = TaxonStatus(Status.MERGED, native_class, "ncbi",
                                  note="the taxid this name resolved to was merged away")
            input_statuses.append(ist)

            candidates.append(Candidate(
                accepted_name=accepted,
                identifier=f"NCBI:txid{live}",
                rank=node.rank if node else None,
                authority=self.db.authority(live),
                status=TaxonStatus(Status.ACCEPTED, "scientific name", "ncbi"),
                provenance=prov,
                supporting_sources=["NCBI Taxonomy"],
                disambiguation=self.db.unique_name(live) if len(taxids) > 1 else None,
                lineage_summary=self.db.lineage_names(live)[1:6] if len(taxids) > 1 else [],
            ))

            if was_merged:
                changes.append(ChangeEvent(
                    from_name=f"NCBI:txid{taxid}", to_name=f"NCBI:txid{live}", kind="merge",
                    note="merged.dmp; taxdump records the merge but not its date"))
            if accepted.lower() != name.lower():
                changes.append(ChangeEvent(from_name=name, to_name=accepted, kind="synonymy",
                                           note=f"input name class: {native_class}"))

        # curated overlay
        contested = False
        for entry in _dedupe_overlay(self.db.overlay(name)):
            consulted.append("binomen overlay")
            oprov = Provenance(
                source="binomen curated overlay",
                version=self.db.meta.get("overlay_version", "0.1.0"),
                retrieved=self.db.meta.get("retrieved", "unknown"),
                note=f"confidence: {entry.get('confidence', 'unverified')} -- "
                     "unverified entries must not be used for reported results")
            provs.append(oprov)
            if entry.get("contested"):
                contested = True
                existing = {c.accepted_name for c in candidates}
                st = TaxonStatus(Status.CONTESTED, "contested", "binomen overlay")
                for cand in entry.get("candidates", []):
                    if cand["accepted_name"] in existing:
                        for c in candidates:
                            if c.accepted_name == cand["accepted_name"]:
                                c.status = st
                                c.supporting_sources += cand.get("supporting_sources", [])
                                c.argument = cand.get("argument")
                        continue
                    candidates.append(Candidate(
                        accepted_name=cand["accepted_name"], identifier=None, rank=None,
                        authority=None, status=st, provenance=oprov,
                        supporting_sources=cand.get("supporting_sources", []),
                        argument=cand.get("argument")))
                warnings.append("AUTHORITIES DISAGREE. " + entry.get("guidance", ""))
            elif entry.get("guidance"):
                warnings.append(entry["guidance"])
            for step in entry.get("chain", []):
                changes.append(ChangeEvent(from_name=step["from"], to_name=step["to"],
                                           kind=step.get("kind", "rename"), year=step.get("year")))
            if entry.get("references"):
                changes.append(ChangeEvent(
                    from_name=name, to_name=entry.get("current_name") or name,
                    kind=entry.get("change_kind", "rename"), year=entry.get("year"),
                    reference="; ".join(entry["references"])))

        if not candidates:
            warnings.append(
                f"'{name}' not found in {self.db.meta.get('version', 'the indexed release')}. "
                "May be a misspelling, an infraspecific or strain name, or newer than the index. "
                "Do not substitute a remembered name; try consult_authorities.")

        res = Resolution(
            query=name,
            matched_name=(rows[0]["name"] if rows else None),
            match_type=match_type,
            governing_code=code_assignment,
            candidates=candidates,
            contested=contested,
            change_chain=changes,
            provenance=provs,
            consulted_sources=sorted(set(consulted)),
            warnings=warnings,
            input_status=input_statuses[0] if input_statuses else None,
        )
        if candidates:
            res.resolution_id = self._handle(_Resolved(
                query=name, taxid=primary_taxid, matched_name=res.matched_name,
                accepted_names=[c.accepted_name for c in candidates],
                code=code_assignment.code, created=time.time(),
                designation=strain_designation))
        if contested:
            res.warnings.append("Call consult_authorities for the primary sources on both sides.")
        return res

    def _resolve_gene(self, symbol: str) -> Resolution:
        warnings = [
            f"'{symbol}' treated as a gene symbol: it matches the HGNC symbol shape and did not "
            "resolve as an organism. If an organism was meant, re-query with the full binomial -- "
            "an abbreviated genus cannot be distinguished from a symbol.",
            "Gene symbols are resolved over the network: call consult_authorities.",
        ]
        candidates, provs, changes = [], [], []
        for entry in _dedupe_overlay(self.db.overlay(symbol)):
            oprov = Provenance(source="binomen curated overlay", version="0.1.0",
                               retrieved=self.db.meta.get("retrieved", "unknown"),
                               note=f"confidence: {entry.get('confidence', 'unverified')}")
            provs.append(oprov)
            if entry.get("guidance"):
                warnings.append(entry["guidance"])
            if entry.get("current_name"):
                candidates.append(Candidate(
                    accepted_name=entry["current_name"], identifier=None, rank="gene",
                    authority=None, status=normalize_status("hgnc", "Approved"),
                    provenance=oprov, supporting_sources=["binomen curated overlay"]))
                changes.append(ChangeEvent(from_name=symbol, to_name=entry["current_name"],
                                           kind="rename", year=entry.get("year"),
                                           reference="; ".join(entry.get("references", []))))
        res = Resolution(query=symbol, matched_name=symbol,
                         match_type="exact" if candidates else "none",
                         governing_code=detect_code([], is_gene=True), candidates=candidates,
                         contested=False, change_chain=changes, provenance=provs,
                         consulted_sources=["binomen overlay"], warnings=warnings)
        if candidates:
            res.resolution_id = self._handle(_Resolved(
                query=symbol, taxid=None, matched_name=symbol,
                accepted_names=[c.accepted_name for c in candidates],
                code=Code.HGNC, created=time.time()))
        return res

    # ================================================================ stage 3
    def consult_authorities(self, name: str, question: str = "current_name",
                            as_of: str | None = None) -> dict:
        """Query the live code-specific authorities. Network, slow, rate-limited.

        This is the only tool that leaves the machine. It exists because three
        classes of question cannot be answered locally at all:

          validity  ICNP standing ("validly published") is an LPSN question.
                    NCBI will happily return a name that has never been validly
                    published, with no indication.
          as-of     taxdump records that names changed, not when. Dates need a
                    code-specific authority.
          contested whether a synonymy is disputed is not expressible in taxdump.
        """
        code = Code.UNDETERMINED
        if self.has_stage2:
            rows = self.db.lookup(name)
            if rows:
                t, _ = self.db.resolve_taxid(rows[0]["taxid"])
                code = Code(self.db.code_for(t) or Code.UNDETERMINED.value)
        elif self.s1:
            v = self.s1.verdict(name)
            if v:
                code = Code(v[0]["code"])
            else:
                c = self.s1.codes_matching(name)
                if len(c) == 1:
                    code = Code(c[0])
        if code is Code.UNDETERMINED and self._looks_like_gene(name):
            code = Code.HGNC

        results, provs, warnings = [], [], []
        if not self.use_live:
            warnings.append("live queries disabled (BINOMEN_OFFLINE / use_live=False); "
                            "serving cached entries only")
        for auth in authorities_for(code, max_tier=self.max_tier):
            try:
                r = auth.lookup(name, fuzzy=True)
            except Exception as e:  # noqa: BLE001
                r = AuthorityResult(authority=auth.name, found=False,
                                    error=f"{type(e).__name__}: {e}")
            if r.provenance:
                provs.append(r.provenance.to_dict())
            if r.error:
                # "not consulted" and "not found" are different facts. Collapsing
                # them would be the same silent failure this project is about.
                warnings.append(f"{auth.name}: NOT CONSULTED -- {r.error}")
                continue
            results.append({
                "authority": r.authority, "found": r.found,
                "accepted_name": r.accepted_name, "identifier": r.identifier,
                "rank": r.rank, "author_citation": r.author_citation,
                "status": r.status.to_dict() if r.status else None,
                "match_type": r.match_type, "confidence": r.confidence,
            })

        names = {r["accepted_name"] for r in results if r.get("accepted_name")}
        overlay = _dedupe_overlay(self.db.overlay(name)) if self.has_stage2 else []
        # Fold in the curated candidates. Reporting `contested: true` next to a
        # one-element `distinct_accepted_names` invites a caller to read the
        # list, see one answer, and ignore the flag -- the same mistake as
        # having a top-level current_name field.
        for e in overlay:
            names.update(c["accepted_name"] for c in e.get("candidates", [])
                         if c.get("accepted_name"))
        if self.has_stage2:
            for row in self.db.lookup(name):
                acc = self.db.scientific_name(self.db.resolve_taxid(row["taxid"])[0])
                if acc:
                    names.add(acc)
        contested = any(e.get("contested") for e in overlay) or len(names) > 1

        out = {
            "query": name, "governing_code": code.value, "question": question,
            "as_of": as_of or date.today().isoformat(),
            "authorities": results,
            "contested": contested,
            "distinct_accepted_names": sorted(names),
            "warnings": warnings,
            "provenance": provs,
        }
        if overlay:
            out["curated_notes"] = [
                {"guidance": e.get("guidance"), "references": e.get("references", []),
                 "confidence": e.get("confidence", "unverified"),
                 "candidates": e.get("candidates", [])} for e in overlay]
        if contested:
            out["warnings"].insert(0, "AUTHORITIES DISAGREE. Report both, attribute each, and "
                                      "say which you are following. A single answer is wrong here "
                                      "however authoritative it sounds.")
        if not results and not overlay:
            out["warnings"].append(
                "No authority returned a match. This is not evidence the organism does not exist. "
                "Do not supply a name from memory.")
        return out

    # ------------------------------------------------- supporting stage-2 tools
    def check_currency(self, name: str, as_of: str | None = None) -> dict:
        if not self.has_stage2:
            return {"query": name, "error": "full index not installed",
                    "next": "consult_authorities"}
        r = self.resolve_name(name)
        today = date.today().isoformat()
        as_of = as_of or today
        dated = [e for e in r.change_chain if e.year]
        accepted_now = [c.accepted_name for c in r.candidates
                        if c.status.normalized in {Status.ACCEPTED, Status.CONTESTED}]
        is_current = any(a.lower() == name.strip().lower() for a in accepted_now)
        out = {
            "query": name, "as_of": as_of, "asked_about_the_past": as_of < today,
            "is_current_accepted_name": is_current, "contested": r.contested,
            "accepted_names_now": accepted_now,
            "governing_code": r.governing_code.to_dict(),
            "as_of_answerable": bool(dated),
            "dated_events": [e.to_dict() for e in dated],
            "warnings": list(r.warnings),
            "provenance": [p.to_dict() for p in r.provenance],
        }
        if not dated:
            out["warnings"].append(
                f"No dated event available, so 'was it accepted on {as_of}?' cannot be answered "
                "from the local index. taxdump records that names changed, not when. "
                "Call consult_authorities.")
            out["next"] = "consult_authorities"
        if not is_current and accepted_now:
            out["warnings"].insert(0, f"'{name}' is NOT the accepted name. Superseded by: "
                                      + ", ".join(accepted_now))
        return out

    def get_synonyms(self, name: str) -> dict:
        if not self.has_stage2:
            return {"query": name, "error": "full index not installed",
                    "next": "consult_authorities"}
        r = self.resolve_name(name)
        groups: dict[str, list[dict]] = {}
        taxids = {int(c.identifier.split("txid")[1]) for c in r.candidates
                  if c.identifier and "txid" in c.identifier}
        for taxid in taxids:
            for row in self.db.names_for(taxid):
                if row["name_class"] == "authority":
                    continue
                st = normalize_status("ncbi", row["name_class"])
                groups.setdefault(st.normalized.value, []).append(
                    {"name": row["name"], "native_class": row["name_class"]})
        merged = [t for taxid in taxids for t in self.db.merged_from(taxid)]
        return {
            "query": name, "governing_code": r.governing_code.to_dict(),
            "contested": r.contested, "by_synonymy_type": groups,
            # by_synonymy_type keeps the verbatim source strings; all_names is
            # the usable list, with author citations stripped.
            "all_names": sorted({
                (strip_authority(e["name"]) or e["name"]).replace("[", "").replace("]", "").strip()
                for g in groups.values() for e in g if e.get("name")}),
            "note_on_strings": "Names in by_synonymy_type are verbatim from the source and may "
                               "include the full author citation; all_names is the bare form.",
            "merged_identifiers": [f"NCBI:txid{t}" for t in merged],
            "note": "NCBI does not distinguish homotypic from heterotypic synonyms. "
                    "If that distinction matters, call consult_authorities.",
            "warnings": r.warnings,
            "provenance": [p.to_dict() for p in r.provenance],
        }

    def expand_query(self, resolution_id: str, include_vernacular: bool = False) -> dict:
        """Gated behind a resolution_id -- instrumentation, not security.

        Query expansion without resolved identity is a list of remembered names.
        The gate forces a multi-step episode and lets the harness observe whether
        the agent resolved identity or jumped to an answer.
        """
        r = self._resolutions.get(resolution_id)
        if not r:
            return {"error": "unknown or expired resolution_id",
                    "remedy": "call resolve_name first and pass the resolution_id it returns"}
        syn = self.get_synonyms(r.query)
        raw = {r.query, *(r.accepted_names or [])}
        for group, entries in syn.get("by_synonymy_type", {}).items():
            if group in {"unknown", "includes"} and not include_vernacular:
                continue
            raw.update(e["name"] for e in entries if e.get("name"))

        # Search terms must be bare binomials. NCBI stores many synonyms as
        # full nomenclatural citations -- "Clostridium difficile (Hall and
        # O'Toole 1935) Prevot 1938 (Approved Lists 1980)" -- and pasting that
        # into PubMed returns nothing. Which would be a query-expansion tool
        # producing a silently empty search: the exact failure it exists to
        # prevent. The citation stays in get_synonyms, where it is evidence
        # rather than input.
        # `includes` entries are unidentified material filed under the taxon,
        # not names for it. They must never reach a search string.
        raw -= {e["name"] for e in syn.get("by_synonymy_type", {}).get("includes", [])
                if e.get("name")}

        node = self.db.node(r.taxid) if r.taxid else None
        code = node.code if node else None
        rank = node.rank if node else None
        terms = set()
        for t in raw:
            if not t:
                continue
            terms.add(strip_authority(t, code, rank) or t)
        terms = {t.replace("[", "").replace("]", "").strip() for t in terms if t}
        # Second guard: anything still carrying an open-nomenclature qualifier
        # or a strain designation is not a name a literature search should use.
        terms = {t for t in terms if not _UNSEARCHABLE.search(t)}
        abbreviated = {f"{p[0][0]}. {' '.join(p[1:])}" for p in
                       (t.split() for t in terms) if len(p) >= 2 and p[0][:1].isupper()}
        # Reattach the designation only when we resolved through the binomial.
        # Appending it unconditionally produced "Clostridioides difficile 630 630"
        # for strains the index already knew about.
        if r.designation:
            terms = {f"{t} {r.designation}" for t in terms} | terms

        ordered = sorted(terms)
        return {
            "query": r.query, "search_terms": ordered,
            "abbreviated_forms": sorted(abbreviated),
            "boolean_query": " OR ".join(f'"{t}"' for t in ordered),
            "pubmed_query": " OR ".join(f'"{t}"[tiab]' for t in ordered),
            "note": "Author citations and NCBI misplacement brackets are stripped from search "
                    "terms; a citation pasted into a literature search matches nothing. The "
                    "verbatim source strings are in get_synonyms.",
            "coverage_warning": (
                f"Searching the current name alone would have missed {len(ordered)-1} variant(s). "
                "Covers names recorded by the local index only; not informal usage, unindexed "
                f"misspellings, or changes after {self.db.meta.get('version', 'the release')}."),
            "provenance": syn.get("provenance", []),
        }

    def compare_names(self, name_a: str, name_b: str) -> dict:
        """Same taxon or not. The tool whose errors are graded 'very major':
        both directions destroy data silently."""
        if not self.has_stage2:
            return {"name_a": name_a, "name_b": name_b, "same_taxon": None,
                    "error": "full index not installed", "next": "consult_authorities"}
        ra, rb = self.resolve_name(name_a), self.resolve_name(name_b)
        ta = {int(c.identifier.split("txid")[1]) for c in ra.candidates
              if c.identifier and "txid" in c.identifier}
        tb = {int(c.identifier.split("txid")[1]) for c in rb.candidates
              if c.identifier and "txid" in c.identifier}
        result: dict = {
            "name_a": name_a, "name_b": name_b, "same_taxon": bool(ta & tb),
            "confidence": "high" if (ta and tb) else "low",
            "governing_code_a": ra.governing_code.to_dict(),
            "governing_code_b": rb.governing_code.to_dict(),
            "contested": ra.contested or rb.contested,
            "warnings": list(dict.fromkeys(ra.warnings + rb.warnings)),
            "provenance": [p.to_dict() for p in ra.provenance],
        }
        if not ta or not tb:
            missing = name_a if not ta else name_b
            result.update(same_taxon=None, confidence="none")
            result["warnings"].insert(0,
                f"'{missing}' did not resolve, so this comparison is NOT decided. "
                "same_taxon is null, meaning unknown -- not 'different'. Do not report a "
                "difference. Try consult_authorities.")
            return result
        if ta & tb:
            shared = min(ta & tb)
            result.update({
                "shared_identifier": f"NCBI:txid{shared}",
                "accepted_name": self.db.scientific_name(shared),
                "reason_names_differ": (
                    f"both map to txid{shared}; one is recorded as a synonym of the other under "
                    f"{ra.governing_code.code.value}"),
            })
            return result
        la, lb = self.db.lineage(min(ta)), self.db.lineage(min(tb))
        sa = {e[0] for e in la}
        common = next((e for e in reversed(lb) if e[0] in sa), None)
        result.update({
            "identifier_a": f"NCBI:txid{min(ta)}", "identifier_b": f"NCBI:txid{min(tb)}",
            "nearest_shared_taxon": ({"name": common[1], "rank": common[2]} if common else None),
            "reason_names_differ": "different taxa; not synonyms. Treating them as one organism "
                                   "would conflate distinct taxa.",
        })
        if common and common[2] in {"genus", "family"}:
            result["note"] = (f"closely related (shared {common[2]}: {common[1]}); similar names in "
                              "one genus are a common source of false synonymy")
        return result

    def get_lineage(self, name: str) -> dict:
        if not self.has_stage2:
            return {"query": name, "error": "full index not installed"}
        r = self.resolve_name(name)
        taxids = sorted({int(c.identifier.split("txid")[1]) for c in r.candidates
                         if c.identifier and "txid" in c.identifier})
        return {
            "query": name,
            "classifications": [{
                "identifier": f"NCBI:txid{t}", "accepted_name": self.db.scientific_name(t),
                "lineage": [{"name": e[1], "rank": e[2]} for e in self.db.lineage(t)],
                "governing_code": self.db.code_for(t),
            } for t in taxids],
            "classification_source": (
                f"NCBI Taxonomy {self.db.meta.get('version', 'unknown')}. A pragmatic "
                "classification for organizing sequence data -- not a phylogenetic hypothesis "
                "and not an authority under any code. Ranks above genus may differ from the "
                "code-specific authority."),
            "warnings": r.warnings,
        }

    def list_reclassifications(self, group: str, since_year: int | None = None,
                               limit: int = 100) -> dict:
        if not self.has_stage2:
            return {"group": group, "error": "full index not installed"}
        rows = self.db.lookup(group)
        if not rows:
            return {"group": group, "error": f"'{group}' not found", "changes": []}
        taxid, _ = self.db.resolve_taxid(rows[0]["taxid"])
        changes, undated = [], 0
        for m in self.db.descendants_at_rank(taxid, "species", limit=limit * 5):
            now = self.db.scientific_name(m.taxid) or ""
            for s in self.db.names_for(m.taxid):
                if s["name_class"] not in {"synonym", "equivalent name", "genbank synonym"}:
                    continue
                kind = ("reassignment" if now.split(" ")[0] != s["name"].split(" ")[0]
                        else "synonymy")
                entry = {"from": s["name"], "to": now, "kind": kind, "year": None}
                ov = self.db.overlay(s["name"])
                if ov and ov[0].get("year"):
                    entry["year"] = ov[0]["year"]
                else:
                    undated += 1
                if since_year and entry["year"] and entry["year"] < since_year:
                    continue
                changes.append(entry)
                if len(changes) >= limit:
                    break
            if len(changes) >= limit:
                break
        return {
            "group": group, "identifier": f"NCBI:txid{taxid}", "since_year": since_year,
            "changes": changes, "n_undated": undated,
            "completeness_warning": (
                f"{undated} of {len(changes)} changes carry no date -- taxdump does not record "
                "when a nomenclatural act was published, so a since_year filter silently excludes "
                "undated changes that may fall in range. This is a lower bound, not a record."),
            "provenance": [self.db.provenance().to_dict()],
        }

    def list_authorities(self, group: str | None = None) -> dict:
        """The one place the full code descriptions live.

        They used to be inlined on every response, which cost ~70 tokens per
        call of identical boilerplate. A caller who needs them comes here.
        """
        from .authorities import REGISTRY
        code = None
        if group and self.has_stage2:
            rows = self.db.lookup(group)
            if rows:
                t, _ = self.db.resolve_taxid(rows[0]["taxid"])
                code = Code(self.db.code_for(t) or Code.UNDETERMINED.value)

        def describe(a) -> dict:
            return {"name": a.name, "tier": a.tier, "codes": [c.value for c in a.codes],
                    "license": a.license_note, "redistributable": a.redistributable,
                    "configured": getattr(a, "configured", True),
                    "homepage": getattr(a, "homepage", None)}

        applicable = [describe(a) for a in REGISTRY.values() if code is None or code in a.codes]
        return {
            "group": group, "governing_code": code.value if code else None,
            "indexes": {
                "stage1": self.s1.meta.get("version") if self.s1 else "not installed",
                "stage2": self.db.meta.get("version") if self.has_stage2 else "not installed",
                "license": (self.db.meta.get("source_license") if self.has_stage2
                            else "NCBI Taxonomy: public domain"),
            },
            "authorities": applicable,
            "consulted_by_default": [a["name"] for a in applicable if a["configured"]],
            "not_configured": [a["name"] for a in applicable if not a["configured"]],
            "codes": {c.value: CODE_DESCRIPTIONS[c] for c in
                      ([code] if code else list(CODE_DESCRIPTIONS))},
            "status_vocabulary": status_vocabulary_for(code) if code else None,
            "note": ("Status vocabularies are not interchangeable across codes. 'Not validly "
                     "published' (ICNP), 'nom. inval.' (ICNafp) and 'unaccepted' (a checklist) "
                     "are three different assertions; binomen returns the native term alongside "
                     "a normalized label and never substitutes one for another."),
        }


def _dedupe_overlay(entries: list[dict]) -> list[dict]:
    """One overlay entry can be reached by several of its keys.

    "Candida auris" and "[Candida] auris" normalize identically now that
    brackets are folded, so the same entry came back twice and its guidance was
    emitted twice. Harmless-looking, but a duplicated "AUTHORITIES DISAGREE"
    reads like two independent sources saying it -- false corroboration, which
    is the thing this package exists to prevent.
    """
    seen, out = set(), []
    for e in entries:
        key = e.get("name")
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out
