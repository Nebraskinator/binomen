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
from binomen.tool_descriptions import (
    descriptions,
    instructions_variant,
    server_instructions,
    variant,
)

SYSTEM = (
    "You are assisting a working scientist. Answer the question directly and concisely. "
    "If you state a biological name, a gene symbol, or a claim about whether two names refer to "
    "the same organism, make sure it is correct and say where it comes from."
)

# The `instructed` condition. Added after a hand-run comparison in which neither
# the `broad` nor the `imperative` tool descriptions caused invocation on a
# prompt framed as a summarization task, and three sentences of instruction in
# the client's own context flipped it on the first attempt -- see
# docs/DISCOVERY-LOG.md, session 2.
#
# DIAGNOSTIC ONLY -- do not report this condition as the result. Telling the
# agent when to check deletes the research question (does it know when to look
# something up?) and replaces it with compliance: abstention failure goes to
# zero by construction and invocation to ~100%. Its use is to bound what the
# tools are worth when invocation is not the bottleneck, so that "the tool did
# not help" and "the tool was never asked" stay distinguishable. The reported
# comparison is baseline vs. tools.
INSTRUCTION = (
    "\n\nAny time a biological organism name or gene symbol appears -- in the question, in a "
    "document you are reading, or in an answer you are about to write -- call check_name first. "
    "It costs about 20 tokens and usually returns \"stable\", in which case you are done."
)

# The `server_instructions` condition.
#
# Distinct from `instructed`, and the distinction is the point. `instructed` is
# a hand-written imperative that exists to bound what the tools are worth when
# invocation is not the bottleneck; nobody ships it. `server_instructions` is
# the text the extension actually sends in its MCP `initialize` result, which at
# least one client renders into the system prompt verbatim under its own
# heading. So this condition measures a treatment that real users receive
# without doing anything -- which is what makes it reportable where `instructed`
# is not.
#
# The harness talks to the API directly rather than through MCP, so it has to
# inject the text itself. It comes from binomen.tool_descriptions, and
# tests/test_instructions_parity.py asserts that copy is byte-identical to the
# one the extension sends. Without that check this condition could report a
# clean number for a treatment nobody receives.
#
# Standing observation, n=1, recorded before any run: with this text live in a
# real client, "give me the latest research into candida auris" produced a
# taxonomic claim from memory and called no tool. See the domain-framed pair in
# the case set (recent-001 / recent-011).
CONDITIONS = ("baseline", "tools", "server_instructions", "instructed")

# Conditions in which the model is given tools at all.
WITH_TOOLS = ("tools", "server_instructions", "instructed")

