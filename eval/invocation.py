#!/usr/bin/env python3
"""Measure invocation rate — does the model call the tool at all?

A separate, much cheaper instrument from `eval/runner.py`, because it answers a
different question.

`runner.py` scores answer quality across 102 cases through a full agentic loop:
tool calls executed, results fed back, multiple turns, then a severity-graded
score. That is the right shape for "does the tool help".

This is not that. Session 1 found the answer was good on *every* prompt where
the tool fired; the failure is entirely whether it fires. So the only thing
recorded here is whether the first assistant turn contains a `tool_use` block.
No tool is executed, no result is fed back, there is no second turn. One API
call per observation, which is what makes a grid of hundreds affordable.

What it varies
--------------
    prompt              framing is the variable that mattered in session 1
    descriptions        BINOMEN_DESCRIPTIONS: terse | broad
    instructions        BINOMEN_INSTRUCTIONS: terse | conditional |
                        unconditional | off
    model               documented to affect tool selection; observed to
    replicates          because a single sample cannot separate a treatment
                        from sampling noise

Usage
-----
    python eval/invocation.py --dry-run                    # grid size and cost
    python eval/invocation.py -n 5
    python eval/invocation.py --models claude-opus-5 claude-sonnet-5 -n 10
    python eval/invocation.py --instructions terse off -n 20
    python eval/invocation.py --report eval/runs/invocation-<stamp>.jsonl
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import itertools
import json
import math
import os
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

from binomen.tool_descriptions import DESCRIPTION_SETS, SERVER_INSTRUCTIONS_VARIANTS

TOOLS = ("check_name", "resolve_name", "get_synonyms", "expand_query")

# The same schema the extension ships. Kept here rather than imported from the
# Node file because the harness must send what the extension sends;
# tests/test_instructions_parity.py is what keeps the two honest.
NAME_ARG = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": (
                "A scientific name: binomial, genus, or a strain designation "
                "such as 'Clostridium difficile 630'. Abbreviated genus forms "
                "like 'C. difficile' are accepted."
            ),
        }
    },
    "required": ["name"],
}

# Deliberately plain. A system prompt that mentions names, taxonomy or checking
# anything would be a third treatment, and the one being measured is the
# server's own text.
SYSTEM_BASE = "You are assisting a working scientist. Answer the question directly and concisely."


def build_tools(desc_variant: str) -> list[dict]:
    d = DESCRIPTION_SETS[desc_variant]
    return [{"name": t, "description": d[t], "input_schema": NAME_ARG} for t in TOOLS]


def build_system(instr_variant: str) -> str:
    if instr_variant == "off":
        return SYSTEM_BASE
    text = SERVER_INSTRUCTIONS_VARIANTS[instr_variant]
    # Placed the way a client places it: its own block, attributed to the
    # server, ahead of the tools. Matching the real delivery matters -- an
    # instruction pasted somewhere else is a different treatment.
    return f"{SYSTEM_BASE}\n\n# MCP Server Instructions\n\n## binomen — biological name checker\n\n{text}"


def text_fingerprint(desc_variant: str, instr_variant: str) -> str:
    """Hash of the exact text sent, so a run is attributable after an edit.

    Variant *names* are not enough: `terse` meant something different three
    commits ago. A result recorded against a name that has since been rewritten
    is a result about nothing.
    """
    blob = json.dumps(
        {"d": DESCRIPTION_SETS[desc_variant],
         "i": "" if instr_variant == "off" else SERVER_INSTRUCTIONS_VARIANTS[instr_variant]},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def one_call(client, model: str, prompt: str, desc_variant: str, instr_variant: str,
             temperature: float, max_tokens: int) -> dict:
    """A single observation. Returns what happened, or what went wrong."""
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=build_system(instr_variant),
            tools=build_tools(desc_variant),
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}

    called = [b.name for b in resp.content if getattr(b, "type", None) == "tool_use"]
    return {
        "called": bool(called),
        "tools": called,
        "stop_reason": resp.stop_reason,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        # Recorded because a `max_tokens` stop means the model was still
        # writing and could in principle have called a tool afterwards. Those
        # rows are a known source of false negatives; see the report footer.
        "truncated": resp.stop_reason == "max_tokens",
    }


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval.

    Not the textbook normal approximation, which is badly wrong at the ends --
    and 0/5 and 5/5 are exactly the results this grid produces most often. A
    point estimate of 0.0 with no interval invites reading "never" from five
    observations.
    """
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def _cutoff(meta: dict, rows: list) -> tuple[int | None, bool]:
    """The max_text_chars this run used, and whether it had to be inferred.

    Older run files predate `max_text_chars` being recorded (fix 4), and the
    three files that exist when this was written were made at 600, 2000 and 400
    with nothing on disk saying so. For those, the smallest character count
    among truncated rows is a tight lower bound on the cutoff, because a row is
    truncated exactly when it crossed it. Marked `inferred` so nobody quotes it
    as fact.
    """
    if meta.get("max_text_chars"):
        return int(meta["max_text_chars"]), False
    seen = [r.get("text_chars_before_stop") for r in rows
            if r.get("truncated") and isinstance(r.get("text_chars_before_stop"), int)]
    return (min(seen), True) if seen else (None, True)


