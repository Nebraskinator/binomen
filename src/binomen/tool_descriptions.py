"""Tool descriptions, versioned, because the wording is an experimental variable.

Descriptions are the only thing deciding whether a model reaches for a tool, so
invocation rate is not a UX detail -- it is the dominant term. A resolver that
is never called has no effect on any outcome.

The three-stage design exists partly to make the ask credible. "Call this
whenever an organism name appears" is an unreasonable request for a tool that
costs a second and a thousand tokens. It is a reasonable request for one that
costs two milliseconds and twenty tokens and usually answers "nothing to see
here". So `check_name`'s description leads with its cost.

Variants, selected with BINOMEN_DESCRIPTIONS:

  narrow      what most tool descriptions look like: names the domain,
              describes the return value
  broad       names the *trigger condition* rather than the domain -- "whenever
              an organism or gene name appears", not "for taxonomy questions"
  imperative  broad, plus an explicit statement that answering from memory is
              an error, aimed directly at the abstention behavior

Hypothesis: `narrow` under-triggers because a model does not classify
"summarize this paper about C. difficile" as a taxonomy question. It classifies
it as a summarization question and never considers the tool. Results in README
section 7.
"""

from __future__ import annotations

import os

VARIANTS = ("narrow", "broad", "imperative")


def variant() -> str:
    v = os.environ.get("BINOMEN_DESCRIPTIONS", "broad").lower()
    return v if v in VARIANTS else "broad"


_NARROW = {
    "check_name": "Check whether a biological name has any recorded nomenclatural history.",
    "resolve_name": (
        "Resolve a biological name against the local NCBI Taxonomy index. Returns accepted name "
        "candidates, rank, identifiers, status, governing code and change history."),
    "consult_authorities": (
        "Query live nomenclatural authorities (LPSN, MycoBank, GBIF, ICTV, HGNC) for a name."),
    "check_currency": "Check whether a name is currently accepted, optionally as of a date.",
    "get_synonyms": "List all names that have referred to a taxon, grouped by synonymy type.",
    "expand_query": (
        "Build the set of search terms covering a taxon's naming history. Requires a "
        "resolution_id from resolve_name."),
    "compare_names": "Determine whether two biological names refer to the same taxon.",
    "get_lineage": "Return the classification lineage for a name.",
    "list_reclassifications": "List recorded nomenclatural changes within a group.",
    "list_authorities": "List the authorities and code governing a group.",
}

