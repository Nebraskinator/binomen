"""Tool descriptions, versioned, because the wording is a variable.

Tool descriptions are the only thing that decides whether a model reaches for
a tool. A resolver that is never invoked has no effect on any outcome, so
invocation rate is not a UX detail here -- it is the dominant term.

Three variants ship so the effect can be measured rather than asserted. They
differ only in wording; the schemas and behavior are identical. Select with:

    BINOMEN_DESCRIPTIONS=narrow|broad|imperative

  narrow      what most tools look like: names the domain, describes the return
  broad       names the *trigger condition* rather than the domain -- "whenever
              an organism or gene name appears", not "for taxonomy questions"
  imperative  broad, plus an explicit statement that answering from memory is
              an error, addressed at the abstention behavior directly

The hypothesis is that `narrow` under-triggers because a model does not
classify "summarize this paper about C. difficile" as a taxonomy question --
it classifies it as a summarization question, and never considers the tool.
Results in README section 7.
"""

from __future__ import annotations

import os

VARIANTS = ("narrow", "broad", "imperative")


def variant() -> str:
    v = os.environ.get("BINOMEN_DESCRIPTIONS", "broad").lower()
    return v if v in VARIANTS else "broad"


_NARROW = {
    "resolve_name": (
        "Resolve a biological name to its currently accepted form using NCBI Taxonomy and "
        "code-specific authorities. Returns accepted name candidates, rank, identifiers, "
        "nomenclatural status, the governing code, and the chain of changes."
    ),
    "check_currency": (
        "Check whether a biological name is currently accepted, optionally as of a given date."
    ),
    "get_synonyms": "List all names that have referred to a taxon, grouped by synonymy type.",
    "expand_query": (
        "Build the full set of search terms needed to retrieve literature across a taxon's "
        "naming history. Requires a resolution_id from resolve_name."
    ),
    "compare_names": "Determine whether two biological names refer to the same taxon.",
    "get_lineage": "Return the full classification lineage for a name.",
    "list_reclassifications": "List recorded nomenclatural changes within a group.",
    "list_authorities": "List the nomenclatural authorities and code governing a group.",
}

_BROAD = {
    "resolve_name": (
        "USE THIS WHENEVER AN ORGANISM NAME OR GENE SYMBOL APPEARS -- in a question, a document "
        "you are reading, a dataset column, or your own draft answer. Not only for questions that "
        "are about taxonomy.\n\n"
        "Biological names are not stable identifiers. Clostridium difficile is now Clostridioides "
        "difficile; Enterobacter aerogenes is now Klebsiella aerogenes; Lactobacillus was split "
        "into about 25 genera; SEPT2 is now SEPTIN2. Both old and new names are heavily attested "
        "in text, so a name being familiar is no evidence that it is current.\n\n"
        "Returns candidate accepted names (a LIST -- authorities genuinely disagree for some "
        "taxa), rank, stable identifiers, the status in the vocabulary of the governing "
        "nomenclatural code, the change history, and provenance for every assertion. Also returns "
        "a resolution_id required by expand_query."
    ),
    "check_currency": (
        "Check whether a biological name was the accepted name, optionally as of a specific date. "
        "Use before stating that any name is 'current', and use whenever a document's publication "
        "date matters -- a name correct in 2015 may not be correct now, and text written then is "
        "not wrong, it is dated. Returns what superseded the name where applicable, and states "
        "explicitly when the as-of question cannot be answered from available data instead of "
        "estimating."
    ),
    "get_synonyms": (
        "Get every name that has referred to a taxon, labelled by synonymy type and by the source "
        "that recorded it. Use when you need to know what an organism has been called, when "
        "reconciling datasets or author lists that may use different names for one organism, and "
        "before concluding that two records describe different organisms."
    ),
    "expand_query": (
        "Build the complete set of search terms needed to retrieve literature across a taxon's "
        "entire naming history, including abbreviated genus forms like 'C. difficile'.\n\n"
        "USE THIS BEFORE ANY LITERATURE SEARCH, DATABASE QUERY, OR SYSTEMATIC REVIEW involving an "
        "organism. Searching the current name alone silently misses everything published under "
        "prior names -- often decades of primary work -- and returns zero results rather than an "
        "error, which reads like a finding.\n\n"
        "Requires a resolution_id from resolve_name: query expansion without resolved identity is "
        "just a list of remembered names."
    ),
    "compare_names": (
        "Determine whether two biological names refer to the same taxon, and if not, how closely "
        "related they are. Use whenever two names appear that might be the same organism, when "
        "merging or deduplicating records, and when a name looks like a variant of another. "
        "Getting this wrong in either direction destroys data silently: treating one organism as "
        "two under-counts, treating two as one conflates them, and neither raises an error. "
        "Pneumocystis carinii and Pneumocystis jirovecii are the canonical trap -- similar names, "
        "different organisms, and the older name appears throughout the human clinical literature."
    ),
    "get_lineage": (
        "Return the full classification lineage for a name under the current classification, "
        "naming the source of that classification. Use when higher-rank placement matters -- "
        "family and phylum membership are not stable, and 'which family is this in' has a "
        "different answer depending on which authority and which year you ask."
    ),
    "list_reclassifications": (
        "List recorded nomenclatural changes within a group, optionally since a year. Use when "
        "assessing whether a body of literature about a group spans a naming change, or when "
        "auditing a dataset built over time."
    ),
    "list_authorities": (
        "List which nomenclatural code governs a group, which authorities are recognized for it, "
        "and which of them this server actually consulted. Use to report provenance honestly, and "
        "when you need to know whether a status term means what you think it means -- status "
        "vocabularies are not interchangeable between codes."
    ),
}

_IMPERATIVE_SUFFIX = {
    "resolve_name": (
        "\n\nDO NOT answer from memory. You have both the old and the new name in your training "
        "data with no reliable signal about which is current, and stating a superseded name "
        "fluently and without hedging is the specific failure this tool exists to prevent. If "
        "this tool is unavailable or returns nothing, say the name could not be verified rather "
        "than supplying one."
    ),
    "check_currency": (
        "\n\nIf you are about to write the word 'currently' next to a biological name, call this "
        "first."
    ),
    "expand_query": (
        "\n\nA literature search performed without calling this is incomplete, and you will not be "
        "able to tell that it was incomplete from the results."
    ),
    "compare_names": (
        "\n\nDo not judge sameness from how similar the strings look. String similarity and taxon "
        "identity are unrelated."
    ),
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