CONTROL_FRAMING = "name_framed"


def _is_dead(r: dict) -> bool:
    """True for a row where the process produced nothing at all.

    Not a model behaviour. A `claude -p` run that answers writes prose; one
    that calls a tool emits a `tool_use` block. A row with neither, that was
    not cut off at the cutoff, is a run that never produced output -- rate
    limiting, a dropped stream, an auth expiry part-way through a session.

    It carries no `error` key, so rule 8 ("errored rows are excluded, not
    counted as zero") never sees it, and it lands in the denominator as a
    non-fire. That happened: 20260814-205716 recorded 12 of these, nine of
    them the entire final replicate, and printed 45-row rates over 33 real
    observations. The positive control read 4/5 when it had in fact fired on
    every observation that existed.

    Deliberately narrow. A `truncated` row produced plenty of text and is a
    different, already-handled case. A missing `text_chars_before_stop` -- the
    Messages API harness does not record one -- is not evidence of anything, so
    the isinstance check is load-bearing rather than defensive.
    """
    if r.get("called") or r.get("truncated"):
        return False
    chars = r.get("text_chars_before_stop")
    if isinstance(chars, int) and chars == 0:
        return True
    # Messages API side: an empty completion. Same failure, different record.
    return r.get("output_tokens") == 0


def _void_replicates(rows: list) -> set:
    """Condition x replicate keys whose positive control did not fire.

    TESTING.md rule 1 applies the control per BLOCK. That is too coarse. The
    replicate is the unit that actually fails together, because replicates are
    outermost in the grid -- the property that makes an interrupted run leave a
    balanced partial grid also means a throttled or expired session kills whole
    sweeps rather than single cells. A dead sweep hits every prompt equally,
    which is precisely what a uniform null effect looks like. The artifact
    disguises itself as the result.

    Computed over rows that still include the dead ones, on purpose: a control
    row that produced no output is not evidence the model declined, it is
    evidence the harness was not working -- the stronger reason to throw the
    sweep away. A replicate containing no control row at all is not evidence
    either way and is left alone.
    """
    keys = ("model", "descriptions", "instructions", "replicate")
    void = set()
    for key in {tuple(r.get(k) for k in keys) for r in rows}:
        ctrl = [r for r in rows
                if tuple(r.get(k) for k in keys) == key
                and r.get("framing") == CONTROL_FRAMING
                and r.get("expect_call") is True]
        if ctrl and not any(r.get("called") for r in ctrl):
            void.add(key)
    return void


def _cell(rows: list) -> dict:
    """Fired / censored / latency for one group of observations.

    `censored` is the number of NON-FIRES that were cut off rather than
    observed to completion, and it is reported as a fraction of non-fires
    rather than of all rows. A global truncation fraction reads as mild -- "6
    of 26" -- while the number that decides whether a zero means anything is
    "6 of 6 non-fires", i.e. every zero in the cell is unobserved. That
    distinction cost a whole 26-run block.
    """
    fired = [r for r in rows if r.get("called")]
    nonf = [r for r in rows if not r.get("called")]
    cens = [r for r in nonf if r.get("truncated")]
    lat = [r["text_chars_before_stop"] for r in fired
           if isinstance(r.get("text_chars_before_stop"), int)]
    return {"n": len(rows), "k": len(fired), "nonfire": len(nonf),
            "censored": len(cens), "max_fire_char": max(lat) if lat else None}


