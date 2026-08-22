"""Governing nomenclatural codes and their status vocabularies.

The central architectural claim of this project: "what is the current name?"
is not one question. It is at least four questions, governed by codes with
materially different rules about priority, validity, homonymy and authorship.
A resolver that returns a bare string has already discarded the information
that makes the answer interpretable.

Two rules are enforced throughout this module:

1. Every status is reported as BOTH a normalized enum and the source's own
   native term. We never silently flatten "invalid", "illegitimate", "not
   validly published" and "unaccepted" into a single label -- they mean
   different things, and in three different codes.
2. When the governing code cannot be determined from the lineage, we say so
   (`Code.UNDETERMINED`) rather than guessing. Protists in particular are
   genuinely contested territory between ICZN and ICNafp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Code(str, Enum):
    """Nomenclatural code governing a taxon."""

    ICNP = "ICNP"          # International Code of Nomenclature of Prokaryotes
    ICNAFP = "ICNafp"      # ...for algae, fungi, and plants
    ICZN = "ICZN"          # International Code of Zoological Nomenclature
    ICTV = "ICTV"          # International Committee on Taxonomy of Viruses
    HGNC = "HGNC"          # Not a code; gene symbol guidelines (Tier 4)
    UNDETERMINED = "undetermined"


CODE_DESCRIPTIONS: dict[Code, str] = {
    Code.ICNP: (
        "International Code of Nomenclature of Prokaryotes. Governs Bacteria and "
        "Archaea. Distinguishing feature: a name has no standing in nomenclature "
        "until it is validly published in IJSEM or appears on a Validation List. "
        "'Not validly published' is therefore a first-class status, not an error."
    ),
    Code.ICNAFP: (
        "International Code of Nomenclature for algae, fungi, and plants. "
        "Distinguishing feature: the basionym and parenthetical author citation "
        "encode change history inside the name itself -- Candida auris (Satoh & "
        "Makimura) becomes Candidozyma auris (Satoh & Makimura) Liu et al., and "
        "the parentheses are load-bearing. Since the 2011 Melbourne Code, the "
        "dual anamorph/teleomorph naming of fungi is abolished ('one fungus, "
        "one name')."
    ),
    Code.ICZN: (
        "International Code of Zoological Nomenclature. Governs animals. "
        "Distinguishing feature: priority and homonymy rules differ from ICNafp, "
        "and there is no equivalent of ICNafp 'combination' status -- a species "
        "moved between genera keeps its original author and year without "
        "parentheses changes of the same kind."
    ),
    Code.ICTV: (
        "International Committee on Taxonomy of Viruses. Distinguishing feature: "
        "taxonomy is assigned by committee on an annual release cycle rather than "
        "accruing from individual publications, and entire ranks can be created "
        "or abolished wholesale. Species names became mandatory binomials under "
        "the ICTV binomial rollout, so almost every virus species name changed by "
        "committee action rather than by evidence about any particular virus."
    ),
    Code.HGNC: (
        "HUGO Gene Nomenclature Committee guidelines. Not a nomenclatural code "
        "and not taxonomy, included because it is the same failure class: SEPT2 "
        "-> SEPTIN2, MARCH1 -> MARCHF1, renamed in 2020 partly because spreadsheet "
        "software silently coerced the old symbols into dates."
    ),
    Code.UNDETERMINED: (
        "Governing code could not be determined from the available lineage. This "
        "is a real answer, not a failure: many protist groups are claimed by both "
        "ICZN and ICNafp, and some organisms are validly named under both."
    ),
}


class Status(str, Enum):
    """Normalized status enum.

    Deliberately coarse. It exists so that downstream code can branch without
    knowing every source's vocabulary. It is never returned alone -- see
    `TaxonStatus`, which pairs it with the native term.
    """

    ACCEPTED = "accepted"
    SYNONYM = "synonym"
    HOMOTYPIC_SYNONYM = "homotypic_synonym"
    HETEROTYPIC_SYNONYM = "heterotypic_synonym"
    BASIONYM = "basionym"
    ILLEGITIMATE = "illegitimate"
    NOT_VALIDLY_PUBLISHED = "not_validly_published"
    MISAPPLIED = "misapplied"
    MISSPELLING = "misspelling"
    HOMONYM = "homonym"
    UNPLACED = "unplaced"
    INCLUDES = "includes"
    MERGED = "merged"
    DELETED = "deleted"
    CONTESTED = "contested"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TaxonStatus:
    """A status as reported by a source, never flattened.

    `normalized` is for branching. `native` is what the source actually said,
    and is what belongs in a citation or a methods section.
    """

    normalized: Status
    native: str
    source: str
    note: str | None = None

    def to_dict(self) -> dict:
        """Native term is never dropped -- that is the whole contract. But when
        it is identical to the normalized label there is nothing to preserve."""
        d = {"normalized": self.normalized.value}
        if self.native.lower() != self.normalized.value.replace("_", " "):
            d["native"] = self.native
        if self.note:
            d["note"] = self.note
        return d


# --- Native vocabulary maps -------------------------------------------------
# Each entry maps a source's own term to a normalized enum. Terms absent from
# these maps normalize to UNKNOWN and are passed through natively rather than
# being force-fit to the nearest enum member.

NCBI_NAME_CLASS_MAP: dict[str, Status] = {
    "scientific name": Status.ACCEPTED,
    "synonym": Status.SYNONYM,
    "equivalent name": Status.SYNONYM,
    "genbank synonym": Status.SYNONYM,
    # NOT synonymy. NCBI uses `includes` to say "unidentified material filed
    # under this taxon" -- "Candida sp. JHS-2008", "Clostridium sp. HMSC19D05".
    # Mapping it to SYNONYM put strain junk into literature search terms, which
    # is a query-expansion tool actively degrading the query.
    "includes": Status.INCLUDES,
    "misspelling": Status.MISSPELLING,
    "misnomer": Status.MISAPPLIED,
    "authority": Status.UNKNOWN,
    "common name": Status.UNKNOWN,
    "genbank common name": Status.UNKNOWN,
    "blast name": Status.UNKNOWN,
    "acronym": Status.UNKNOWN,
    "genbank acronym": Status.UNKNOWN,
    "type material": Status.UNKNOWN,
    "in-part": Status.INCLUDES,
    "anamorph": Status.SYNONYM,
    "teleomorph": Status.SYNONYM,
    "genbank anamorph": Status.SYNONYM,
}

GBIF_STATUS_MAP: dict[str, Status] = {
    "ACCEPTED": Status.ACCEPTED,
    "SYNONYM": Status.SYNONYM,
    "HOMOTYPIC_SYNONYM": Status.HOMOTYPIC_SYNONYM,
    "HETEROTYPIC_SYNONYM": Status.HETEROTYPIC_SYNONYM,
    "PROPARTE_SYNONYM": Status.HETEROTYPIC_SYNONYM,
    "DOUBTFUL": Status.UNPLACED,
    "MISAPPLIED": Status.MISAPPLIED,
}

# LPSN / ICNP native terms. Note that "validly published" and "correct name"
# are different properties in ICNP: a name can be validly published and still
# not be the correct name for the taxon.
LPSN_STATUS_MAP: dict[str, Status] = {
    "correct name": Status.ACCEPTED,
    "validly published under the ICNP": Status.ACCEPTED,
    "synonym": Status.SYNONYM,
    "later heterotypic synonym": Status.HETEROTYPIC_SYNONYM,
    "later homotypic synonym": Status.HOMOTYPIC_SYNONYM,
    "not validly published": Status.NOT_VALIDLY_PUBLISHED,
    "nomen illegitimum": Status.ILLEGITIMATE,
    "illegitimate name": Status.ILLEGITIMATE,
    "nomen dubium": Status.UNPLACED,
    "later homonym": Status.HOMONYM,
}

# ICNafp native terms (MycoBank / Index Fungorum / IPNI / WFO).
ICNAFP_STATUS_MAP: dict[str, Status] = {
    "legitimate": Status.ACCEPTED,
    "accepted": Status.ACCEPTED,
    "current name": Status.ACCEPTED,
    "basionym": Status.BASIONYM,
    "nom. illeg.": Status.ILLEGITIMATE,
    "nomen illegitimum": Status.ILLEGITIMATE,
    "nom. inval.": Status.NOT_VALIDLY_PUBLISHED,
    "nomen invalidum": Status.NOT_VALIDLY_PUBLISHED,
    "nom. nud.": Status.NOT_VALIDLY_PUBLISHED,
    "nom. rej.": Status.ILLEGITIMATE,
    "nom. cons.": Status.ACCEPTED,
    "synonym": Status.SYNONYM,
    "obligate synonym": Status.HOMOTYPIC_SYNONYM,
    "taxonomic synonym": Status.HETEROTYPIC_SYNONYM,
}

ICTV_STATUS_MAP: dict[str, Status] = {
    "current": Status.ACCEPTED,
    "abolished": Status.DELETED,
    "renamed": Status.SYNONYM,
    "moved": Status.ACCEPTED,
    "merged": Status.MERGED,
    "split": Status.SYNONYM,
    "demoted": Status.SYNONYM,
    "promoted": Status.ACCEPTED,
}

HGNC_STATUS_MAP: dict[str, Status] = {
    "Approved": Status.ACCEPTED,
    "Entry Withdrawn": Status.DELETED,
    "Symbol Withdrawn": Status.SYNONYM,
    "previous symbol": Status.SYNONYM,
    "alias symbol": Status.SYNONYM,
}

_VOCABULARIES: dict[str, dict[str, Status]] = {
    "ncbi": NCBI_NAME_CLASS_MAP,
    "gbif": GBIF_STATUS_MAP,
    "lpsn": LPSN_STATUS_MAP,
    "mycobank": ICNAFP_STATUS_MAP,
    "indexfungorum": ICNAFP_STATUS_MAP,
    "wfo": ICNAFP_STATUS_MAP,
    "ipni": ICNAFP_STATUS_MAP,
    "ictv": ICTV_STATUS_MAP,
    "hgnc": HGNC_STATUS_MAP,
}


def normalize_status(source: str, native_term: str, note: str | None = None) -> TaxonStatus:
    """Map a source's native status term onto the normalized enum.

    Unrecognized terms become Status.UNKNOWN with the native term preserved
    verbatim. This is intentional: an unmapped term is a gap in our vocabulary
    table, not evidence about the taxon, and guessing would manufacture a fact.
    """
    vocab = _VOCABULARIES.get(source.lower(), {})
    key = native_term.strip()
    normalized = vocab.get(key) or vocab.get(key.lower())

    if normalized is None and "(" in key:
        # LPSN qualifies a status in parentheses: the live service returns
        # "correct name (and explicitly recommended for medical use)" where
        # this table only had "correct name". Exact matching dropped the whole
        # thing to UNKNOWN, discarding both the status AND the qualifier --
        # and for a clinical caller that qualifier is the most useful field on
        # the record, because it is the register saying which of two competing
        # names to put in a report.
        #
        # So: match on the head term, and carry the qualifier out in `note`
        # rather than flattening it away. The enum stays a small closed set;
        # the source's own words survive alongside it.
        head, _, tail = key.partition("(")
        normalized = vocab.get(head.strip()) or vocab.get(head.strip().lower())
        if normalized is not None and note is None:
            note = f"{source} qualifies this status: {tail.rstrip(')').strip()}"

    if normalized is None:
        normalized = Status.UNKNOWN
        if note is None:
            note = f"'{native_term}' is not in binomen's vocabulary map for {source}; "\
                   "the native term is authoritative here."
    return TaxonStatus(normalized=normalized, native=native_term, source=source, note=note)


# --- Code detection ---------------------------------------------------------
# Detection is by lineage, not by name shape. A name string carries no reliable
# signal about which code governs it -- that is exactly the ambiguity the
# project exists to remove.

_ICNP_ROOTS = {"bacteria", "archaea", "eubacteria", "prokaryota"}
_ICTV_ROOTS = {"viruses", "viroids", "viridae"}
_ICZN_ROOTS = {"metazoa", "animalia"}
_ICNAFP_ROOTS = {
    "viridiplantae", "plantae", "fungi", "rhodophyta", "glaucocystophyceae",
    "chlorophyta", "streptophyta", "embryophyta", "phaeophyceae", "bacillariophyta",
    "haptophyta", "cryptophyceae", "eustigmatophyceae", "chrysophyceae",
    "xanthophyceae", "charophyta", "oomycota", "oomycetes",
}
# Groups genuinely claimed by more than one code, or historically shuffled
# between them. We return UNDETERMINED with an explanation rather than picking.
_DUAL_CLAIMED = {
    "euglenozoa", "euglenida", "dinophyceae", "dinoflagellata", "myzozoa",
    "amoebozoa", "ciliophora", "apicomplexa", "sar", "alveolata", "stramenopiles",
    "rhizaria", "excavata", "protista", "protozoa", "chromista", "heterolobosea",
    "parabasalia", "fornicata", "metamonada", "choanoflagellata", "microsporidia",
}


@dataclass
class CodeAssignment:
    """Result of code detection, with the evidence that produced it."""

    code: Code
    confidence: str            # "certain" | "likely" | "undetermined"
    evidence: str              # which lineage element drove the call
    alternatives: list[Code] = field(default_factory=list)
    description: str = ""

    def to_dict(self, *, verbose: bool = False) -> dict:
        """Brief by default.

        The description is a ~300-character paragraph explaining what the code
        is. Returning it on every call cost roughly 70 tokens per invocation of
        identical boilerplate -- on a "nothing has changed" answer it was most
        of the response. It is now available on demand from `list_authorities`,
        which is where a caller goes when they actually need it.
        """
        d = {"code": self.code.value, "confidence": self.confidence}
        if self.alternatives:
            d["alternatives"] = [c.value for c in self.alternatives]
        if self.code is Code.UNDETERMINED or verbose:
            d["evidence"] = self.evidence
        if verbose:
            d["description"] = self.description or CODE_DESCRIPTIONS[self.code]
        return d


def detect_code(lineage_names: list[str], *, is_gene: bool = False) -> CodeAssignment:
    """Infer the governing code from a lineage.

    `lineage_names` is ordered root-first, e.g. ["cellular organisms",
    "Bacteria", "Bacillota", ...]. Matching is case-insensitive and checks the
    whole lineage rather than just the top rank, because NCBI's upper ranks are
    not uniform across kingdoms.
    """
    if is_gene:
        return CodeAssignment(
            code=Code.HGNC,
            confidence="certain",
            evidence="input identified as a gene symbol, not an organism name",
        )

    lowered = [n.strip().lower() for n in lineage_names if n]
    lset = set(lowered)

    if lset & _ICTV_ROOTS or any(n.endswith(("viridae", "virales")) for n in lowered):
        hit = next((n for n in lowered if n in _ICTV_ROOTS or n.endswith("viridae")), "viral lineage")
        return CodeAssignment(Code.ICTV, "certain", f"lineage contains '{hit}'")

    if lset & _ICNP_ROOTS:
        hit = next(n for n in lowered if n in _ICNP_ROOTS)
        return CodeAssignment(Code.ICNP, "certain", f"lineage contains '{hit}'")

    if lset & _ICZN_ROOTS:
        hit = next(n for n in lowered if n in _ICZN_ROOTS)
        return CodeAssignment(Code.ICZN, "certain", f"lineage contains '{hit}'")

    if lset & _ICNAFP_ROOTS:
        hit = next(n for n in lowered if n in _ICNAFP_ROOTS)
        return CodeAssignment(Code.ICNAFP, "certain", f"lineage contains '{hit}'")

    if lset & _DUAL_CLAIMED:
        hit = next(n for n in lowered if n in _DUAL_CLAIMED)
        return CodeAssignment(
            code=Code.UNDETERMINED,
            confidence="undetermined",
            evidence=(
                f"lineage contains '{hit}', a group claimed by more than one code. "
                "Some names in these groups are validly published under both ICZN "
                "and ICNafp, and the two codes can disagree about which name has "
                "priority. Consult both."
            ),
            alternatives=[Code.ICZN, Code.ICNAFP],
        )

    return CodeAssignment(
        code=Code.UNDETERMINED,
        confidence="undetermined",
        evidence=(
            "no recognized kingdom-level anchor in the supplied lineage: "
            + (", ".join(lineage_names) if lineage_names else "(empty lineage)")
        ),
    )


def code_anchors() -> dict[str, Code]:
    """Lineage anchor name -> code, for build-time code assignment.

    The build walks the tree once from the root carrying the current code and
    updating it whenever it passes an anchor, so the *deepest* anchor wins.
    That matters: NCBI places Microsporidia inside Fungi, so a naive top-down
    assignment would call them ICNafp; the dual-claimed anchor deeper in the
    tree correctly overrides that to undetermined.

    This replaces the materialized lineage cache. Code detection needs one
    byte per taxon, not a serialized 25-element lineage -- storing the latter
    made the index roughly a gigabyte larger to answer a six-way question.
    """
    out: dict[str, Code] = {}
    for n in _ICNP_ROOTS:
        out[n] = Code.ICNP
    for n in _ICTV_ROOTS:
        out[n] = Code.ICTV
    for n in _ICZN_ROOTS:
        out[n] = Code.ICZN
    for n in _ICNAFP_ROOTS:
        out[n] = Code.ICNAFP
    for n in _DUAL_CLAIMED:
        out[n] = Code.UNDETERMINED
    return out


def status_vocabulary_for(code: Code) -> dict[str, str]:
    """The native status terms that are meaningful under a given code.

    Exposed through `list_authorities` so an agent can see that the vocabularies
    are not interchangeable rather than having to infer it.
    """
    if code is Code.ICNP:
        src = LPSN_STATUS_MAP
    elif code is Code.ICNAFP:
        src = ICNAFP_STATUS_MAP
    elif code is Code.ICTV:
        src = ICTV_STATUS_MAP
    elif code is Code.HGNC:
        src = HGNC_STATUS_MAP
    else:
        src = GBIF_STATUS_MAP
    return {native: norm.value for native, norm in src.items()}
