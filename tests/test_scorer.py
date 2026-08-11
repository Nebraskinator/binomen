import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

from scorer import score_case
from taxonomy import ErrorClass


def _load_cases() -> dict:
    path = ROOT / "eval" / "cases" / "cases.jsonl"
    out = {}
    for line in path.read_text().splitlines():
        if line.strip():
            case = json.loads(line)
            out[case["id"]] = case
    return out


CASES = _load_cases()


def test_conflating_two_taxa_is_very_major():
    s = score_case(CASES["distractor-001"], "Yes, they are the same organism.", [], "baseline")
    assert s.error_class is ErrorClass.VERY_MAJOR


def test_splitting_one_taxon_is_also_very_major():
    s = score_case(CASES["multihop-006"], "No, they are different organisms.", [], "baseline")
    assert s.error_class is ErrorClass.VERY_MAJOR


def test_genus_level_blanket_substitution_on_a_split_is_very_major():
    s = score_case(CASES["split-001"], "Lactobacillus is now Lacticaseibacillus.",
                   ["resolve_name"], "tools")
    assert s.error_class is ErrorClass.VERY_MAJOR


def test_correct_without_provenance_is_minor_not_clean():
    s = score_case(CASES["historic-001"], "Call it Clostridioides difficile.", ["resolve_name"],
                   "tools")
    assert s.passed and s.error_class is ErrorClass.MINOR


def test_correct_with_provenance_is_clean():
    s = score_case(CASES["historic-001"],
                   "Clostridioides difficile (LPSN; Lawson et al. 2016).", ["resolve_name"], "tools")
    assert s.passed and s.error_class is ErrorClass.NONE


def test_single_answer_on_contested_case_is_very_major():
    s = score_case(CASES["contested-001"], "Yes, switch to Candidozyma auris.", ["resolve_name"],
                   "tools")
    assert s.error_class is ErrorClass.VERY_MAJOR
    assert s.false_confidence is True


def test_flagging_disagreement_passes():
    s = score_case(CASES["contested-001"],
                   "Both are in use: Candidozyma auris per Liu et al. 2024, but Candida auris "
                   "remains widespread and authorities disagree.", ["resolve_name"], "tools")
    assert s.passed and s.false_confidence is False


def test_incomplete_query_expansion_is_major():
    s = score_case(CASES["literature-001"], 'Search "Clostridioides difficile".',
                   ["resolve_name"], "tools")
    assert s.error_class is ErrorClass.MAJOR
    assert "silently incomplete" in s.reason


def test_abstention_failure_is_tracked_separately_from_correctness():
    """A correct answer given without checking is still flagged."""
    s = score_case(CASES["historic-001"],
                   "Clostridioides difficile, per LPSN 2016.", [], "tools")
    assert s.passed is True
    assert s.abstention_failure is True
    assert s.severity == 0.0          # must not contaminate the severity score
    assert "will not hold at scale" in s.reason


def test_abbreviated_genus_counts_as_a_mention():
    s = score_case(CASES["historic-001"], "Call it C. difficile -- formally Clostridioides "
                                          "difficile (LPSN).", ["resolve_name"], "tools")
    assert s.passed
