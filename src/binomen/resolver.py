"""The resolver. All eight tools are methods here; server.py is a thin wrapper.

Three rules govern everything in this file.

1. Nothing is generated. Every returned value is a lookup or a rule applied to
   versioned reference data. Where we do not know something we return null and
   say why, rather than producing a plausible value. A fabricated authority
   string or year is worse than a missing one because it is citable.

2. Disagreement is never collapsed. `resolve_name` returns a list of candidates.
   There is deliberately no top-level `current_name` field: if one existed,
   every caller would read it and the contested cases would silently degrade to
   whichever answer we happened to sort first.

3. Provenance rides along with the value it describes, not in a header. A
   response assembled from three sources carries three provenance records, and
   each candidate points at the one that produced it.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import date

from .authorities import authorities_for
from .authorities.base import AuthorityResult
from .codes import (
    CODE_DESCRIPTIONS,
    Code,
    Status,
    TaxonStatus,
    detect_code,
    normalize_status,
    status_vocabulary_for,
)
from .db import Backbone
from .models import Candidate, ChangeEvent, Provenance, Resolution

GENE_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9\-]{1,14}$")


@dataclass
class _Resolved:
    """Internal handle recorded when identity resolution succeeds."""

    query: str
    taxid: int | None
    matched_name: str | None
    accepted_names: list[str]
    code: Code
    created: float


class Resolver:
    def __init__(self, db: Backbone | None = None, *, use_live: bool = True, max_tier: int = 4):
        self.db = db or Backbone()
        self.use_live = use_live
        self.max_tier = max_tier
        # Resolution handles. `expand_query` requires one, which is what forces
        # a multi-step episode and lets the harness observe whether the agent
        # actually resolved identity or jumped straight to an answer.
        self._resolutions: dict[str, _Resolved] = {}

    # ------------------------------------------------------------------ util
    def _looks_like_gene(self, name: str) -> bool:
        """Heuristic, and reported as one.

        A gene symbol and a genus abbreviation are not reliably separable from
        the string alone. We only treat something as a gene when it matches the
        symbol shape AND does not resolve as an organism in the backbone.
        """
        if " " in name.strip():
            return False
        return bool(GENE_SYMBOL_RE.match(name.strip()))

    def _handle(self, r: _Resolved) -> str:
        h = hashlib.sha1(f"{r.query}|{r.taxid}|{r.created}".encode()).hexdigest()[:12]
        self._resolutions[h] = r
        return h

    def _consult(self, code: Code, name: str, *, fuzzy: bool) -> list[AuthorityResult]:
        if not self.use_live:
            return []
        out = []
        for auth in authorities_for(code, max_tier=self.max_tier):
            try:
                out.append(auth.lookup(name, fuzzy=fuzzy))
            except Exception as e:  # noqa: BLE001
                out.append(AuthorityResult(authority=auth.name, found=False,
                                           error=f"{type(e).__name__}: {e}"))
        return out

    # ------------------------------------------------------- tool 1: resolve
    def resolve_name(self, name: str, group_hint: str | None = None,
                     *, allow_fuzzy: bool = True) -> Resolution:
        name = (name or "").strip()
        warnings: list[str] = []
        consulted: list[str] = []
        provs: list[Provenance] = []
        changes: list[ChangeEvent] = []

        if not name:
            return Resolution(query=name, matched_name=None, match_type="none",
                              governing_code=detect_code([]), candidates=[], contested=False,
                              change_chain=[], provenance=[],
                              warnings=["empty query"])

        # --- gene branch (Tier 4) -----------------------------------------
        rows = self.db.lookup(name)
        if not rows and self._looks_like_gene(name):
            return self._resolve_gene(name)

        # --- backbone -----------------------------------------------------
        consulted.append("NCBI Taxonomy")
        match_type = "exact" if rows else "none"
        if not rows:
            near = self.db.prefix_lookup(name, limit=5)
            if near:
                warnings.append(
                    "no exact match in the backbone; nearest index entries: "
                    + ", ".join(sorted({r["name"] for r in near}))
                )

        # Homonyms: the same string under more than one taxid. This is not an
        # ambiguity to resolve silently -- Bacillus is a bacterium and a stick
        # insect, governed by different codes, and picking one is a coin flip.
        taxids = sorted({r["taxid"] for r in rows})
        if len(taxids) > 1:
            warnings.append(
                f"'{name}' matches {len(taxids)} distinct taxa in NCBI Taxonomy "
                f"(taxids {', '.join(str(t) for t in taxids)}). This is a homonym or a "
                f"reused name; the correct answer depends on which is meant. All are returned."
            )

        candidates: list[Candidate] = []
        input_statuses: list[TaxonStatus] = []
        code_assignment = detect_code([])
        primary_taxid: int | None = None

        for taxid in taxids:
            live_taxid, was_merged = self.db.resolve_taxid(taxid)
            if primary_taxid is None:
                primary_taxid = live_taxid
            lineage = self.db.lineage_names(live_taxid)
            ca = detect_code(lineage)
            if code_assignment.code is Code.UNDETERMINED:
                code_assignment = ca
            node = self.db.node(live_taxid)
            accepted = self.db.scientific_name(live_taxid) or name
            matched_row = next((r for r in rows if r["taxid"] == taxid), None)
            native_class = matched_row["name_class"] if matched_row else "scientific name"

            # Two different statuses, kept apart on purpose:
            #   input_status    - what the string the caller typed is
            #   candidate.status - what the accepted name is
            # Conflating them produces the reading "Clostridioides difficile is
            # a synonym", which is exactly backwards.
            input_status = normalize_status("ncbi", native_class)
            if was_merged and input_status.normalized is Status.ACCEPTED:
                input_status = TaxonStatus(Status.MERGED, native_class, "ncbi",
                                           note="the taxid this name resolved to was merged away")
            if input_statuses is not None:
                input_statuses.append(input_status)
            status = TaxonStatus(Status.ACCEPTED, "scientific name", "ncbi")

            prov = self.db.provenance()
            provs.append(prov)
            # When a name is a homonym, the bare accepted name is useless --
            # two candidates both reading "Bacillus" is not an answer. NCBI's
            # unique_name ("Bacillus <stick insect>") disambiguates, and we
            # attach a short lineage so the caller can tell them apart even if
            # the source has no unique_name.
            uniq = None
            if len(taxids) > 1:
                row = self.db.conn.execute(
                    "SELECT unique_name FROM names WHERE taxid=? AND name_class='scientific name' "
                    "AND unique_name != '' LIMIT 1", (live_taxid,)).fetchone()
                uniq = row["unique_name"] if row else None
            candidates.append(Candidate(
                accepted_name=accepted,
                identifier=f"NCBI:txid{live_taxid}",
                rank=node.rank if node else None,
                authority=self.db.authority(live_taxid),
                status=status,
                provenance=prov,
                supporting_sources=["NCBI Taxonomy"],
                disambiguation=uniq,
                lineage_summary=list(lineage[1:6]) if len(taxids) > 1 else [],
            ))

            if was_merged:
                changes.append(ChangeEvent(
                    from_name=f"NCBI:txid{taxid}", to_name=f"NCBI:txid{live_taxid}",
                    kind="merge", provenance=prov,
                    note="recorded in merged.dmp: NCBI unified two taxids. The year of the "
                         "underlying nomenclatural act is not recorded in taxdump.",
                ))
            if accepted.lower() != name.lower():
                changes.append(ChangeEvent(
                    from_name=name, to_name=accepted, kind="synonymy", provenance=prov,
                    note=f"NCBI name class of the input: '{native_class}'",
                ))

        # --- curated overlay ----------------------------------------------
        contested = False
        overlay_entries = self.db.overlay(name)
        for entry in overlay_entries:
            consulted.append("binomen curated overlay")
            oprov = Provenance(
                source="binomen curated overlay", version=self.db.meta.get("overlay_version", "0.1.0"),
                retrieved=self.db.meta.get("retrieved", "unknown"),
                license="see contested.json",
                note=f"confidence: {entry.get('confidence', 'unverified')}. "
                     "Unverified overlay entries must not be used for reported results.",
            )
            provs.append(oprov)
            if entry.get("contested"):
                contested = True
                existing = {c.accepted_name for c in candidates}
                for cand in entry.get("candidates", []):
                    st = TaxonStatus(Status.CONTESTED, "contested", "binomen curated overlay")
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
                        argument=cand.get("argument"),
                    ))
                warnings.append("AUTHORITIES DISAGREE. " + entry.get("guidance", ""))
            elif entry.get("guidance"):
                warnings.append(entry["guidance"])
            for step in entry.get("chain", []):
                changes.append(ChangeEvent(
                    from_name=step["from"], to_name=step["to"], kind=step.get("kind", "rename"),
                    year=step.get("year"), provenance=oprov,
                ))
            if entry.get("references"):
                changes.append(ChangeEvent(
                    from_name=name, to_name=entry.get("current_name") or name,
                    kind=entry.get("change_kind", "rename"), year=entry.get("year"),
                    reference="; ".join(entry["references"]), provenance=oprov,
                ))

        # --- live authorities ---------------------------------------------
        for res in self._consult(code_assignment.code, name, fuzzy=allow_fuzzy and not rows):
            consulted.append(res.authority)
            if res.error:
                warnings.append(f"{res.authority}: not consulted ({res.error})")
                continue
            if not res.found:
                warnings.append(f"{res.authority}: no match for '{name}'")
                continue
            if res.provenance:
                provs.append(res.provenance)
            if match_type == "none":
                match_type = res.match_type
            existing = {c.accepted_name for c in candidates}
            if res.accepted_name and res.accepted_name not in existing:
                candidates.append(Candidate(
                    accepted_name=res.accepted_name, identifier=res.identifier, rank=res.rank,
                    authority=res.author_citation, status=res.status or normalize_status(res.authority, "unknown"),
                    provenance=res.provenance or self.db.provenance(),
                    supporting_sources=[res.authority],
                ))
            elif res.accepted_name:
                for c in candidates:
                    if c.accepted_name == res.accepted_name:
                        c.supporting_sources.append(res.authority)
                        c.authority = c.authority or res.author_citation

        # Disagreement detected empirically, not just from the overlay.
        distinct = {c.accepted_name for c in candidates}
        if len(distinct) > 1 and len(taxids) <= 1 and not contested:
            contested = True
            warnings.append(
                "AUTHORITIES DISAGREE: consulted sources returned different accepted names "
                f"({', '.join(sorted(distinct))}). No single answer is correct here; report the "
                "disagreement and say which source you are following."
            )

        if not candidates:
            warnings.append(
                f"'{name}' was not found in any consulted source. This is not evidence that the "
                "organism does not exist -- it may be a misspelling, an infraspecific name, a "
                "strain designation, or a name too recent for the indexed release "
                f"({self.db.meta.get('version', 'unknown')}). Do not substitute a remembered name."
            )

        resolution = Resolution(
            query=name,
            matched_name=(rows[0]["name"] if rows else (candidates[0].accepted_name if candidates else None)),
            match_type=match_type,
            governing_code=code_assignment,
            candidates=candidates,
            contested=contested,
            change_chain=changes,
            provenance=_dedupe_prov(provs),
            consulted_sources=sorted(set(consulted)),
            warnings=warnings,
            input_status=input_statuses[0] if input_statuses else None,
        )
        if candidates:
            resolution.resolution_id = self._handle(_Resolved(
                query=name, taxid=primary_taxid, matched_name=resolution.matched_name,
                accepted_names=[c.accepted_name for c in candidates],
                code=code_assignment.code, created=time.time(),
            ))
        return resolution

    def _resolve_gene(self, symbol: str) -> Resolution:
        ca = detect_code([], is_gene=True)
        candidates, provs, warnings, changes = [], [], [], []
        consulted = []
        for res in self._consult(Code.HGNC, symbol, fuzzy=False):
            consulted.append(res.authority)
            if res.error:
                warnings.append(f"{res.authority}: not consulted ({res.error})")
                continue
            if not res.found:
                warnings.append(f"{res.authority}: no match for '{symbol}'")
                continue
            provs.append(res.provenance)
            candidates.append(Candidate(
                accepted_name=res.accepted_name or symbol, identifier=res.identifier,
                rank="gene", authority=res.author_citation,
                status=res.status or normalize_status("hgnc", "Approved"),
                provenance=res.provenance, supporting_sources=[res.authority],
            ))
            if res.match_type == "exact_previous":
                changes.append(ChangeEvent(
                    from_name=symbol, to_name=res.accepted_name or symbol, kind="rename",
                    provenance=res.provenance,
                    note="input matched a previous or alias symbol, not the current one",
                ))
        overlay = self.db.overlay(symbol)
        for entry in overlay:
            oprov = Provenance(source="binomen curated overlay", version="0.1.0",
                               retrieved=self.db.meta.get("retrieved", "unknown"),
                               note=f"confidence: {entry.get('confidence', 'unverified')}")
            provs.append(oprov)
            if entry.get("guidance"):
                warnings.append(entry["guidance"])
            if entry.get("current_name"):
                if entry["current_name"] not in {c.accepted_name for c in candidates}:
                    candidates.append(Candidate(
                        accepted_name=entry["current_name"], identifier=None, rank="gene",
                        authority=None, status=normalize_status("hgnc", "Approved"),
                        provenance=oprov, supporting_sources=["binomen curated overlay"]))
                changes.append(ChangeEvent(from_name=symbol, to_name=entry["current_name"],
                                           kind="rename", year=entry.get("year"),
                                           reference="; ".join(entry.get("references", [])),
                                           provenance=oprov))
        warnings.append(
            f"'{symbol}' was treated as a gene symbol because it matches the HGNC symbol shape and "
            "did not resolve as an organism name. If an organism was meant, re-query with the full "
            "binomial -- an abbreviated genus ('E. coli') cannot be distinguished from a symbol."
        )
        res = Resolution(query=symbol, matched_name=symbol,
                         match_type="exact" if candidates else "none",
                         governing_code=ca, candidates=candidates,
                         contested=False, change_chain=changes, provenance=_dedupe_prov(provs),
                         consulted_sources=sorted(set(consulted)), warnings=warnings)
        if candidates:
            res.resolution_id = self._handle(_Resolved(
                query=symbol, taxid=None, matched_name=symbol,
                accepted_names=[c.accepted_name for c in candidates],
                code=Code.HGNC, created=time.time()))
        return res

    # ------------------------------------------------ tool 2: check_currency
    def check_currency(self, name: str, as_of: str | None = None) -> dict:
        """Was this name current, as of a date?

        The `as_of` parameter is the honest frame for a nomenclature question,
        and it is also where we most often have to admit ignorance. NCBI does
        not record when a synonymy was published, so for backbone-only answers
        we can say "this name is not currently the accepted one" but not "it
        stopped being accepted in 2016". We return `as_of_answerable: false`
        and say which source would be needed, rather than inventing a date.
        """
        r = self.resolve_name(name)
        today = date.today().isoformat()
        as_of = as_of or today

        dated = [e for e in r.change_chain if e.year]
        accepted_now = [c.accepted_name for c in r.candidates
                        if c.status.normalized in {Status.ACCEPTED, Status.CONTESTED}]
        is_current = any(a.lower() == name.strip().lower() for a in accepted_now)

        out = {
            "query": name,
            "as_of": as_of,
            "asked_about_the_past": as_of < today,
            "is_current_accepted_name": is_current,
            "contested": r.contested,
            "accepted_names_now": accepted_now,
            "governing_code": r.governing_code.to_dict(),
            "as_of_answerable": bool(dated),
            "dated_events": [e.to_dict() for e in dated],
            "consulted_sources": r.consulted_sources,
            "provenance": [p.to_dict() for p in r.provenance],
            "warnings": list(r.warnings),
        }
        if not dated:
            out["warnings"].append(
                "No dated nomenclatural event is available for this name from the consulted "
                "sources, so the question 'was it accepted on " + as_of + "?' cannot be answered "
                "from data. NCBI taxdump records that names changed but not when the change was "
                "published. Add a code-specific authority (LPSN for prokaryotes, MycoBank/Index "
                "Fungorum for fungi, an ICTV MSL for viruses) to answer as-of questions."
            )
        if not is_current and accepted_now:
            out["warnings"].insert(0,
                f"'{name}' is NOT the currently accepted name. Superseded by: "
                + ", ".join(accepted_now) + ". Using it as current will silently under-retrieve.")
        return out

    # -------------------------------------------------- tool 3: get_synonyms
    def get_synonyms(self, name: str) -> dict:
        r = self.resolve_name(name)
        groups: dict[str, list[dict]] = {}
        prov = [p.to_dict() for p in r.provenance]

        taxids = {int(c.identifier.split("txid")[1]) for c in r.candidates
                  if c.identifier and "txid" in c.identifier}
        for taxid in taxids:
            for row in self.db.names_for(taxid):
                klass = row["name_class"]
                if klass == "authority":
                    continue
                st = normalize_status("ncbi", klass)
                groups.setdefault(st.normalized.value, []).append({
                    "name": row["name"],
                    "native_class": klass,
                    "disambiguated_as": row["unique_name"] or None,
                    "identifier": f"NCBI:txid{taxid}",
                })
        for old in {t for taxid in taxids for t in self.db.merged_from(taxid)}:
            groups.setdefault("merged", []).append({
                "name": None, "native_class": "merged taxid", "identifier": f"NCBI:txid{old}",
                "note": "an identifier that was folded into this taxon; datasets keyed on it "
                        "will not join against the current taxid",
            })

        return {
            "query": name,
            "governing_code": r.governing_code.to_dict(),
            "contested": r.contested,
            "by_synonymy_type": groups,
            "all_names": sorted({e["name"] for g in groups.values() for e in g if e.get("name")}),
            "note": ("Synonymy type is reported using each source's native vocabulary alongside a "
                     "normalized label. NCBI does not distinguish homotypic from heterotypic "
                     "synonyms; if that distinction matters, consult the code-specific authority."),
            "consulted_sources": r.consulted_sources,
            "warnings": r.warnings,
            "provenance": prov,
        }

    # -------------------------------------------------- tool 4: expand_query
    def expand_query(self, resolution_id: str, include_vernacular: bool = False) -> dict:
        """Gated behind a resolution_id on purpose.

        This is the design rule from the spec: expand_query must be reachable
        only after identity has been resolved. It is not a security measure --
        it is instrumentation. An agent that wants a search string without
        having resolved identity cannot get one, and the harness can see that
        it tried.
        """
        r = self._resolutions.get(resolution_id)
        if not r:
            return {
                "error": "unknown or expired resolution_id",
                "remedy": "call resolve_name first and pass the resolution_id it returns",
                "why": ("Query expansion without resolved identity produces a search string built "
                        "from remembered names, which is the failure this tool exists to prevent."),
            }
        syn = self.get_synonyms(r.query)
        terms: set[str] = {r.query, *(r.accepted_names or [])}
        skip = {"unknown"} if include_vernacular else {"unknown", "misapplied"}
        for group, entries in syn["by_synonymy_type"].items():
            if group in skip and not include_vernacular:
                continue
            for e in entries:
                if e.get("name"):
                    terms.add(e["name"])
        terms = {t for t in terms if t}

        # Abbreviated-genus forms. "C. difficile" appears in an enormous amount
        # of clinical literature and will not match "Clostridioides difficile"
        # in a title/abstract search.
        abbreviated = set()
        for t in terms:
            parts = t.split()
            if len(parts) >= 2 and parts[0][:1].isupper():
                abbreviated.add(f"{parts[0][0]}. {' '.join(parts[1:])}")

        ordered = sorted(terms)
        return {
            "query": r.query,
            "resolution_id": resolution_id,
            "search_terms": ordered,
            "abbreviated_forms": sorted(abbreviated),
            "boolean_query": " OR ".join(f'"{t}"' for t in ordered),
            "pubmed_query": " OR ".join(f'"{t}"[tiab]' for t in ordered),
            "coverage_warning": (
                "This expansion covers names recorded by the consulted sources. It cannot cover "
                "names that were only ever used informally, misspellings not indexed as such, or "
                "changes published after " + self.db.meta.get("version", "the indexed release") +
                ". Searching the current name alone would have retrieved "
                f"{len(ordered) - 1} fewer name variant(s)."
            ),
            "consulted_sources": syn["consulted_sources"],
            "provenance": syn["provenance"],
        }

    # ------------------------------------------------- tool 5: compare_names
    def compare_names(self, name_a: str, name_b: str) -> dict:
        """Same taxon or not -- and if not, how far apart.

        This is the tool whose errors are graded 'very major'. Both directions
        of mistake destroy data silently: calling two names the same merges
        distinct organisms, calling one name two splits a single one.
        """
        ra, rb = self.resolve_name(name_a), self.resolve_name(name_b)
        ta = {int(c.identifier.split("txid")[1]) for c in ra.candidates
              if c.identifier and "txid" in c.identifier}
        tb = {int(c.identifier.split("txid")[1]) for c in rb.candidates
              if c.identifier and "txid" in c.identifier}

        same = bool(ta & tb)
        result: dict = {
            "name_a": name_a,
            "name_b": name_b,
            "same_taxon": same,
            "confidence": "high" if (ta and tb) else "low",
            "governing_code_a": ra.governing_code.to_dict(),
            "governing_code_b": rb.governing_code.to_dict(),
            "contested": ra.contested or rb.contested,
            "consulted_sources": sorted(set(ra.consulted_sources) | set(rb.consulted_sources)),
            "provenance": [p.to_dict() for p in _dedupe_prov(ra.provenance + rb.provenance)],
            "warnings": list(dict.fromkeys(ra.warnings + rb.warnings)),
        }

        if not ta or not tb:
            missing = name_a if not ta else name_b
            result["confidence"] = "none"
            result["warnings"].insert(0,
                f"'{missing}' did not resolve in the backbone, so this comparison is NOT decided. "
                "same_taxon=false here means 'unknown', not 'different'. Do not report a difference.")
            result["same_taxon"] = None
            return result

        if same:
            shared = min(ta & tb)
            names = {r["name"] for r in self.db.names_for(shared)}
            accepted = self.db.scientific_name(shared)
            reason = "identical taxid"
            if name_a.lower() != name_b.lower():
                reason = (
                    f"both names map to NCBI:txid{shared}; the accepted name is '{accepted}'. "
                    f"The names differ because one is recorded as a synonym of the other under "
                    f"{ra.governing_code.code.value}."
                )
            result.update({
                "shared_identifier": f"NCBI:txid{shared}",
                "accepted_name": accepted,
                "reason_names_differ": reason,
                "both_names_recorded_for_taxon": sorted(names & {name_a, name_b}) or None,
            })
            return result

        # Different taxa: report the nearest shared rank so the caller can see
        # how different. "Different genus" and "different kingdom" are not the
        # same finding.
        la = self.db.lineage(min(ta))
        lb = self.db.lineage(min(tb))
        sa = {e[0]: e for e in la}
        common = None
        for entry in reversed(lb):
            if entry[0] in sa:
                common = entry
                break
        result.update({
            "identifier_a": f"NCBI:txid{min(ta)}",
            "identifier_b": f"NCBI:txid{min(tb)}",
            "nearest_shared_taxon": ({"name": common[1], "rank": common[2],
                                      "identifier": f"NCBI:txid{common[0]}"} if common else None),
            "reason_names_differ": (
                "These resolve to different taxa. They are not synonyms; treating them as one "
                "organism would conflate distinct taxa."
            ),
        })
        if common and common[2] in {"genus", "family"}:
            result["note"] = (
                f"Closely related (shared {common[2]}: {common[1]}). Similar names in the same "
                "genus are a common source of false synonymy -- check that the intended organism "
                "is the one named, not merely one nearby."
            )
        return result

    # --------------------------------------------------- tool 6: get_lineage
    def get_lineage(self, name: str) -> dict:
        r = self.resolve_name(name)
        taxids = sorted({int(c.identifier.split("txid")[1]) for c in r.candidates
                         if c.identifier and "txid" in c.identifier})
        out = []
        for t in taxids:
            out.append({
                "identifier": f"NCBI:txid{t}",
                "accepted_name": self.db.scientific_name(t),
                "lineage": [{"identifier": f"NCBI:txid{e[0]}", "name": e[1], "rank": e[2]}
                            for e in self.db.lineage(t)],
                "governing_code": detect_code(self.db.lineage_names(t)).to_dict(),
            })
        return {
            "query": name,
            "classifications": out,
            "classification_source": (
                f"NCBI Taxonomy {self.db.meta.get('version', 'unknown')}. NCBI Taxonomy is a "
                "pragmatic classification maintained for sequence-database organization, not a "
                "phylogenetic hypothesis and not an authority under any nomenclatural code. "
                "Ranks above genus in particular may differ from the code-specific authority."
            ),
            "warnings": r.warnings,
            "provenance": [p.to_dict() for p in r.provenance],
        }

    # ------------------------------------- tool 7: list_reclassifications
    def list_reclassifications(self, group: str, since_year: int | None = None,
                               limit: int = 100) -> dict:
        """Changes recorded within a group.

        Honest limitation up front: taxdump has no dates, so `since_year` can
        only be honoured for entries the curated overlay dates. We report what
        we can date and say plainly how much we could not.
        """
        rows = self.db.lookup(group)
        if not rows:
            return {"group": group, "error": f"'{group}' not found in the backbone",
                    "changes": [], "provenance": [self.db.provenance().to_dict()]}
        taxid, _ = self.db.resolve_taxid(rows[0]["taxid"])
        members = self.db.descendants_at_rank(taxid, "species", limit=limit * 5) or []
        changes, undated = [], 0
        for m in members:
            syns = [r for r in self.db.names_for(m.taxid)
                    if r["name_class"] in {"synonym", "equivalent name", "genbank synonym"}]
            for s in syns:
                genus_now = (self.db.scientific_name(m.taxid) or "").split(" ")[0]
                genus_then = s["name"].split(" ")[0]
                kind = "reassignment" if genus_now and genus_then and genus_now != genus_then \
                    else "synonymy"
                entry = {"from": s["name"], "to": self.db.scientific_name(m.taxid),
                         "kind": kind, "year": None, "identifier": f"NCBI:txid{m.taxid}"}
                ov = self.db.overlay(s["name"])
                if ov and ov[0].get("year"):
                    entry["year"] = ov[0]["year"]
                    entry["reference"] = "; ".join(ov[0].get("references", []))
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
            "group": group,
            "identifier": f"NCBI:txid{taxid}",
            "since_year": since_year,
            "changes": changes,
            "n_undated": undated,
            "completeness_warning": (
                f"{undated} of {len(changes)} recorded changes carry no date, because NCBI taxdump "
                "does not record when a nomenclatural act was published. A since_year filter "
                "therefore silently excludes undated changes that may fall in range. This listing "
                "is a lower bound on changes in this group, not a complete record."
            ),
            "provenance": [self.db.provenance().to_dict()],
        }

    # ---------------------------------------- tool 8: list_authorities
    def list_authorities(self, group: str | None = None) -> dict:
        from .authorities import REGISTRY

        code = None
        evidence = None
        if group:
            rows = self.db.lookup(group)
            if rows:
                taxid, _ = self.db.resolve_taxid(rows[0]["taxid"])
                ca = detect_code(self.db.lineage_names(taxid))
                code, evidence = ca.code, ca.to_dict()

        def describe(a) -> dict:
            return {
                "name": a.name, "tier": a.tier,
                "codes": [c.value for c in a.codes],
                "license": a.license_note,
                "redistributable": a.redistributable,
                "configured": getattr(a, "configured", True),
                "homepage": getattr(a, "homepage", None),
            }

        applicable = [describe(a) for a in REGISTRY.values() if code is None or code in a.codes]
        return {
            "group": group,
            "governing_code": evidence,
            "backbone": {
                "source": self.db.meta.get("source"),
                "version": self.db.meta.get("version"),
                "retrieved": self.db.meta.get("retrieved"),
                "license": self.db.meta.get("source_license"),
            },
            "authorities": applicable,
            "consulted_by_default": [a["name"] for a in applicable if a["configured"]],
            "not_configured": [a["name"] for a in applicable if not a["configured"]],
            "codes": {c.value: CODE_DESCRIPTIONS[c] for c in
                      ([code] if code else list(CODE_DESCRIPTIONS))},
            "status_vocabulary": status_vocabulary_for(code) if code else None,
            "note": ("Status vocabularies are not interchangeable across codes. 'Not validly "
                     "published' under ICNP, 'nom. inval.' under ICNafp and 'unaccepted' in a "
                     "checklist are three different assertions; binomen returns the native term "
                     "alongside a normalized label and never substitutes one for another."),
        }


def _dedupe_prov(provs: list[Provenance]) -> list[Provenance]:
    seen, out = set(), []
    for p in provs:
        k = (p.source, p.version, p.retrieved, p.note)
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out
