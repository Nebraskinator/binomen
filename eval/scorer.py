"""Scorer for the severity-graded taxonomy.

Deterministic by default. Every predicate here is a string or set operation over
the agent's final answer, so scoring is reproducible and auditable: you can read
why a case was graded the way it was without re-running a model.

The cost is that deterministic scoring is conservative. It cannot tell an
elegantly hedged correct answer from an evasive one, so `states_unknown` and
`flags_disagreement` are checked with keyword evidence, which will
under-credit some good answers. An optional LLM judge (--judge) is available
for those two predicates only, and disagreements between the deterministic
scorer and the judge are written to the run file rather than silently resolved.

Never score a case whose `confidence` is still `unverified` into a reported
number -- the runner refuses by default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from taxonomy import SEVERITY_WEIGHT, ErrorClass

HEDGE_PATTERNS = [
    r"\bauthorities disagree\b", r"\bnot settled\b", r"\bunsettled\b", r"\bcontested\b",
    r"\bdisputed\b", r"\bdisagreement\b", r"\bboth names\b", r"\bdepends on\b",
    r"\bno single\b", r"\bnot universally accepted\b", r"\bsome authorities\b",
    r"\bothers (?:retain|use|argue|dispute)\b", r"\bcannot (?:be )?(?:determine|verify|confirm)",
    r"\bunable to verify\b", r"\bi (?:would|should) check\b", r"\bnot able to confirm\b",
    r"\bambiguous\b", r"\bwhich (?:species|taxon|one) (?:is|do you) mean\b",
    r"\bunclear\b", r"\binsufficient\b", r"\bvaries by\b", r"\bpartial(?:ly)? adopt",
]

PROVENANCE_PATTERNS = [
    r"\bNCBI\b", r"\bGBIF\b", r"\bLPSN\b", r"\bMycoBank\b", r"\bIndex Fungorum\b", r"\bICTV\b",
    r"\bHGNC\b", r"\bIJSEM\b", r"\bMSL\s?\d", r"\btaxdump\b", r"\bWorld Flora\b", r"\bIPNI\b",
    r"\baccording to\b", r"\bas of\b", r"\b(19|20)\d{2}\b", r"\bet al\.", r"\bValidation List\b",
]

SUBSTITUTION_PATTERNS = [
    r"{a}\s+(?:is|are|was|were|has been|have been)\s+(?:now\s+)?(?:called|named|known as|renamed to|)\s*{b}",
    r"{a}\s+(?:is|are)\s+now\s+{b}",
    r"{a}\s*(?:->|→|becomes?)\s*{b}",
]


@dataclass
class CaseScore:
    case_id: str
    category: str
    condition: str
    passed: bool
    error_class: ErrorClass
    severity: float
    tool_called: bool
    tools_used: list[str] = field(default_factory=list)
    abstention_failure: bool = False
    false_confidence: bool = False
    provenance_given: bool = False
    reason: str = ""
    judge_disagreed: bool = False

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id, "category": self.category, "condition": self.condition,
            "passed": self.passed, "error_class": self.error_class.value,
            "severity": self.severity, "tool_called": self.tool_called,
            "tools_used": self.tools_used, "abstention_failure": self.abstention_failure,
            "false_confidence": self.false_confidence, "provenance_given": self.provenance_given,
            "reason": self.reason, "judge_disagreed": self.judge_disagreed,
        }


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _mentions(answer: str, term: str) -> bool:
    """Whole-name containment, tolerant of abbreviated genus.

    'C. difficile' counts as a mention of 'Clostridioides difficile' only when
    the initial and epithet both match, so 'C. difficile' does not match
    'Clostridium perfringens'.
    """
    a, t = _norm(answer), _norm(term)
    if t and t in a:
        return True
    parts = term.split()
    if len(parts) >= 2:
        abbrev = f"{parts[0][0].lower()}. {' '.join(parts[1:]).lower()}"
        if abbrev in a:
            return True
    return False


def _hedged(answer: str) -> bool:
    a = _norm(answer)
    return any(re.search(p, a) for p in HEDGE_PATTERNS)


def _has_provenance(answer: str) -> bool:
    return any(re.search(p, answer or "", re.IGNORECASE) for p in PROVENANCE_PATTERNS)


def _asserts_substitution(answer: str, a: str, b: str) -> bool:
    """Did the answer claim 'a is now b'? Used for genus-level blanket errors."""
    txt = _norm(answer)
    for pat in SUBSTITUTION_PATTERNS:
        if re.search(pat.format(a=re.escape(_norm(a)), b=re.escape(_norm(b))), txt):
            return True
    return False


def _yes_no(answer: str) -> bool | None:
    """Extract a sameness judgement. Returns None when the answer does not commit."""
    a = _norm(answer)
    neg = [r"\bnot the same\b", r"\bdifferent (?:organism|species|taxa|taxon)\b", r"\bare different\b",
           r"\bno,? (?:they|these)\b", r"\bdistinct (?:organism|species|taxa)\b", r"\bnot synonym"]
    pos = [r"\bthe same (?:organism|species|taxon)\b", r"\bare the same\b", r"\byes,? (?:they|these)\b",
           r"\bsynonym", r"\bsame taxon\b", r"\brefer to the same\b"]
    n = any(re.search(p, a) for p in neg)
    p = any(re.search(x, a) for x in pos)
    if n and not p:
        return False
    if p and not n:
        return True
    return None


# --------------------------------------------------------------- predicates
def score_case(case: dict, answer: str, tools_used: list[str], condition: str) -> CaseScore:
    check = case["check"]
    exp = case.get("expected", {})
    cid, cat = case["id"], case["category"]
    answer = answer or ""
    tool_called = bool(tools_used)
    prov = _has_provenance(answer)

    passed, err, reason = False, ErrorClass.VERY_MAJOR, ""

    if check == "current_name":
        accepted = exp["accepted"]
        also = exp.get("also_acceptable", [])
        if _mentions(answer, accepted):
            passed = True
            err = ErrorClass.NONE if prov else ErrorClass.MINOR
            reason = f"named '{accepted}'" + ("" if prov else "; no source or date given")
        elif any(_mentions(answer, x) for x in also):
            passed = True
            err = ErrorClass.MINOR
            reason = "named an acceptable alternative but not the preferred accepted name"
        else:
            # Did it assert the query name as current? That is the major error.
            passed, err = False, ErrorClass.MAJOR
            reason = f"did not give the accepted name '{accepted}'"
            if _hedged(answer) and not tool_called:
                err = ErrorClass.MINOR
                reason = "declined to assert rather than asserting a wrong name"

    elif check == "same_taxon":
        judged = _yes_no(answer)
        want = exp["same"]
        if judged is None:
            passed, err = False, ErrorClass.MINOR if _hedged(answer) else ErrorClass.MAJOR
            reason = "no clear sameness judgement"
        elif judged == want:
            passed = True
            err = ErrorClass.NONE if prov else ErrorClass.MINOR
            reason = f"correct sameness judgement ({judged})" + ("" if prov else "; no source")
        else:
            passed, err = False, ErrorClass.VERY_MAJOR
            reason = (f"said same={judged}, truth same={want}. "
                      + ("Conflated distinct taxa." if judged else "Split one taxon into two."))

    elif check == "flags_disagreement":
        names = exp.get("names", [])
        named = [n for n in names if _mentions(answer, n.split(" (")[0])]
        if len(named) >= 2 and _hedged(answer):
            passed = True
            err = ErrorClass.NONE if prov else ErrorClass.MINOR
            reason = f"named {len(named)} candidates and flagged the disagreement"
        elif len(named) >= 2:
            passed, err = False, ErrorClass.MINOR
            reason = "named both candidates but did not say authorities disagree"
        else:
            passed, err = False, ErrorClass.VERY_MAJOR
            reason = "gave a single answer where authorities genuinely disagree"

    elif check == "must_include_terms":
        terms = exp["terms"]
        hit = [t for t in terms if _mentions(answer, t)]
        frac = len(hit) / max(1, len(terms))
        if frac >= exp.get("min_fraction", 1.0):
            passed = True
            err = ErrorClass.NONE if prov else ErrorClass.MINOR
            reason = f"covered {len(hit)}/{len(terms)} required terms"
        else:
            passed, err = False, ErrorClass.MAJOR
            reason = (f"covered only {len(hit)}/{len(terms)} terms; missing "
                      + ", ".join(t for t in terms if t not in hit)
                      + ". Retrieval built on this would be silently incomplete.")

    elif check == "must_not_substitute":
        wrong = exp.get("wrong", [])
        bad = [w for w in wrong if _mentions(answer, w)]
        passed = not bad
        err = ErrorClass.NONE if passed else ErrorClass.MAJOR
        reason = "no spurious substitution" if passed else f"asserted {bad}"

    elif check == "split_disambiguation":
        mapping = exp.get("mapping", {})
        must_not = exp.get("must_not_say", [])
        blanket = []
        for phrase in must_not:
            parts = phrase.split()
            if len(parts) >= 3:
                blanket.append((parts[0], parts[-1]))
        made_blanket = any(_asserts_substitution(answer, a, b) for a, b in blanket)
        correct = [k for k, v in mapping.items() if _mentions(answer, v)]
        if made_blanket:
            passed, err = False, ErrorClass.VERY_MAJOR
            reason = ("applied a genus-level blanket substitution to a split; species that did not "
                      "move would be silently renamed")
        elif len(correct) >= max(1, int(0.7 * len(mapping))):
            passed = True
            err = ErrorClass.NONE if prov else ErrorClass.MINOR
            reason = f"resolved {len(correct)}/{len(mapping)} species individually"
        else:
            passed, err = False, ErrorClass.MAJOR
            reason = f"resolved only {len(correct)}/{len(mapping)} species"

    elif check == "states_unknown":
        if _hedged(answer):
            passed = True
            err = ErrorClass.NONE if prov else ErrorClass.MINOR
            reason = "flagged uncertainty or explained the ambiguity"
        else:
            passed, err = False, ErrorClass.MAJOR
            reason = "gave a confident answer where the correct behavior is to flag uncertainty"

    elif check == "lineage_contains":
        taxa = exp.get("taxa", [])
        hit = [t for t in taxa if _mentions(answer, t)]
        passed = len(hit) == len(taxa)
        err = ErrorClass.NONE if passed else ErrorClass.MINOR
        reason = f"named {len(hit)}/{len(taxa)} expected higher taxa"

    else:
        raise ValueError(f"unknown check predicate: {check}")

    # ------------------------------------------------ separately-tracked flags
    abstention = bool(case.get("tool_expected")) and not tool_called and condition == "tools"
    false_conf = (cat == "contested" or case["check"] == "flags_disagreement") and not _hedged(answer)

    if abstention and err is ErrorClass.NONE:
        reason += ("; answered correctly WITHOUT calling a tool -- correct but unverified, and "
                   "this will not hold at scale")

    return CaseScore(
        case_id=cid, category=cat, condition=condition, passed=passed, error_class=err,
        severity=SEVERITY_WEIGHT[err], tool_called=tool_called, tools_used=tools_used,
        abstention_failure=abstention, false_confidence=false_conf,
        provenance_given=prov, reason=reason,
    )
