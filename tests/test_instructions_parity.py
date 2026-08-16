"""The server's `instructions` text must be identical in both implementations.

The eval harness talks to the API directly rather than through MCP, so to
measure what a Claude Desktop user actually receives it has to inject this text
itself, from the Python copy. The extension sends the Node copy. If the two
drift, the harness measures a treatment nobody gets -- and it would report a
clean number while doing it.

That is the same failure as the hand-written conformance fixture, which was
built from assumptions about NCBI rather than from NCBI and caused three bugs
before anyone looked. The lesson recorded there was to derive the fixture from
the real artifact rather than restate it. So this test does not restate the
string; it asks Node for its copy and compares.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from binomen.tool_descriptions import (
    INSTRUCTION_VARIANTS,
    SERVER_INSTRUCTIONS_VARIANTS,
)

REPO = Path(__file__).resolve().parents[1]
NODE_MODULE = REPO / "node" / "src" / "tool_descriptions.js"
CONTEXT_SCRIPT = REPO / "scripts" / "show_model_context.js"
DOCS = REPO / "docs"


def _node(*args: str) -> str:
    """Run node and return its stdout, decoded as UTF-8.

    `encoding="utf-8"` is load-bearing, not tidiness -- the same lesson
    eval/invocation_cc.py records at length for the streaming reader. `text=True`
    alone decodes with the locale default, which on Windows is cp1252, and Node
    emits UTF-8. The em dashes in scripts/show_model_context.js then come back as
    "â€”" and the comparison fails, reporting a text drift that does not exist.

    The instruction and description strings are currently pure ASCII, which is
    the only reason the two callers that predate this helper ever passed without
    it. That is luck, not design: one micro sign in a tool description would have
    turned a parity test into a mystery failure about a character nobody changed.
    """
    return subprocess.run(["node", *args], capture_output=True, text=True,
                          encoding="utf-8", check=True, cwd=REPO).stdout


def _node_variants() -> dict[str, str]:
    """Ask Node for the strings it will actually send, rather than parsing them.

    Reading them out of the JS by regex would be a third restatement of the same
    text, and would pass while the real value differed -- the concatenated
    string literals are not what a regex over the source sees.
    """
    return json.loads(_node("-e", (
        f"const d = require({json.dumps(str(NODE_MODULE))});"
        "process.stdout.write(JSON.stringify(d.INSTRUCTION_SETS));"
    )))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("variant", INSTRUCTION_VARIANTS)
def test_instructions_match_between_implementations(variant):
    assert NODE_MODULE.exists(), f"missing {NODE_MODULE}"
    node = _node_variants()
    assert node.get(variant) == SERVER_INSTRUCTIONS_VARIANTS[variant], (
        f"The `{variant}` instructions text has drifted between the Node "
        "extension and the Python copy the eval harness injects. They must "
        "match, or the harness measures a treatment the extension does not send."
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_no_variant_exists_on_only_one_side():
    # A variant present in Node but absent in Python would be shippable and
    # unmeasurable; the reverse would be measurable and unshipped. Both are the
    # same bug wearing different clothes.
    assert set(_node_variants()) == set(SERVER_INSTRUCTIONS_VARIANTS)


@pytest.mark.parametrize("variant", INSTRUCTION_VARIANTS)
def test_instructions_are_not_accidentally_empty(variant):
    # A silently empty treatment would look like a clean negative result.
    text = SERVER_INSTRUCTIONS_VARIANTS[variant]
    assert text.strip()
    assert "binomen" in text


def test_every_description_states_an_occasion():
    """Each description must say *when* to call, not only what the tool does.

    This assertion has now been wrong twice, and each rewrite narrowed it
    usefully.

    First it required `check_name` to appear in the instructions text -- true
    while instructions carried the trigger, false once `terse` moved the
    trigger into the descriptions. Then it required the string `check_name` to
    appear somewhere the model reads, which failed when the descriptions
    stopped repeating the tool's own name: "Call on every genus and/or species
    name" is read against the `name` field, so restating it was pure cost.

    What survives both is the thing actually being protected. binomen's
    measured failure mode is not wrong arguments or misread results -- session
    1 found the answer was good on every prompt where the tool fired. It is
    that the tool never fires. A description that says only what a tool does,
    and never when to reach for it, is the shape of that failure, and it looks
    perfectly reasonable in review.
    """
    from binomen.tool_descriptions import DESCRIPTION_SETS

    for name, text in DESCRIPTION_SETS["terse"].items():
        lowered = text.lower()
        assert any(w in lowered for w in ("call", "when", "if", "every")), (
            f"{name}'s description states no occasion for calling it: {text!r}")


def test_check_name_trigger_is_unconditional():
    """check_name's trigger must not ask the model to judge which names matter.

    That judgement requires knowing which remembered names are out of date, and
    nothing the model remembers carries a date -- the conditional instruction
    that asked for it failed live (DISCOVERY-LOG session 3). The stage-1 tool
    is the one place the trigger has to stay observable.
    """
    from binomen.tool_descriptions import DESCRIPTION_SETS

    assert "every" in DESCRIPTION_SETS["terse"]["check_name"].lower()


def test_variants_are_actually_different():
    # If a copy-paste collapsed the two, the comparison would report no effect
    # for the most defensible reason imaginable: there was nothing to compare.
    assert (SERVER_INSTRUCTIONS_VARIANTS["conditional"]
            != SERVER_INSTRUCTIONS_VARIANTS["unconditional"])


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("variant", [*INSTRUCTION_VARIANTS, "off"])
def test_fingerprint_matches_between_implementations(variant):
    """The hash in docs/MODEL-CONTEXT-*.md must be the hash stamped on runs.

    `scripts/show_model_context.js` recomputes `invocation.py:text_fingerprint`
    in JavaScript so the generated documents can carry it. That is a
    restatement, and restatements drift. Two ways it can:

      separators  Python's `json.dumps` writes a space after `:` and `,`;
                  `JSON.stringify` does not. Live today -- get this wrong and
                  every hash differs.
      escaping    Python escapes every character above U+007F as \\uXXXX;
                  JavaScript emits it literally. Latent today, because the
                  instruction and description strings are currently pure ASCII.
                  One micro sign in a description and it stops being latent.

    A drifted fingerprint is worse than no fingerprint: it reads as licence to
    pool runs that TESTING.md rule 7 exists to keep apart.
    """
    # By path, not `from eval.invocation import ...`: eval/ has no __init__.py
    # and `eval` is a builtin name, so the package import works or does not
    # depending on how pytest was invoked.
    spec = importlib.util.spec_from_file_location(
        "binomen_eval_invocation", REPO / "eval" / "invocation.py")
    inv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inv)

    out = _node(str(CONTEXT_SCRIPT), "--fingerprint", variant).strip()
    assert out == inv.text_fingerprint("terse", variant)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("variant", ["terse", "conditional", "unconditional"])
def test_model_context_docs_are_not_stale(variant):
    """The committed document must be what the script produces right now.

    These files existed for a day describing descriptions that had since been
    rewritten -- 317 chars of tool text documented while 234 were being sent,
    and every run on disk measuring the 234. A wording review against that
    document is a review of text no model has ever received, which is the exact
    failure the script was written to prevent.

    Run `make model-context` in the same commit as any tool-text edit.
    """
    doc = DOCS / f"MODEL-CONTEXT-{variant}.md"
    assert doc.exists(), f"missing {doc} -- run `make model-context`"
    fresh = _node(str(CONTEXT_SCRIPT), "--md", variant)
    assert doc.read_text(encoding="utf-8").strip() == fresh.strip(), (
        f"docs/MODEL-CONTEXT-{variant}.md is stale. Run `make model-context`.")


def _node_descriptions(variant: str) -> dict:
    return json.loads(_node("-e", (
        f"const d = require({json.dumps(str(NODE_MODULE))});"
        f"process.stdout.write(JSON.stringify(d.DESCRIPTION_SETS[{json.dumps(variant)}]));"
    )))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_terse_descriptions_match_between_implementations():
    """The tools block is the text sent on every request. It must match too.

    Node ships `terse` as the default description set; the harness serves the
    same four tools from Python. A drift here would mean the harness measures
    descriptions the extension does not send -- which is the same failure as an
    instructions drift, on the more expensive channel.
    """
    from binomen.tool_descriptions import DESCRIPTION_SETS
    assert _node_descriptions("terse") == DESCRIPTION_SETS["terse"]
