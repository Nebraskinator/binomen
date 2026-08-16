#!/usr/bin/env python3
"""Run every organism name the case set asserts through check_name. No API key.

Why this exists
---------------
Everything measured so far is *invocation* -- whether a model reaches for the
tool. Nothing has measured what the tool says when it is reached for. Those are
different questions, and only one of them costs money to answer.

This is the cheap one. It needs an index and nothing else: no network, no
ANTHROPIC_API_KEY, no `claude -p`, no rate limit to hit at row 40.

Where the names come from
-------------------------
The `expected` block, not the prompt text. That matters. A first draft of this
script regexed binomials out of the prompts and reported 30 of 77 names missing
from the index -- a 39% coverage hole that was almost entirely "Actinobacteria
still", "Candida going" and "All the", i.e. a capitalised taxon followed by the
next English word. A wrong number presented confidently is the exact failure
this project exists to catch, so the names are now read from the structured
ground truth the case author committed to:

    accepted        the name the answer must give
    names           both sides of a disagreement
    terms           strings the answer must contain
    mapping         old name -> new name, for splits
    also_acceptable / wrong / must_not_say

The 23 `states_unknown` cases assert no name at all -- they test that the model
declines -- so they contribute nothing here and are not counted against
coverage.

What it is for
--------------
Three things the invocation grids structurally cannot see:

  * **Coverage.** Does the index know the names this project chose as its own
    test material? An `unknown` here is a hole in the product.

  * **The escalate gate.** `check_name` returns `escalate` to say "now call
    resolve_name". If it escalates on nearly everything it is not a filter, it
    is a toll booth -- every check becomes two calls, on a tool whose own budget
    documentation is counted in tokens per request. Escherichia coli and
    Drosophila melanogaster both escalate. That may be correct; it has never
    been counted.

  * **The interesting verdicts.** `contested` and `homonym` are what the product
    is for. Their count is worth watching across index releases.

Usage
-----
    python scripts/sweep_cases.py
    python scripts/sweep_cases.py --show contested homonym unknown
    python scripts/sweep_cases.py --json sweep.json

Point BINOMEN_STAGE1_DB / BINOMEN_DB at an index if it is not in the default
location.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

# Keys under `expected` whose values are names. `same` (bool) and
# `min_fraction` (float) are assertions about the answer, not taxa.
# Names an answer MUST contain. Deliberately not `also_acceptable`, `wrong` or
# `must_not_say`: those are tolerances and traps, not requirements. Counting
# them as coverage produced a false alarm -- historic-012 lists the feminine
# variant "Nakaseomyces glabrata" as merely tolerated while asserting the
# correct masculine "Nakaseomyces glabratus", and the sweep reported the
# tolerated spelling as an index hole.
NAME_KEYS = ("accepted", "names", "terms")


def looks_like_a_name(s: str) -> bool:
    """A scientific name, loosely: capitalised, alphabetic, at most four tokens.

    Loose on purpose. `terms` is free text the case author wrote, so it holds
    things like "no longer valid" alongside real binomials. Rejecting a real
    name here understates coverage, and accepting a phrase only costs one
    `unknown` that is visible by eye in the listing.
    """
    if not isinstance(s, str):
        return False
    toks = s.split()
    if not 1 <= len(toks) <= 4 or not s[:1].isupper():
        return False
    if not all(t.replace(".", "").replace("-", "").isalpha() for t in toks):
        return False
    # Exactly one capitalised token, and it must be the genus. This is what
    # separates a name from a sentence: "Aster is now Symphyotrichum" is four
    # alphabetic tokens under five, capitalised at the front, and is not a
    # taxon. Six of those slipped through the first version and were reported
    # as index coverage holes.
    caps = [i for i, t in enumerate(toks) if t[:1].isupper()]
    if toks[0] == "Candidatus":
        return caps in ([0], [0, 1])
    return caps == [0]


def names_in(expected: dict) -> set[str]:
    found: set[str] = set()
    for key in NAME_KEYS:
        v = expected.get(key)
        if isinstance(v, str):
            found.add(v)
        elif isinstance(v, list):
            found.update(x for x in v if isinstance(x, str))
    mapping = expected.get("mapping")
    if isinstance(mapping, dict):
        found.update(mapping.keys())
        found.update(x for x in mapping.values() if isinstance(x, str))
    return {s for s in found if looks_like_a_name(s)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=Path, default=REPO / "eval" / "cases" / "cases.jsonl")
    ap.add_argument("--show", nargs="*", default=["contested", "homonym", "unknown"],
                    help="verdicts to list by name (default: the interesting ones)")
    ap.add_argument("--json", type=Path, help="write the full per-name result here")
    a = ap.parse_args()

    from binomen.db import IndexNotBuilt
    from binomen.resolver import Resolver
    try:
        r = Resolver()
    except IndexNotBuilt as e:
        print(f"no index: {e}", file=sys.stderr)
        return 1
    if r.s1 is None:
        print("stage-1 index not built; run binomen-build-index", file=sys.stderr)
        return 1

    cases = [json.loads(line) for line in
             a.cases.read_text(encoding="utf-8").splitlines() if line.strip()]

    # Which categories a name came from, so a hole is traceable to the cases it
    # breaks rather than merely counted.
    origin: dict[str, set[str]] = defaultdict(set)
    silent = 0
    for c in cases:
        found = names_in(c.get("expected") or {})
        if not found:
            silent += 1
        for n in found:
            origin[n].add(c.get("category", "?"))

    names = sorted(origin)
    if not names:
        print("no names found in the case set", file=sys.stderr)
        return 1

    print(f"{len(cases)} cases, {silent} of which assert no name -> {len(names)} distinct names")
    print(f"index: {r.s1.meta.get('version', 'unknown')}\n")

    results = {n: r.check_name(n) for n in names}
    verdicts = Counter(v["verdict"] for v in results.values())
    escalated = sum(1 for v in results.values() if v.get("escalate"))

    print("VERDICTS")
    for v, k in verdicts.most_common():
        print(f"  {v:<24}{k:>4}{k / len(names):>8.0%}")
    print()
    print(f"ESCALATE   {escalated}/{len(names)} ({escalated / len(names):.0%}) ask for a "
          f"second call")
    print("  A check that escalates on nearly everything is not a filter. This is the "
          "per-use cost of the design, and it has not been counted before.")
    print()

    unknown = [n for n in names if results[n]["verdict"] == "unknown"]
    if unknown:
        cats = Counter(c for n in unknown for c in origin[n])
        print(f"COVERAGE   {len(unknown)}/{len(names)} asserted names are not in the index")
        print("  by the category of the case that asserts them:")
        for c, k in cats.most_common():
            print(f"    {c:<16}{k:>4}")
        print()

    for want in a.show:
        hits = [n for n in names if results[n]["verdict"] == want]
        if not hits:
            continue
        print(f"{want.upper()}  ({len(hits)})")
        for n in hits:
            out = results[n]
            extra = out.get("accepted_name") or ""
            if out.get("expansions"):
                extra = f"-> {len(out['expansions'])} expansions"
            print(f"    {n:<40}{'/'.join(sorted(origin[n])):<24}{extra}")
        print()

    if a.json:
        a.json.write_text(json.dumps(
            {"index": r.s1.meta.get("version"), "results": results,
             "origin": {k: sorted(v) for k, v in origin.items()}},
            indent=2), encoding="utf-8")
        print(f"wrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
