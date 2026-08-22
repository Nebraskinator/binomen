#!/usr/bin/env python3
"""Invocation grid via Claude Code, for people without API billing.

Same measurement as `eval/invocation.py` -- did a binomen tool get called --
but driven through `claude -p`, which authenticates with a Claude subscription
instead of an API key. Rows are written in the same shape, so
`invocation.py --report` summarises either.

    python eval/invocation_cc.py --smoke          # one call, prove the wiring
    python eval/invocation_cc.py -n 5
    python eval/invocation_cc.py --models opus sonnet --instructions terse off -n 5

READ THIS BEFORE BELIEVING A NUMBER FROM IT
-------------------------------------------
**Claude Code is a coding assistant, and that biases this measurement
downward.** In DISCOVERY-LOG session 2 it declined a taxonomy prompt outright:
"this is a medical/clinical question unrelated to the software engineering
context this environment is set up for". A tool-use rate measured here is a
lower bound on what the same wording would produce in Claude Desktop, and the
gap is not quantified.

This runner reduces that bias where it can -- it appends a plain
working-scientist framing to the system prompt, and runs in an empty temporary
directory so no CLAUDE.md, hook, plugin or project MCP config is loaded -- but
it cannot remove Claude Code's own system prompt.

So: use this to compare conditions **against each other**, on the same host, in
the same session. Do not report an absolute rate from it, and do not compare a
number from here against a number from Claude Desktop. Those are different
environments and the difference is the size of the thing being measured.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src"))

from invocation import SYSTEM_BASE, report, text_fingerprint  # noqa: E402

SERVER = REPO / "node" / "src" / "server.js"
TOOL_PREFIX = "mcp__binomen__"
ALLOWED = ",".join(f"{TOOL_PREFIX}{t}" for t in
                   ("check_name", "resolve_name", "get_synonyms", "expand_query"))


def mcp_config(desc: str, instr: str) -> str:
    """An MCP config naming the working copy, with the variant env vars set.

    Points at node/src/server.js rather than the installed extension on
    purpose: the installed copy is whatever was last double-clicked, and a grid
    that silently measures a stale build is worse than no grid.
    """
    return json.dumps({
        "mcpServers": {
            "binomen": {
                "command": "node",
                "args": [str(SERVER)],
                "env": {"BINOMEN_DESCRIPTIONS": desc, "BINOMEN_INSTRUCTIONS": instr},
            }
        }
    })


def _one_call(model: str, prompt: str, desc: str, instr: str, timeout: int,
              max_text_chars: int = 600) -> dict:
    """One observation, stopping as early as the answer allows.

    Cost, not elegance, drives the shape of this. Every run is a full agent
    session billed against a subscription, and the expensive case is the one
    where NO tool fires -- the model then writes a complete answer that is
    thrown away, because the only thing recorded is whether a tool_use block
    appeared.

    So the stream is read incrementally and the process is killed at the first
    of:

      1. a binomen tool_use block  -> fired, and nothing after it is needed
      2. `max_text_chars` of assistant text with no tool call yet

    Case 2 is a real trade and is recorded as `truncated`. A model that wrote
    600 characters of prose and *then* called a tool would be scored here as
    not firing. That is a known, one-directional bias: it can only
    under-report invocation, never over-report it. Raise --max-text-chars to
    trade money for certainty; the report counts truncated rows so the trade
    stays visible.
    """
    # ignore_cleanup_errors: on Windows the killed `claude` may still hold a
    # handle on its working directory when this block exits. Losing a temp
    # directory is not worth losing an observation over.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as cwd:
        cmd = [
            "claude", "-p", prompt,
            "--model", model,
            "--mcp-config", mcp_config(desc, instr),
            "--output-format", "stream-json",
            "--verbose", "--include-partial-messages",
            "--allowedTools", ALLOWED,
            "--append-system-prompt", SYSTEM_BASE,
        ]
        # encoding="utf-8" is load-bearing, not tidiness. `text=True` alone
        # decodes with the locale default, which on Windows is cp1252, and
        # Claude Code emits UTF-8.
        #
        # The mechanism is worth stating precisely, because the obvious guess
        # is wrong. It is NOT "the answer contained a special character": most
        # of them survive cp1252, wrongly. Micro sign, en dash and times sign
        # all have cp1252 code points, so their UTF-8 bytes decode to mojibake
        # ("Âµg/mL") with no error raised at all -- silent corruption that would
        # have landed in tool_args as a wrong name.
        #
        # The crash needs one of the five bytes cp1252 leaves UNDEFINED --
        # 0x81, 0x8D, 0x8F, 0x90, 0x9D -- which appear as UTF-8 continuation
        # bytes inside characters such as U+2081 or U+207B. Observed live as
        # `UnicodeDecodeError: 'charmap' codec can't decode byte 0x81`.
        #
        # That failure is NOT independent of the outcome, which is what makes
        # it dangerous rather than merely annoying. A run that calls a tool
        # immediately emits almost no prose and never reaches the offending
        # character; a run that writes a full answer does. So the crash
        # preferentially deletes the NON-FIRING runs, and because errored rows
        # are excluded rather than zeroed, the surviving rate is inflated. One
        # 90-run grid reported a 100% false-positive rate on the one prompt
        # whose answers mention ug/mL; 7 of its 10 runs had died this way.
        #
        # errors="replace" so a decode problem can never again remove a row:
        # the byte becomes U+FFFD, the JSON still parses, the observation
        # survives. This project has now been bitten by cp1252 twice.
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True,
                                encoding="utf-8", errors="replace", bufsize=1)

        called: list[str] = []
        args: list[dict] = []
        server_ok = None
        mcp_err = None
        text_seen = 0
        truncated = False
        deadline = time.time() + timeout
        # Index of a tool_use block whose arguments are still streaming, and
        # the partial JSON accumulated for it so far.
        pending_idx = None
        pending_json = ""
        seen_ids: set = set()
        id_order: list = []

        try:
            for line in proc.stdout:
                if time.time() > deadline:
                    proc.kill()
                    return {"error": "timeout"}
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if ev.get("type") == "system" and ev.get("subtype") == "init":
                    servers = {x.get("name"): x.get("status")
                               for x in ev.get("mcp_servers", [])}
                    server_ok = servers.get("binomen")
                    if ev.get("mcp_server_errors"):
                        mcp_err = ev["mcp_server_errors"]
                        break

                # Complete assistant message. Arguments are already whole here.
                #
                # This can arrive for a tool_use whose content_block_start was
                # already seen on the partial stream, so both paths must
                # deduplicate on the block id. Without that the same call is
                # recorded twice -- caught by running --smoke, which reported
                # `expand_query` twice for a single call.
                if ev.get("type") == "assistant":
                    for b in ev.get("message", {}).get("content", []):
                        if b.get("type") != "tool_use" or \
                                not b.get("name", "").startswith(TOOL_PREFIX):
                            continue
                        bid = b.get("id")
                        if bid in seen_ids:
                            # Already counted from the stream; fill in the
                            # arguments if they had not finished arriving.
                            i = id_order.index(bid)
                            if not args[i]:
                                args[i] = b.get("input") or {}
                            continue
                        seen_ids.add(bid)
                        id_order.append(bid)
                        called.append(b["name"].removeprefix(TOOL_PREFIX))
                        args.append(b.get("input") or {})
                    if called:
                        break

                # Partial stream: catch a tool call the moment it starts, and
                # count text so a non-firing run can be cut short.
                if ev.get("type") == "stream_event":
                    e = ev.get("event", {})
                    cb = e.get("content_block", {})
                    if cb.get("type") == "tool_use" and \
                            cb.get("name", "").startswith(TOOL_PREFIX) and \
                            cb.get("id") not in seen_ids:
                        seen_ids.add(cb.get("id"))
                        id_order.append(cb.get("id"))
                        called.append(cb["name"].removeprefix(TOOL_PREFIX))
                        args.append({})
                        # `content_block_start` carries the tool NAME but an
                        # empty `input`: arguments arrive afterwards as
                        # input_json_delta fragments. Breaking here -- which is
                        # what this did -- records that something fired but not
                        # what it was asked. For a false positive that is the
                        # whole question: two rows called check_name on a prompt
                        # naming no organism, and nothing on disk says what name
                        # was passed. So keep reading to content_block_stop.
                        # Costs a few dozen tokens, and only on runs that fired,
                        # which are the cheap ones anyway.
                        pending_idx = e.get("index")
                        pending_json = ""
                        if pending_idx is None:
                            break          # no index to follow; name only
                        continue

                    d = e.get("delta", {})
                    if pending_idx is not None and d.get("type") == "input_json_delta" \
                            and e.get("index") == pending_idx:
                        pending_json += d.get("partial_json", "")
                        continue
                    if pending_idx is not None and e.get("type") == "content_block_stop" \
                            and e.get("index") == pending_idx:
                        try:
                            args[-1] = json.loads(pending_json) if pending_json else {}
                        except json.JSONDecodeError:
                            # Record the raw fragment rather than dropping it. An
                            # unparseable argument is itself worth seeing.
                            args[-1] = {"_unparsed": pending_json[:200]}
                        break

                    if d.get("type") == "text_delta":
                        text_seen += len(d.get("text", ""))
                        if text_seen >= max_text_chars:
                            truncated = True
                            break
        finally:
            if proc.poll() is None:
                proc.kill()
                # kill() only signals. On Windows the process holds a lock on
                # `cwd` until it has actually exited, and the enclosing
                # TemporaryDirectory cleanup runs the moment this block ends.
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            try:
                proc.stdout.close()
            except Exception:  # noqa: BLE001
                pass
            stderr_tail = (proc.stderr.read() or "")[-300:] if proc.stderr else ""
            try:
                proc.stderr.close()
            except Exception:  # noqa: BLE001
                pass

    if mcp_err:
        return {"error": f"mcp_server_errors: {mcp_err}"}
    if server_ok is None:
        return {"error": f"no system/init seen; stderr={stderr_tail}"}
    if server_ok not in ("connected", "pending"):
        return {"error": f"binomen did not attach (status={server_ok}); stderr={stderr_tail}"}

    # If the stream ended mid-arguments, keep `tools` and `args` aligned rather
    # than letting a row claim arguments belonging to a different call.
    while len(args) < len(called):
        args.append({})
    args = [a if a else {"_incomplete": True} for a in args]

    return {
        "called": bool(called),
        "tools": called,
        "tool_args": args,
        "server_status": server_ok,
        "text_chars_before_stop": text_seen,
        "truncated": truncated,
    }


def one_call(*args, **kwargs) -> dict:
    """Never raise.

    The first version let an exception out of the worker, where it reached
    `fut.result()` and ended the whole run -- on a Windows temp-directory
    cleanup race, of all things. An expensive partial grid should not be
    destroyed by one row failing for a reason unrelated to what is being
    measured. Failures become recorded errors instead, which the summary counts
    and excludes.
    """
    try:
        return _one_call(*args, **kwargs)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def already_done(path: Path) -> set:
    """Cells already recorded, so an interrupted grid can be resumed.

    A subscription run will hit a rate limit partway through. Without this the
    only options are re-running everything or pooling two partial files by
    hand, and the second is how a grid ends up with uneven replicate counts
    nobody notices.
    """
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("_meta") or r.get("error"):
            continue
        done.add((r["model"], r["descriptions"], r["instructions"],
                  r["prompt_id"], r["replicate"]))
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts", type=Path, default=HERE / "prompts_invocation.jsonl")
    ap.add_argument("--models", nargs="+", default=["opus"])
    ap.add_argument("--descriptions", nargs="+", default=["terse"])
    ap.add_argument("--instructions", nargs="+", default=["terse"])
    ap.add_argument("-n", "--replicates", type=int, default=5)
    ap.add_argument("--concurrency", type=int, default=2,
                    help="keep low: a subscription has rate limits, and a throttled "
                         "run that errors out looks like a run where nothing fired")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--max-text-chars", type=int, default=600,
                    help="kill a non-firing run after this much prose. Lower is "
                         "cheaper and under-reports invocation; the bias only ever "
                         "runs one way")
    ap.add_argument("--max-runs", type=int,
                    help="stop after this many calls. A partial grid you can afford "
                         "beats a whole one you abandon halfway")
    ap.add_argument("--resume", type=Path,
                    help="append to an existing run file, skipping cells already in it")
    ap.add_argument("--minimal", action="store_true",
                    help="the smallest grid that answers something: the prompt that "
                         "failed live, plus a positive control, across two instruction "
                         "variants")
    ap.add_argument("--plan", action="store_true",
                    help="print exactly what would run, and stop")
    ap.add_argument("--out", type=Path, default=HERE / "runs")
    ap.add_argument("--smoke", action="store_true",
                    help="one call on the positive-control prompt, printed in full")
    a = ap.parse_args()

    if shutil.which("claude") is None:
        print("claude CLI not found. Install Claude Code first.", file=sys.stderr)
        return 1
    if not SERVER.exists():
        print(f"missing {SERVER}", file=sys.stderr)
        return 1

    prompts = [json.loads(line) for line in a.prompts.read_text(encoding="utf-8").splitlines()
               if line.strip()]

    if a.smoke:
        p = next(x for x in prompts if x["id"] == "nf-01")
        print(f"model={a.models[0]}  desc={a.descriptions[0]}  instr={a.instructions[0]}")
        print(f"prompt: {p['prompt']}\n")
        out = one_call(a.models[0], p["prompt"], a.descriptions[0], a.instructions[0],
                       a.timeout, a.max_text_chars)
        print(json.dumps(out, indent=2)[:1500])
        if out.get("error"):
            print("\nFix this before running the grid: an error here would be recorded "
                  "as 'did not fire' for every row.")
            return 1
        if not out.get("called"):
            print("\nThe positive control did not fire. Either the wiring is wrong or "
                  "this model does not call the tool at all -- and the second is a "
                  "finding, not a bug. Try --models opus.")
        return 0

    if a.minimal:
        # df-01 is the prompt that failed live; nf-01 is the control that fired.
        # One replicate of the control per condition is enough -- it is a
        # validity check, not a measurement, and spending five on it buys
        # nothing.
        keep = {"df-01", "nf-01"}
        prompts = [p for p in prompts if p["id"] in keep]

    # Replicate-major order: sweep the entire grid once, then again.
    #
    # The first version put replicates innermost, so a run that stopped early
    # had five replicates of the first prompt and nothing of the rest. A whole
    # session was spent that way and never reached the domain-framed prompt it
    # existed to test.
    #
    # This order means any interruption leaves a *balanced* partial grid --
    # n=2 across every condition rather than n=5 on one corner. With a budget
    # that can run out mid-run, which cells are missing matters more than how
    # many.
    grid = [(m, d, i, p, r)
            for r in range(a.replicates)
            for m in a.models for d in a.descriptions for i in a.instructions
            for p in prompts
            if not (a.minimal and p["id"] == "nf-01" and r > 0)]

    done = already_done(a.resume) if a.resume else set()
    if done:
        before = len(grid)
        grid = [g for g in grid
                if (g[0], g[1], g[2], g[3]["id"], g[4]) not in done]
        print(f"resuming {a.resume.name}: {before - len(grid)} cells already recorded")

    if a.max_runs:
        grid = grid[: a.max_runs]
    if a.plan:
        for m, d, i, p, r in grid:
            print(f"  {m:<8} desc={d:<6} instr={i:<14} {p['id']:<6} rep={r}  {p['prompt'][:52]}")
        print(f"\n{len(grid)} runs")
        return 0

    print(f"{len(grid)} runs "
          f"({len(prompts)} prompts x {len(a.models)} models x {len(a.descriptions)} desc "
          f"x {len(a.instructions)} instr x {a.replicates} reps)")
    print("Each run is a separate claude -p invocation. Expect minutes, not seconds,")
    print("and watch for rate limiting -- errored rows are dropped, not counted as 0.\n")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    a.out.mkdir(parents=True, exist_ok=True)
    path = a.resume if a.resume else a.out / f"invocation-cc-{stamp}.jsonl"
    mode = "a" if a.resume else "w"

    with open(path, mode, encoding="utf-8") as f:
        if not a.resume:
            f.write(json.dumps({
            "_meta": True, "harness": "claude-code", "started": stamp,
            "replicates": a.replicates, "models": a.models,
            "descriptions": a.descriptions, "instructions": a.instructions,
            "temperature": "n/a (claude -p)", "max_tokens": "n/a",
            # Recorded because it decides what a zero in this file MEANS, and
            # was not recorded for the first four run files -- which were made
            # at 600, 2000, 400 and 600 with nothing on disk saying so. The
            # fingerprint covers description and instruction text only, so
            # nothing else in the row catches a cutoff change.
            "max_text_chars": a.max_text_chars,
            "prompts_file": a.prompts.name,
            "caveat": "Claude Code is a coding assistant; rates are a lower bound "
                          "and are not comparable to Claude Desktop.",
            }) + "\n")

        done = errors = 0
        with cf.ThreadPoolExecutor(max_workers=a.concurrency) as ex:
            futs = {ex.submit(one_call, m, p["prompt"], d, i, a.timeout,
                              a.max_text_chars): (m, d, i, p, r)
                    for m, d, i, p, r in grid}
            for fut in cf.as_completed(futs):
                m, d, i, p, r = futs[fut]
                res = fut.result()
                errors += bool(res.get("error"))
                f.write(json.dumps({
                    "model": m, "descriptions": d, "instructions": i,
                    "fingerprint": text_fingerprint(d, i),
                    "prompt_id": p["id"], "framing": p["framing"],
                    "organism": p.get("organism"), "expect_call": p.get("expect_call"),
                    "replicate": r, **res,
                }) + "\n")
                f.flush()
                done += 1
                print(f"  {done}/{len(grid)}  errors={errors}", end="\r", flush=True)

    print(f"\nwrote {path}")
    if errors:
        print(f"  {errors} runs errored and are excluded from the summary. If that is a "
              f"large fraction, the numbers below are not trustworthy.")
    report(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