_BROAD = {
    "check_name": (
        "STAGE 1 -- CHEAP. Local lookup, about 2 ms, and the reply is usually one short line. "
        "Call it freely.\n\n"
        "USE THIS WHENEVER AN ORGANISM NAME OR GENE SYMBOL APPEARS -- in a question, a document "
        "you are reading, a dataset column, or a sentence you are about to write. Not only for "
        "questions that are about taxonomy.\n\n"
        "Answers one question: is there anything about this name that needs a closer look? "
        "Most names return `verdict: stable, escalate: false`, which means the name you have is "
        "the accepted one and you are done. Otherwise it returns `escalate: true` with a `next` "
        "field naming the tool to call and a one-line reason -- superseded, homonym, contested, "
        "or unknown.\n\n"
        "You do not have to judge which names are risky. This tells you, from the index."),
    "resolve_name": (
        "STAGE 2 -- local, no network. Call when check_name says `next: resolve_name`, or when "
        "you need identifiers, rank, authorship or the change history for a name.\n\n"
        "Biological names are not stable identifiers. Clostridium difficile is now Clostridioides "
        "difficile; Enterobacter aerogenes is now Klebsiella aerogenes; Lactobacillus was split "
        "into about 25 genera; SEPT2 is now SEPTIN2. Old and new names are both heavily attested "
        "in text, so a name being familiar is no evidence that it is current.\n\n"
        "Returns candidate accepted names (a LIST -- authorities genuinely disagree for some "
        "taxa), stable identifiers, status in the governing code's vocabulary, the change chain, "
        "and provenance. Also returns a resolution_id required by expand_query."),
    "consult_authorities": (
        "STAGE 3 -- NETWORK, SLOW. Call when check_name or resolve_name says "
        "`next: consult_authorities`, or for the three questions the local index cannot answer "
        "at all:\n"
        "  - is a prokaryotic name validly published under ICNP (an LPSN question -- NCBI will "
        "return names that never were, with no indication)\n"
        "  - was a name accepted AS OF a given date (the local index records that names changed, "
        "not when)\n"
        "  - is a synonymy disputed, and by whom\n\n"
        "Queries LPSN, MycoBank, GBIF, the ICTV Master Species List and HGNC as applicable to the "
        "governing code, and reports which were actually reachable. Do not call it for names "
        "check_name already reported as stable."),
    "check_currency": (
        "Check whether a name was the accepted name, optionally as of a specific date. Use before "
        "writing that any name is 'current', and whenever a document's publication date matters "
        "-- a name correct in 2015 may not be correct now, and text written then is not wrong, it "
        "is dated. States explicitly when the as-of question cannot be answered from data instead "
        "of estimating."),
    "get_synonyms": (
        "Every name that has referred to a taxon, labelled by synonymy type and source. Use when "
        "reconciling datasets or author lists that may use different names for one organism, and "
        "before concluding that two records describe different organisms."),
    "expand_query": (
        "Build the complete set of search terms covering a taxon's entire naming history, "
        "including abbreviated genus forms like 'C. difficile'.\n\n"
        "USE BEFORE ANY LITERATURE SEARCH, DATABASE QUERY OR SYSTEMATIC REVIEW involving an "
        "organism. Searching the current name alone silently misses everything published under "
        "prior names -- often decades of primary work -- and returns zero results rather than an "
        "error, which reads like a finding.\n\n"
        "Requires a resolution_id from resolve_name: expansion without resolved identity is just "
        "a list of remembered names."),
    "compare_names": (
        "Determine whether two biological names refer to the same taxon, and if not, how closely "
        "related they are. Use whenever two names appear that might be one organism, when merging "
        "or deduplicating records, and when a name looks like a variant of another. Getting this "
        "wrong in either direction destroys data silently: treating one organism as two "
        "under-counts, treating two as one conflates them, and neither raises an error. "
        "Pneumocystis carinii and Pneumocystis jirovecii are the canonical trap -- similar names, "
        "different organisms, and the older one appears throughout the human clinical literature."),
    "get_lineage": (
        "Full classification lineage under the current classification, naming the source. Use "
        "when higher-rank placement matters -- family and phylum membership are not stable, and "
        "'which family is this in' has a different answer depending on which authority and which "
        "year you ask."),
    "list_reclassifications": (
        "Recorded nomenclatural changes within a group, optionally since a year. Use when "
        "assessing whether a body of literature about a group spans a naming change, or auditing "
        "a dataset built over time."),
    "list_authorities": (
        "Which code governs a group, which authorities are recognized, which this server actually "
        "consulted, and what each code's status terms mean. Use to report provenance honestly, "
        "and when you need to know whether a status term means what you think -- status "
        "vocabularies are not interchangeable between codes."),
}

_IMPERATIVE_SUFFIX = {
    "check_name": (
        "\n\nThere is no reason not to call this. It is cheaper than being wrong, and being wrong "
        "here is invisible: a superseded name produces confident prose and an incomplete search "
        "that looks complete."),
    "resolve_name": (
        "\n\nDO NOT answer from memory. You hold both the old and the new name with no reliable "
        "signal about which is current, and stating a superseded name fluently and without hedging "
        "is the specific failure this tool prevents. If lookup fails, say the name could not be "
        "verified rather than supplying one."),
    "consult_authorities": (
        "\n\nIf you are about to state that a name is valid, or give a date for a change, and you "
        "have not called this, you are guessing."),
    "check_currency": (
        "\n\nIf you are about to write the word 'currently' next to a biological name, call this "
        "first."),
    "expand_query": (
        "\n\nA literature search performed without calling this is incomplete, and you will not be "
        "able to tell it was incomplete from the results."),
    "compare_names": (
        "\n\nDo not judge sameness from how similar the strings look. String similarity and taxon "
        "identity are unrelated."),
}


def descriptions() -> dict[str, str]:
    v = variant()
    if v == "narrow":
        return dict(_NARROW)
    out = dict(_BROAD)
    if v == "imperative":
        for k, extra in _IMPERATIVE_SUFFIX.items():
            out[k] = out[k] + extra
    return out