SCHEMAS = {
    "check_name": {"type": "object", "properties": {"name": {"type": "string"}},
                   "required": ["name"]},
    "consult_authorities": {"type": "object", "properties": {
        "name": {"type": "string"}, "question": {"type": "string"},
        "as_of": {"type": "string"}}, "required": ["name"]},
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
        "check_name": lambda: resolver.check_name(args["name"]),
        "consult_authorities": lambda: resolver.consult_authorities(
            args["name"], args.get("question", "current_name"), args.get("as_of")),
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
                max_turns: int = 8) -> tuple[str, list[str], list[dict], dict]:
    messages = [{"role": "user", "content": case["prompt"]}]
    tools = build_tools() if condition in WITH_TOOLS else []
    system = SYSTEM
    if condition == "server_instructions":
        # Placed where a client puts it: appended to the system prompt, as its
        # own block, attributed to the server.
        system += f"\n\n# MCP Server Instructions\n\n## binomen\n\n{server_instructions()}"
    elif condition == "instructed":
        system += INSTRUCTION
    used: list[str] = []
    transcript: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "api_calls": 0}

    for _ in range(max_turns):
        kwargs = {"model": model, "max_tokens": 1400, "system": system,
                  "messages": messages}
        if tools:
            kwargs["tools"] = tools
        resp = client.messages.create(**kwargs)
        if getattr(resp, "usage", None):
            usage["input_tokens"] += getattr(resp.usage, "input_tokens", 0) or 0
            usage["output_tokens"] += getattr(resp.usage, "output_tokens", 0) or 0
        usage["api_calls"] += 1
        blocks = resp.content
        messages.append({"role": "assistant", "content": blocks})
        calls = [b for b in blocks if getattr(b, "type", "") == "tool_use"]
        text = "".join(getattr(b, "text", "") for b in blocks if getattr(b, "type", "") == "text")
        transcript.append({"assistant_text": text,
                           "tool_calls": [{"name": c.name, "input": c.input} for c in calls]})
        if not calls:
            return text, used, transcript, usage
        results = []
        for c in calls:
            used.append(c.name)
            out = dispatch(resolver, c.name, c.input)  # type: ignore[arg-type]
            results.append({"type": "tool_result", "tool_use_id": c.id,
                            "content": json.dumps(out, default=str)[:20000]})
        messages.append({"role": "user", "content": results})

    return "(max turns reached without a final answer)", used, transcript, usage


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=Path, default=HERE / "cases" / "cases.jsonl")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--condition", choices=[*CONDITIONS, "both", "all"], default="all",
                    help="'both' is baseline+tools (the reported comparison); "
                         "'all' adds server_instructions and the diagnostic-only "
                         "instructed condition")
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

    if a.condition == "all":
        conditions = list(CONDITIONS)
    elif a.condition == "both":
        conditions = ["baseline", "tools"]
    else:
        conditions = [a.condition]
    resolver = Resolver() if any(c in WITH_TOOLS for c in conditions) else None

    a.out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    totals = {"input_tokens": 0, "output_tokens": 0, "api_calls": 0}
    run_path = a.out / f"run-{stamp}.jsonl"
    meta = {
        "_meta": True, "model": a.model, "description_variant": variant(),
        # Which instructions text the `server_instructions` condition used.
        # Recorded unconditionally: a run that does not say which treatment it
        # applied cannot be compared with another one, and two runs of
        # `server_instructions` can now differ in the only way that matters.
        "instructions_variant": instructions_variant(),
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
                    answer, used, transcript, usage = run_episode(
                        client, a.model, case, cond, resolver)
                except Exception as e:  # noqa: BLE001
                    print(f"  [{case['id']}/{cond}] ERROR {e}", file=sys.stderr)
                    continue
                s = score_case(case, answer, used, cond)
                for k in totals:
                    totals[k] += usage[k]
                f.write(json.dumps({
                    "case_id": case["id"], "category": case["category"], "code": case["code"],
                    "condition": cond, "prompt": case["prompt"], "answer": answer,
                    "tools_used": used, "transcript": transcript, "score": s.to_dict(),
                    "usage": usage,
                }, ensure_ascii=False) + "\n")
                f.flush()
                flag = "OK " if s.passed else f"{s.error_class.value.upper():11s}"
                print(f"[{i:3d}/{len(cases)}] {case['id']:20s} {cond:8s} {flag} "
                      f"tools={len(used)}", file=sys.stderr)

    # Token accounting, printed so a small run can size a large one. The API is
    # billed separately from a Pro or Max subscription, so this is real money and
    # the harness should not be coy about it.
    n_episodes = len(cases) * len(conditions)
    print(f"\ntokens: {totals['input_tokens']:,} in / {totals['output_tokens']:,} out "
          f"across {totals['api_calls']} API calls, {n_episodes} episodes", file=sys.stderr)
    if n_episodes:
        print(f"per episode: {totals['input_tokens'] // n_episodes:,} in / "
              f"{totals['output_tokens'] // n_episodes:,} out", file=sys.stderr)
        full = 101 * len(conditions)
        print(f"extrapolated to the full dev set ({full} episodes): "
              f"~{totals['input_tokens'] * full // n_episodes:,} in / "
              f"~{totals['output_tokens'] * full // n_episodes:,} out", file=sys.stderr)
        print("Multiply by your model's per-token rates at platform.claude.com/settings/billing "
              "for the cost.", file=sys.stderr)
    print(f"\nwrote {run_path}\nnow: python eval/report.py {run_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
