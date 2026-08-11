# Discovery log

The conversational loop from the harness design (§6 of the README): install in
Claude Desktop, use it by hand, and log every failure. Failures found here become
cases in `eval/cases/`.

Keep this separate from the measurement loop. Anything discovered here is fair
game for iterating on tool descriptions and the case set; the held-out set is
not.

## Format

```
### YYYY-MM-DD — short title
**Prompt:** what was asked
**Observed:** what happened
**Failure mode:** not-invoked | wrong-arguments | result-misread | tool-gap | other
**Fix:** what changed, or "became case <id>"
```

## Entries

### (template) 2026-08-11 — not yet run
**Prompt:** —
**Observed:** —
**Failure mode:** —
**Fix:** —

---

## What to watch for

The four failure modes worth distinguishing, because they have different fixes:

**Tool not invoked.** The most important one, and the least visible — the answer
looks fine. Watch especially for prompts that do not announce themselves as
nomenclature questions: summarization, drafting, data cleaning. Fix is usually a
tool-description change, which is why the description variants are a measured
variable rather than a settled choice.

**Invoked with wrong arguments.** Genus passed where a species was meant;
`compare_names` called with a name and a common name; `expand_query` called with
a name instead of a `resolution_id`. Usually a schema or description fix.

**Correct result misread.** The tool returned candidates and a contested flag,
and the summary presented one answer confidently anyway. This is the most
interesting failure because the information was right there and got dropped —
and it is the one that argues for how the response is *shaped*, not just what it
contains.

**Tool gap.** A question the eight tools genuinely cannot answer. Log it before
building anything; several will turn out to be the same tool.
