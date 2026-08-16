"""An abbreviated genus is an input, not an error.

`check_name("C. difficile")` returned `unknown` for the whole life of the
project, alongside "Do not substitute a name you remember" -- the worst answer
available, because it is indistinguishable from "no such organism" and offers
the caller nothing to do next. Both tool schemas promised the form and used
that exact string as their example.

It also cost measured invocations. In eval/runs/invocation-cc-20260815-121825,
the model reached for the tool and passed `{"name": "E. coli"}`; the call
returned nothing usable, and the run was counted a success because the harness
scores whether a tool fired, not whether it answered.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from binomen.build.build_index import split_abbreviation

REPO = Path(__file__).resolve().parents[1]
NODE_NAMES = REPO / "node" / "src" / "names.js"

# Shared with the Node side below, so the two implementations are checked
# against one table rather than against two hand-written ones that can drift.
SHAPES = [
    ("C. difficile", ["c", "difficile"]),
    ("c. difficile", ["c", "difficile"]),
    ("C.difficile", ["c", "difficile"]),          # no space after the point
    ("  E. coli  ", ["e", "coli"]),
    ("E. coli subsp. coli", ["e", "coli subsp. coli"]),
    ("C. difficile 630", ["c", "difficile 630"]),
    ("Clostridioides difficile", None),           # already full
    ("Clostridioides", None),                     # bare genus
    ("CFTR", None),                               # gene symbol
    ("C.", None),                                 # nothing to expand to
    ("C. 630", None),                             # remainder is not a name
    ("", None),
]


@pytest.mark.parametrize("raw,expected", SHAPES)
def test_split_abbreviation_shapes(raw, expected):
    got = split_abbreviation(raw)
    assert (list(got) if got else None) == expected


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_split_abbreviation_matches_node():
    """The two implementations must agree on what counts as an abbreviation.

    A disagreement here is not cosmetic: the extension and the harness would
    disagree about which inputs are even resolvable, and the eval would measure
    a tool the user does not have.
    """
    script = (
        f"const n = require({json.dumps(str(NODE_NAMES))});"
        f"const inputs = {json.dumps([s for s, _ in SHAPES])};"
        "process.stdout.write(JSON.stringify(inputs.map((i) => n.splitAbbreviation(i))));"
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", check=True, cwd=REPO).stdout
    assert json.loads(out) == [e for _, e in SHAPES]


# --------------------------------------------------------------------------
# Index-backed behaviour. Skipped where no index is built, because the point of
# these is the real NCBI collision set -- a synthetic fixture would assert that
# the code does what it does.

def _resolver():
    from binomen.db import IndexNotBuilt
    from binomen.resolver import Resolver
    try:
        r = Resolver()
        if r.s1 is None:
            pytest.skip("stage-1 index not built")
        return r
    except IndexNotBuilt:
        pytest.skip("stage-1 index not built")


def test_abbreviation_enumerates_rather_than_guessing():
    """'S. aureus' is not Staphylococcus. It is twelve things, one of which is.

    Senecio (a plant), Stegastes (a fish) and Scindapsus (pothos) all abbreviate
    to S. aureus. Every reader supplies Staphylococcus from context, and that
    supplied context is exactly what this package exists to make explicit.
    """
    out = _resolver().check_name("S. aureus")
    assert out["verdict"] == "ambiguous_abbreviation"
    assert out["escalate"] is True
    assert "Staphylococcus aureus" in out["expansions"]
    assert len(out["expansions"]) > 1
    assert out["coverage_warning"]


def test_abbreviation_reports_the_pair_it_exists_for():
    out = _resolver().check_name("C. difficile")
    assert out["verdict"] == "ambiguous_abbreviation"
    assert set(out["expansions"]) >= {"Clostridium difficile", "Clostridioides difficile"}


def test_trinomials_are_not_expansions_of_a_binomial_abbreviation():
    """"Spermophilus elegans aureus" ends in ' aureus' and LIKE will return it.

    It abbreviates to "S. e. aureus". Returning it under "S. aureus" would be
    the tool inventing a collision, which is worse than missing one.
    """
    for name in _resolver().check_name("S. aureus")["expansions"]:
        assert len(name.split()) == 2


def test_a_single_expansion_resolves_through_to_its_verdict():
    """One candidate is not ambiguous; carry the real verdict, labelled."""
    out = _resolver().check_name("M. tuberculosis")
    assert out["verdict"] != "ambiguous_abbreviation"
    assert out["resolved_via"]["abbreviation"] == "M. tuberculosis"
    assert out["resolved_via"]["expanded"] == "Mycobacterium tuberculosis"


def test_full_binomials_are_untouched():
    """The abbreviation branch must not reach names that resolve normally."""
    r = _resolver()
    assert r.check_name("Homo sapiens")["verdict"] == "stable"
    assert "resolved_via" not in r.check_name("Escherichia coli")


def test_an_unmatched_abbreviation_still_says_unknown():
    """Absence of expansions is not licence to invent one."""
    out = _resolver().check_name("Q. xyzzy")
    assert out["verdict"] == "unknown"
    assert "do_not" in out