def _classes(rows: list) -> dict:
    """Split by declared ground truth, not by framing.

    `expect_call` is the label the prompt author committed to; `framing` is
    descriptive. They can disagree -- ot-02 is tagged `over_trigger_stable`
    with organism "Homo sapiens" but its text names no organism at all -- and
    when they do, the declared label is the one to score against. `None` means
    the author deliberately refused to call it right or wrong; those rows are
    reported and never folded into TPR or FPR.
    """
    return {
        "pos": [r for r in rows if r.get("expect_call") is True],
        "neg": [r for r in rows if r.get("expect_call") is False],
        "unscored": [r for r in rows if r.get("expect_call") is None],
    }


def report(path: Path) -> None:
    all_rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    meta = next((r for r in all_rows if r.get("_meta")), {})
    body = [r for r in all_rows if not r.get("_meta")]
    errored = [r for r in body if "error" in r]
    live = [r for r in body if "error" not in r]

    # Order matters. The control check runs over `live`, dead rows included,
    # so a sweep whose control row is itself dead still voids -- see
    # _void_replicates. Removal then happens in one pass.
    rkeys = ("model", "descriptions", "instructions", "replicate")
    void_keys = _void_replicates(live)
    dead = [r for r in live if _is_dead(r)]
    voided = [r for r in live
              if tuple(r.get(k) for k in rkeys) in void_keys and not _is_dead(r)]
    rows = [r for r in live
            if not _is_dead(r) and tuple(r.get(k) for k in rkeys) not in void_keys]
    if not rows:
        print("no usable rows")
        return

    cutoff, inferred = _cutoff(meta, rows)
    warnings: list[str] = []

    print(f"\n{path.name}")
    if meta:
        print(f"  temperature {meta.get('temperature')}  max_tokens {meta.get('max_tokens')}")
    if cutoff is not None:
        print(f"  max_text_chars {cutoff}{'  (INFERRED -- not recorded in this file)' if inferred else ''}")
    dropped = ""
    if dead or voided:
        parts = []
        if dead:
            parts.append(f"{len(dead)} produced no output")
        if voided:
            parts.append(f"{len(voided)} in {len(void_keys)} replicate(s) whose control failed")
        dropped = f", {len(dead) + len(voided)} VOID ({'; '.join(parts)})"
    print(f"  {len(rows)} usable rows, {len(errored)} errored{dropped}")
    print()

    keys = ("model", "descriptions", "instructions")
    combos = sorted({tuple(r[k] for k in keys) for r in rows})

    print("BY CONDITION")
    for combo in combos:
        crows = [r for r in rows if tuple(r[k] for k in keys) == combo]
        fps = {r.get("fingerprint") for r in crows}
        fp = next(iter(fps)) if len(fps) == 1 else f"MIXED {sorted(fps)}"
        print(f"  {combo[0]} / desc={combo[1]} / instr={combo[2]}   fingerprint {fp}")
        # Fingerprint drift is only drift WITHIN one condition. Comparing across
        # conditions is the entire point of a grid, and the old check warned on
        # every multi-arm run -- a warning that always fires is one nobody reads.
        if len(fps) > 1:
            warnings.append(
                f"condition {combo} spans {len(fps)} fingerprints -- the wording changed "
                f"mid-run for a single condition. Do not pool these rows.")
        print(f"    {'framing':<21}{'fired':>8}  {'95% CI':<12}{'censored':<11}max fire char")
        for framing in ("name_framed", "domain_framed", "over_trigger_stable", "no_organism"):
            sub = [r for r in crows if r.get("framing") == framing]
            if not sub:
                continue
            c = _cell(sub)
            lo, hi = wilson(c["k"], c["n"])
            cens = f"{c['censored']}/{c['nonfire']}" if c["nonfire"] else "-"
            flag = ""
            if c["nonfire"] and c["censored"] == c["nonfire"]:
                flag = " ALL"
                warnings.append(
                    f"{combo[2]}/{framing}: every one of the {c['nonfire']} non-fires was cut "
                    f"off at the cutoff, so this cell's rate is a LOWER BOUND, not a rate.")
            mfc = c["max_fire_char"]
            print(f"    {framing:<21}{c['k']:>3}/{c['n']:<4}{lo:.2f}-{hi:.2f}  "
                  f"{cens + flag:<11}{mfc if mfc is not None else '-'}")
            for pid in sorted({r["prompt_id"] for r in sub}):
                psub = [r for r in sub if r["prompt_id"] == pid]
                pc = _cell(psub)
                # Per-prompt, because pooling hides everything. In one real run
                # `over_trigger_stable 5/10` was ot-01 at 5/5 and ot-02 at 0/5 --
                # two opposite behaviours averaged into a number describing neither.
                tail = f"{'':<14}{pc['censored']}/{pc['nonfire']}" if pc["nonfire"] else ""
                print(f"      {pid:<19}{pc['k']:>3}/{pc['n']:<4}{tail}")
        print()

    print("DETECTION SUMMARY   (from expect_call; unscored rows excluded from both rates)")
    print(f"  {'condition':<34}{'TPR (should fire)':<26}{'FPR (should not)':<26}unscored")
    for combo in combos:
        crows = [r for r in rows if tuple(r[k] for k in keys) == combo]
        cl = _classes(crows)
        def fmt(sub):
            if not sub:
                return f"{'-':<26}"
            c = _cell(sub)
            lo, hi = wilson(c["k"], c["n"])
            bound = ">=" if c["nonfire"] and c["censored"] == c["nonfire"] else ""
            return f"{bound:<2}{c['k']:>3}/{c['n']:<4}[{lo:.2f}-{hi:.2f}]{'':<7}"
        u = _cell(cl["unscored"])
        label = f"{combo[0]}/{combo[1]}/{combo[2]}"
        uns = f"{u['k']}/{u['n']}" if cl["unscored"] else "-"
        print(f"  {label:<34}{fmt(cl['pos'])}{fmt(cl['neg'])}{uns}")
    print("  >= marks a rate whose zeros are all censored: a lower bound, not an estimate.")
    print()

    # --- cutoff margin -----------------------------------------------------
    lat = [r["text_chars_before_stop"] for r in rows
           if r.get("called") and isinstance(r.get("text_chars_before_stop"), int)]
    if lat and cutoff:
        mx = max(lat)
        print(f"CUTOFF MARGIN  max observed fire at {mx} chars, cutoff {cutoff} "
              f"({cutoff / max(mx, 1):.1f}x)")
        # A tool call that starts just under the cutoff means the cutoff is
        # inside the distribution being measured, not safely outside it. 400 was
        # chosen from 12 rows that showed nothing above 108; 26 rows then produced
        # a fire at 368, against a 400 cutoff.
        if mx > cutoff / 2:
            warnings.append(
                f"the latest observed tool call was at {mx} chars against a {cutoff}-char "
                f"cutoff ({cutoff / max(mx, 1):.1f}x margin). The cutoff is inside the "
                f"latency distribution; raise it before trusting any zero.")
        print()

    # --- void rows ---------------------------------------------------------
    # Printed separately from errors because they fail differently: an error
    # announced itself, these did not. A run can look clean -- "errors=0" --
    # and still be a third dead.
    if dead or voided:
        print(f"VOID  {len(dead) + len(voided)} rows excluded, NOT counted as zeros")
        if dead:
            by_prompt = Counter(r.get("prompt_id") for r in dead)
            print(f"    no output at all ({len(dead)})")
            for pid, n in by_prompt.most_common():
                print(f"      {pid:<17}{n}")
        for key in sorted(void_keys, key=str):
            print(f"    control did not fire: {key[0]}/{key[1]}/{key[2]} replicate {key[3]}")
        warnings.append(
            f"{len(dead) + len(voided)} rows produced no observation and were dropped. "
            f"Any rate printed above is over what remains; compare denominators, not "
            f"just rates, against earlier runs of the same grid.")
        reps = {r.get("replicate") for r in dead}
        if dead and len(reps) <= 2 and max(reps, default=0) > 0:
            # Concentrated at the tail of the session rather than scattered is
            # the signature of throttling or an expiring session, not of noise.
            warnings.append(
                f"the dead rows sit in replicate(s) {sorted(reps)} -- the end of the "
                f"session, not spread through it. That is rate limiting or an expiring "
                f"auth, and it means the run stopped early rather than finished.")
        print()

    # --- errors ------------------------------------------------------------
    if errored:
        by_prompt = Counter(r.get("prompt_id") for r in errored)
        print(f"ERRORS  {len(errored)} rows excluded")
        for pid, n in by_prompt.most_common():
            print(f"    {pid:<10}{n}")
        top, ntop = by_prompt.most_common(1)[0]
        # Errors are excluded rather than counted as zero, which is only the safe
        # choice when they are independent of the outcome. Concentrated on one
        # prompt they are not: a cp1252 decode failure on a micro-sign answer kills the
        # runs that WROTE an answer and spares the ones that called a tool
        # immediately, so exclusion deletes the zeros and inflates the rate.
        if len(errored) >= 3 and ntop / len(errored) > 0.5:
            warnings.append(
                f"{ntop} of {len(errored)} errors are the same prompt ({top}). Errors are "
                f"excluded, not zeroed -- which is only safe if they are independent of "
                f"whether a tool fired. Concentrated like this they are probably not.")
        print()

    if warnings:
        print("WARNINGS")
        for w in warnings:
            print(f"  ! {w}")
        print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts", type=Path, default=HERE / "prompts_invocation.jsonl")
    ap.add_argument("--models", nargs="+", default=["claude-opus-5"])
    ap.add_argument("--descriptions", nargs="+", default=["terse"],
                    choices=list(DESCRIPTION_SETS))
    ap.add_argument("--instructions", nargs="+", default=["terse"],
                    choices=[*SERVER_INSTRUCTIONS_VARIANTS, "off"])
    ap.add_argument("-n", "--replicates", type=int, default=5)
    ap.add_argument("--temperature", type=float, default=1.0,
                    help="1.0 by default: the grid must reflect how the tool is actually "
                         "used, and temperature 0 would measure a mode nobody runs in")
    ap.add_argument("--max-tokens", type=int, default=700)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", type=Path, default=HERE / "runs")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", type=Path, help="summarise an existing run and exit")
    a = ap.parse_args()

    if a.report:
        report(a.report)
        return 0

    prompts = [json.loads(l) for l in a.prompts.read_text(encoding="utf-8").splitlines()
               if l.strip()]

    grid = list(itertools.product(a.models, a.descriptions, a.instructions, prompts,
                                  range(a.replicates)))
    print(f"{len(prompts)} prompts x {len(a.models)} models x {len(a.descriptions)} desc "
          f"x {len(a.instructions)} instr x {a.replicates} reps = {len(grid)} API calls")

    # Cost is dominated by output tokens on the runs that do NOT call a tool:
    # those write a full answer. A tool call stops almost immediately.
    est_in = 400
    print(f"  rough estimate: ~{len(grid) * est_in / 1000:.0f}k input tokens, "
          f"up to ~{len(grid) * a.max_tokens / 1000:.0f}k output tokens if nothing fires")
    print("  multiply by your per-token rates at platform.claude.com/settings/billing")
    if a.dry_run:
        return 0

    try:
        from anthropic import Anthropic
    except ImportError:
        print("pip install anthropic", file=sys.stderr)
        return 1
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Claude Pro does not include API access; "
              "billing is separate at platform.claude.com.", file=sys.stderr)
        return 1
    client = Anthropic()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    a.out.mkdir(parents=True, exist_ok=True)
    path = a.out / f"invocation-{stamp}.jsonl"

    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "_meta": True, "started": stamp, "temperature": a.temperature,
            "max_tokens": a.max_tokens, "replicates": a.replicates,
            "models": a.models, "descriptions": a.descriptions,
            "instructions": a.instructions,
            "fingerprints": {f"{d}/{i}": text_fingerprint(d, i)
                             for d in a.descriptions for i in a.instructions},
        }) + "\n")

        done = 0
        with cf.ThreadPoolExecutor(max_workers=a.concurrency) as ex:
            futures = {
                ex.submit(one_call, client, model, p["prompt"], desc, instr,
                          a.temperature, a.max_tokens): (model, desc, instr, p, rep)
                for model, desc, instr, p, rep in grid
            }
            for fut in cf.as_completed(futures):
                model, desc, instr, p, rep = futures[fut]
                row = {
                    "model": model, "descriptions": desc, "instructions": instr,
                    "fingerprint": text_fingerprint(desc, instr),
                    "prompt_id": p["id"], "framing": p["framing"],
                    "organism": p.get("organism"), "expect_call": p.get("expect_call"),
                    "replicate": rep, **fut.result(),
                }
                f.write(json.dumps(row) + "\n")
                f.flush()
                done += 1
                if done % 10 == 0 or done == len(grid):
                    print(f"  {done}/{len(grid)}", end="\r", flush=True)

    print(f"\nwrote {path}")
    report(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
