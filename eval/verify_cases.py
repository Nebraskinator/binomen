#!/usr/bin/env python3
"""Verify case ground truth against the configured authorities.

Why this exists. A benchmark whose ground truth was written from the same
parametric memory the benchmark is evaluating measures nothing. Every case
ships `confidence: "unverified"` and the runner refuses to produce reportable
numbers until this has run.

What it can and cannot do:

  CAN   confirm that a `current_name` case's expected accepted name is what the
        backbone and live authorities actually return, and flag mismatches.
  CAN   confirm `same_taxon` cases against resolved identifiers.
  CAN   confirm that the terms in `must_include_terms` cases are real recorded
        synonyms rather than invented ones.
  CANNOT verify `states_unknown` cases, or that a dispute is genuine, or a
        publication year. Those need a human with the primary literature, and
        they stay `unverified` with `needs_human: true`.

Output is a report plus an optional rewritten cases file. It never silently
edits ground truth to match the tool -- that would make the eval circular.
Mismatches are reported for a human to adjudicate.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from binomen.resolver import Resolver

AUTOVERIFIABLE = {"current_name", "same_taxon", "must_include_terms"}


def verify(resolver: Resolver, case: dict) -> dict:
    check, exp = case["check"], case.get("expected", {})
    out = {"id": case["id"], "check": check, "status": "needs_human", "detail": ""}

    if check not in AUTOVERIFIABLE:
        out["detail"] = ("predicate cannot be machine-verified; a human must confirm the ground "
                         "truth against the cited sources")
        return out

    if check == "current_name":
        target = exp["accepted"]
        r = resolver.resolve_name(target)
        names = {c.accepted_name for c in r.candidates}
        if not r.candidates:
            out.update(status="unresolvable",
                       detail=f"'{target}' did not resolve in any consulted source")
        elif target in names:
            out.update(status="confirmed",
                       detail=f"'{target}' is returned as an accepted name by "
                              f"{', '.join(sorted(set(r.consulted_sources)))}")
        else:
            out.update(status="CONFLICT",
                       detail=f"expected accepted name '{target}' but sources return "
                              f"{sorted(names)}. Adjudicate before using this case.")

    elif check == "same_taxon":
        # Parse the two names out of the prompt is unreliable; require them in
        # `expected` when present, else mark for a human.
        pair = exp.get("names")
        if not pair or len(pair) != 2:
            out["detail"] = ("same_taxon case does not carry an explicit name pair in `expected`; "
                             "add `names: [a, b]` to make it machine-verifiable")
            return out
        cmp = resolver.compare_names(pair[0], pair[1])
        if cmp["same_taxon"] is None:
            out.update(status="unresolvable", detail="one or both names did not resolve")
        elif cmp["same_taxon"] == exp["same"]:
            out.update(status="confirmed", detail=cmp.get("reason_names_differ", ""))
        else:
            out.update(status="CONFLICT",
                       detail=f"expected same={exp['same']}, sources say {cmp['same_taxon']}")

    elif check == "must_include_terms":
        missing = []
        for t in exp["terms"]:
            r = resolver.resolve_name(t)
            if not r.candidates:
                missing.append(t)
        if missing:
            out.update(status="CONFLICT",
                       detail=f"these expected terms do not resolve in any consulted source: "
                              f"{missing}. Either the term is wrong, or the index/authority that "
                              f"would carry it was not consulted. Check which before editing the "
                              f"case.")
        else:
            out.update(status="confirmed", detail="all expected terms resolve to real records")

    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=Path, default=HERE / "cases" / "cases.jsonl")
    ap.add_argument("--live", action="store_true", help="allow live authority queries")
    ap.add_argument("--write", action="store_true",
                    help="mark machine-confirmed cases as verified in place")
    a = ap.parse_args(argv)

    if not a.live:
        os.environ.setdefault("BINOMEN_OFFLINE", "1")

    cases = [json.loads(line) for line in a.cases.read_text().splitlines() if line.strip()]
    resolver = Resolver()

    # A fixture or partially-built index produces false CONFLICTs -- every name
    # it does not contain looks invented. Say so loudly rather than letting
    # someone act on it.
    n_names = int(resolver.db.meta.get("n_names", 0) or 0)
    if n_names < 100_000:
        print(f"WARNING: the loaded index has only {n_names} names "
              f"(version '{resolver.db.meta.get('version')}'). This looks like a fixture or a "
              f"partial build. Verification against it will report false CONFLICTs for every "
              f"name the index does not happen to contain. Build the full index first:\n"
              f"    binomen-build-index\n", file=sys.stderr)
    results, counts = [], {}
    for c in cases:
        r = verify(resolver, c)
        results.append(r)
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        if r["status"] == "CONFLICT":
            print(f"CONFLICT  {r['id']}: {r['detail']}")

    print("\n--- verification summary ---")
    for k, v in sorted(counts.items()):
        print(f"  {k:14s} {v}")
    print(f"\n{counts.get('confirmed', 0)}/{len(cases)} cases machine-confirmed.")
    print(f"{counts.get('needs_human', 0)} require a human with the primary literature.")
    if counts.get("CONFLICT"):
        print(f"{counts['CONFLICT']} CONFLICTS must be adjudicated before reporting anything.")

    if a.write:
        confirmed = {r["id"] for r in results if r["status"] == "confirmed"}
        for c in cases:
            if c["id"] in confirmed:
                c["confidence"] = "verified"
            elif c["id"] in {r["id"] for r in results if r["status"] == "needs_human"}:
                c["needs_human"] = True
        with open(a.cases, "w", encoding="utf-8") as f:
            for c in cases:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        print(f"\nrewrote {a.cases}: {len(confirmed)} marked verified")

    (HERE / "verification-report.json").write_text(json.dumps(results, indent=2))
    print(f"\nfull report: {HERE / 'verification-report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
