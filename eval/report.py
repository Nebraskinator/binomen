#!/usr/bin/env python3
"""Turn a run file into the tables that go in the README.

Reporting rule, enforced here rather than left to discipline: there is no
single headline accuracy number in this output. A single number averages a
silent join failure against a missing citation, which is the measurement error
the whole error taxonomy exists to avoid. Results are reported by category and
by error class, with tool-invocation rate alongside, and that is deliberate.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from taxonomy import DEFINITIONS, ErrorClass

ORDER = ["control", "historic", "recent", "split", "homonym", "distractor", "multihop",
         "literature", "crosscode", "contested", "gene"]
ECLASSES = [ErrorClass.VERY_MAJOR, ErrorClass.MAJOR, ErrorClass.MINOR, ErrorClass.NONE]


def md_table(headers, rows) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def pct(n, d) -> str:
    return "-" if not d else f"{100 * n / d:.0f}%"


def _invocation_block(parts: list, by: dict, cond: str) -> None:
    """Invocation and abstention for one tool-bearing condition.

    Reported per condition rather than once, because the whole point of the
    `instructed` condition is that this number moves and the correctness numbers
    follow it.
    """
    rows = [x for (c, cd), lst in by.items() if cd == cond for x in lst]
    if not rows:
        return
    called = sum(1 for x in rows if x["tool_called"])
    abst = sum(1 for x in rows if x["abstention_failure"])
    lucky = sum(1 for x in rows if x["abstention_failure"] and x["passed"])
    parts.append(f"\n**{cond}**\n")
    parts.append(md_table(
        ["measure", "value", "meaning"],
        [["tool invocation rate", pct(called, len(rows)),
          "cases where at least one tool was called"],
         ["abstention failures", f"{abst} ({pct(abst, len(rows))})",
          "a tool was available and needed, and was not called"],
         ["correct but unchecked", f"{lucky}",
          "answered correctly without checking -- lucky, not reliable"]]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)

    lines = [json.loads(line) for line in a.run.read_text().splitlines() if line.strip()]
    meta = next((x for x in lines if x.get("_meta")), {})
    rows = [x for x in lines if not x.get("_meta")]
    if not rows:
        print("no scored cases in run file", file=sys.stderr)
        return 1

    by = defaultdict(list)
    for r in rows:
        by[(r["category"], r["condition"])].append(r["score"])
    # Canonical order, not alphabetical: the story runs left to right.
    order = ["baseline", "tools", "instructed"]
    present = {r["condition"] for r in rows}
    conditions = [c for c in order if c in present] + sorted(present - set(order))

    parts: list[str] = []
    parts.append("## Results\n")
    parts.append(
        f"Model `{meta.get('model', '?')}` · tool descriptions `{meta.get('description_variant', '?')}` · "
        f"index `{meta.get('index_version', '?')}` · {meta.get('n_cases', len(rows))} cases · "
        f"live authorities {'disabled (cached)' if meta.get('offline') else 'ENABLED (not reproducible)'}\n"
    )
    if meta.get("unverified_cases"):
        parts.append(
            f"> **{meta['unverified_cases']} cases have unverified ground truth.** These numbers "
            "are exploratory and must not be cited. Run `eval/verify_cases.py` first.\n"
        )

    # --- pass rate by category -------------------------------------------
    parts.append("\n### Pass rate by case category\n")
    headers = ["category", "n"] + list(conditions) + (
        [f"delta ({conditions[0]}\u2192{conditions[-1]})"] if len(conditions) >= 2 else [])
    trows = []
    for cat in ORDER:
        cells, vals = [], []
        n = 0
        for cond in conditions:
            s = by.get((cat, cond), [])
            n = max(n, len(s))
            p = sum(1 for x in s if x["passed"])
            vals.append(100 * p / len(s) if s else None)
            cells.append(pct(p, len(s)))
        if not n:
            continue
        row = [cat, n] + cells
        if len(vals) >= 2 and vals[0] is not None and vals[-1] is not None:
            d = vals[-1] - vals[0]
            row.append(f"{d:+.0f} pts")
        trows.append(row)
    parts.append(md_table(headers, trows))
    parts.append(
        "\nRead this table by row, not by column mean. 'Accuracy improved' is not the finding; "
        "*which categories moved and which did not* is."
    )

    # --- error class distribution ----------------------------------------
    parts.append("\n\n### Error class distribution\n")
    ec_rows = []
    for ec in ECLASSES:
        row = [ec.value]
        for cond in conditions:
            s = [x for (c, cd), lst in by.items() if cd == cond for x in lst]
            row.append(f"{sum(1 for x in s if x['error_class'] == ec.value)} ({pct(sum(1 for x in s if x['error_class'] == ec.value), len(s))})")
        ec_rows.append(row)
    parts.append(md_table(["error class"] + list(conditions), ec_rows))
    parts.append("\nSeverity-weighted error load (very major = 10, major = 4, minor = 1, none = 0):\n")
    sev_rows = []
    for cond in conditions:
        s = [x for (c, cd), lst in by.items() if cd == cond for x in lst]
        sev_rows.append([cond, f"{sum(x['severity'] for x in s):.0f}",
                         f"{sum(x['severity'] for x in s) / max(1, len(s)):.2f}"])
    parts.append(md_table(["condition", "total severity", "per case"], sev_rows))

    # --- the abstention measure ------------------------------------------
    parts.append("\n\n### Tool invocation and abstention\n")
    for cond in [c for c in conditions if c in ("tools", "instructed")]:
        _invocation_block(parts, by, cond)

    tools_rows = []
    if tools_rows:
        called = sum(1 for x in tools_rows if x["tool_called"])
        abst = sum(1 for x in tools_rows if x["abstention_failure"])
        lucky = sum(1 for x in tools_rows if x["abstention_failure"] and x["passed"])
        parts.append(md_table(
            ["measure", "value", "meaning"],
            [["tool invocation rate", pct(called, len(tools_rows)),
              "cases where at least one tool was called"],
             ["abstention failures", f"{abst} ({pct(abst, len(tools_rows))})",
              "a tool was available and needed, and was not called"],
             ["correct but unchecked", f"{lucky}",
              "answered correctly without checking -- lucky, not reliable"]]))
        parts.append(
            "\n**This is the research question.** A model that answers correctly without calling "
            "the tool has not demonstrated that it knows the answer; it has demonstrated that this "
            "particular name happened to be well represented in its training data with the current "
            "form dominant. The 'correct but unchecked' row is the count of cases where accuracy "
            "and reliability come apart.\n"
        )
        per_tool = defaultdict(int)
        for r in rows:
            for t in r.get("tools_used", []):
                per_tool[t] += 1
        if per_tool:
            parts.append("\nCalls per tool:\n")
            parts.append(md_table(["tool", "calls"],
                                  sorted(per_tool.items(), key=lambda kv: -kv[1])))

    # --- false confidence -------------------------------------------------
    parts.append("\n\n### False confidence on contested cases\n")
    fc_rows = []
    for cond in conditions:
        s = [x for (c, cd), lst in by.items() if cd == cond and c == "contested" for x in lst]
        fc = sum(1 for x in s if x["false_confidence"])
        fc_rows.append([cond, len(s), f"{fc} ({pct(fc, len(s))})"])
    parts.append(md_table(["condition", "contested cases", "single-answer responses"], fc_rows))
    parts.append("\nOn these cases a single answer is wrong however authoritative it sounds.\n")

    # --- worst failures ---------------------------------------------------
    vm = [r for r in rows if r["score"]["error_class"] == "very_major"]
    if vm:
        parts.append("\n\n### Very major errors\n")
        parts.append(md_table(
            ["case", "condition", "why"],
            [[r["case_id"], r["condition"], r["score"]["reason"][:130]] for r in vm[:25]]))

    parts.append("\n\n### Error class definitions\n")
    parts.append(md_table(["class", "definition"],
                          [[k.value, v] for k, v in DEFINITIONS.items()]))

    text = "\n".join(parts) + "\n"
    if a.out:
        a.out.write_text(text)
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
