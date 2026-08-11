#!/usr/bin/env python3
"""Run the case set in both conditions.

Environment framing: each case is an episode. The observation space is the
prompt plus tool returns; the action space is tool calls plus a final answer;
the reward is verifiable from ground truth via the severity scorer. This is a
reinforcement-learning environment with verifiable rewards, and the same loop
would serve as a training environment, not only an evaluation one.

Conditions
----------
baseline  model alone, no tools
tools     model with the binomen tools available

Tools are served in-process from `binomen.resolver` using the same
descriptions the MCP server publishes, rather than over stdio. Same schemas,
same text, no subprocess -- which keeps the run reproducible and lets us record
exactly which tools were called with which arguments.

Reproducibility
---------------
Runs default to BINOMEN_OFFLINE=1 so live authorities are served from cache. A
number produced against a live API is not reproducible. Warm the cache first
with --warm-cache.

Usage
-----
    python eval/runner.py --condition both --model claude-sonnet-5
    python eval/runner.py --cases eval/cases/heldout.jsonl --once   # final numbers
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

from scorer import score_case

from binomen.resolver import Resolver
from binomen.tool_descriptions import descriptions, variant

SYSTEM = (
    "You are assisting a working scientist. Answer the question directly and concisely. "
    "If you state a biological name, a gene symbol, or a claim about whether two names refer to "
    "the same organism, make sure it is correct and say where it comes from."
)

SCHEMAS = {
    "resolve_name": {"type": "object", "properties": {
        "name": {"type": "string"}, "group_hint": {"type": "string"}}, "required": ["name"]},
    "check_currency": {"type": "object", "properties": {
        "name": {"type": "string"}, "as_of": {"type": "string"}}, "required": ["name"]},
    "get_synonyms": {"type": "object", "properties": {"name": {"type": "string"}},
                     "required": ["name"]},
    "expand_query": {"type": "object", "properties": {
        "resolution_id": {"type": "string"}, "include_vernacular": {"type": "boolean"}},
        "required": ["resolution_id"]},
    "compare_names": {"type": "object", "properties": {
        "name_a": {"type": "string"}, "name_b": {"type": "string"}},
        "required": ["name_a", "name_b"]},
    "get_lineage": {"type": "object", "properties": {"name": {"type": "string"}},
                    "required": ["name"]},
    "list_reclassifications": {"type": "object", "properties": {
        "group": {"type": "string"}, "since_year": {"type": "integer"},
        "limit": {"type": "integer"}}, "required": ["group"]},
    "list_authorities": {"type": "object", "properties": {"group": {"type": "string"}},
                         "required": []},
}


def build_tools() -> list[dict]:
    desc = descriptions()
    return [{"name": n, "description": desc[n], "input_schema": SCHEMAS[n]} for n in SCHEMAS]


def dispatch(resolver: Resolver, name: str, args: dict):
    fn = {
        "resolve_name": lambda: resolver.resolve_name(args["name"], args.get("group_hint")).to_dict(),
        "check_currency": lambda: resolver.check_currency(args["name"], args.get("as_of")),
        "get_synonyms": lambda: resolver.get_synonyms(args["name"]),
        "expand_query": lambda: resolver.expand_query(args["resolution_id"],
                                                      args.get("include_vernacular", False)),
        "compare_names": lambda: resolver.compare_names(args["name_a"], args["name_b"]),
        "get_lineage": lambda: resolver.get_lineage(args["name"]),
        "list_reclassifications": lambda: resolver.list_reclassifications(
            args["group"], args.get("since_year"), args.get("limit", 100)),
        "list_authorities": lambda: resolver.list_authorities(args.get("group")),
    }[name]
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}",
                "do_not": "Do not substitute a remembered name for a failed lookup."}


def run_episode(client, model: str, case: dict, condition: str, resolver: Resolver | None,
                max_turns: int = 8) -> tuple[str, list[str], list[dict]]:
    messages = [{"role": "user", "content": case["prompt"]}]
    tools = build_tools() if condition == "tools" else []
    used: list[str] = []
    transcript: list[dict] = []

    for _ in range(max_turns):
        kwargs = {"model": model, "max_tokens": 1400, "system": SYSTEM,
                  "messages": messages}
        if tools:
            kwargs["tools"] = tools
        resp = client.messages.create(**kwargs)
        blocks = resp.content
        messages.append({"role": "assistant", "content": blocks})
        calls = [b for b in blocks if getattr(b, "type", "") == "tool_use"]
        text = "".join(getattr(b, "text", "") for b in blocks if getattr(b, "type", "") == "text")
        transcript.append({"assistant_text": text,
                           "tool_calls": [{"name": c.name, "input": c.input} for c in calls]})
        if not calls:
            return text, used, transcript
        results = []
        for c in calls:
            used.append(c.name)
            out = dispatch(resolver, c.name, c.input)  # type: ignore[arg-type]
            results.append({"type": "tool_result", "tool_use_id": c.id,
                            "content": json.dumps(out, default=str)[:20000]})
        messages.append({"role": "user", "content": results})

    return "(max turns reached without a final answer)", used, transcript


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=Path, default=HERE / "cases" / "cases.jsonl")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--condition", choices=["baseline", "tools", "both"], default="both")
    ap.add_argument("--out", type=Path, default=HERE / "runs")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--category")
    ap.add_argument("--allow-unverified", action="store_true",
                    help="score cases whose ground truth has not been verified (NOT for reporting)")
    ap.add_argument("--live", action="store_true",
                    help="allow live authority queries; off by default for reproducibility")
    a = ap.parse_args(argv)

    if not a.live:
        os.environ.setdefault("BINOMEN_OFFLINE", "1")

    cases = [json.loads(line) for line in a.cases.read_text().splitlines() if line.strip()]
    if a.category:
        cases = [c for c in cases if c["category"] == a.category]
    if a.limit:
        cases = cases[: a.limit]

    unverified = [c["id"] for c in cases if c.get("confidence") != "verified"]
    if unverified and not a.allow_unverified:
        print(f"REFUSING TO RUN: {len(unverified)}/{len(cases)} cases have unverified ground "
              f"truth.\nRun `python eval/verify_cases.py --live` first, or pass --allow-unverified "
              f"for an exploratory run whose numbers must not be reported.\nFirst few: "
              f"{unverified[:5]}", file=sys.stderr)
        return 2
    if unverified:
        print(f"WARNING: {len(unverified)} cases have unverified ground truth. "
              f"These numbers are exploratory and must not be reported.", file=sys.stderr)

    try:
        from anthropic import Anthropic
    except ImportError:
        print("pip install 'binomen[eval]'", file=sys.stderr)
        return 1
    client = Anthropic()

    conditions = ["baseline", "tools"] if a.condition == "both" else [a.condition]
    resolver = Resolver() if "tools" in conditions else None

    a.out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_path = a.out / f"run-{stamp}.jsonl"
    meta = {
        "_meta": True, "model": a.model, "description_variant": variant(),
        "cases_file": str(a.cases), "n_cases": len(cases), "conditions": conditions,
        "offline": os.environ.get("BINOMEN_OFFLINE") == "1",
        "unverified_cases": len(unverified),
        "index_version": (resolver.db.meta.get("version") if resolver else None),
        "started": stamp,
    }
    with open(run_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(meta) + "\n")
        for i, case in enumerate(cases, 1):
            for cond in conditions:
                try:
                    answer, used, transcript = run_episode(client, a.model, case, cond, resolver)
                except Exception as e:  # noqa: BLE001
                    print(f"  [{case['id']}/{cond}] ERROR {e}", file=sys.stderr)
                    continue
                s = score_case(case, answer, used, cond)
                f.write(json.dumps({
                    "case_id": case["id"], "category": case["category"], "code": case["code"],
                    "condition": cond, "prompt": case["prompt"], "answer": answer,
                    "tools_used": used, "transcript": transcript, "score": s.to_dict(),
                }, ensure_ascii=False) + "\n")
                f.flush()
                flag = "OK " if s.passed else f"{s.error_class.value.upper():11s}"
                print(f"[{i:3d}/{len(cases)}] {case['id']:20s} {cond:8s} {flag} "
                      f"tools={len(used)}", file=sys.stderr)

    print(f"\nwrote {run_path}\nnow: python eval/report.py {run_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
