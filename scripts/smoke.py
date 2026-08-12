#!/usr/bin/env python3
"""End-to-end smoke test. Exercises all eight tools and prints a short report.

Run after building the index, before installing in Claude Desktop:
    python scripts/smoke.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from binomen.resolver import Resolver


def show(title: str, obj) -> None:
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))
    print(json.dumps(obj, indent=2, default=str)[:1800])


def main() -> int:
    r = Resolver()

    print("=" * 70)
    print("STAGE 1 -- check_name. Local, ~2 ms. Call this on every organism mention.")
    print("=" * 70)
    for q in ["Escherichia coli", "Clostridium difficile", "Candida auris",
              "Bacillus", "Nonsenseus fakus"]:
        out = r.check_name(q)
        blob = json.dumps(out, separators=(",", ":"))
        print(f"  {len(blob):4d} chars (~{len(blob)//4:3d} tokens)  {blob}")
    print("\n  Note the first line: a stable name needs no accepted_name field,")
    print("  because for a stable name the accepted name IS the input.\n")

    if not r.has_stage2:
        print("Full index not installed -- stage 2 tools will report that honestly.")
        print("Build it with: binomen-build-index")
        return 0

    res = r.resolve_name("Clostridium difficile")
    show("resolve_name('Clostridium difficile')", res.to_dict())
    show("check_currency('Enterobacter aerogenes')", r.check_currency("Enterobacter aerogenes"))
    show("get_synonyms('Candida auris')", r.get_synonyms("Candida auris"))
    if res.resolution_id:
        show("expand_query(<resolution_id>)", r.expand_query(res.resolution_id))
    show("expand_query('not-a-real-id')  # gating", r.expand_query("not-a-real-id"))
    show("compare_names('Pneumocystis carinii', 'Pneumocystis jirovecii')",
         r.compare_names("Pneumocystis carinii", "Pneumocystis jirovecii"))
    show("get_lineage('Bacillus')  # homonym", r.get_lineage("Bacillus"))
    show("list_reclassifications('Lactobacillaceae')", r.list_reclassifications("Lactobacillaceae", limit=10))
    show("list_authorities('Fungi')", r.list_authorities("Fungi"))
    show("consult_authorities('Candida auris')  # stage 3, network",
         r.consult_authorities("Candida auris"))
    print("\nAll ten tools returned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
