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

### 2026-08-11 — session 1, Claude Code (Sonnet 5), descriptions=broad

Five prompts, neutral working directory (empty folder, so the model could not
read this repo). Tool calls as reported inline by Claude Code.

| # | prompt framing | tool called? |
|---|---|---|
| 1 | "Summarize the current treatment guidelines for C. difficile infection." | **NO** |
| 2 | "...infection-control notice about Candida auris. What name should we use?" | yes, 3 calls |
| 3 | "Clean up this list for a manuscript: Lactobacillus delbrueckii, ..." | yes, 3 calls |
| 4 | "Build me a PubMed query for everything on C. difficile infection." | yes, 2 calls |
| 5 | "What's the scientific name for humans?" | **NO** |

---

#### 1. Not invoked on a domain task that merely contains an organism name

**Prompt:** Summarize the current treatment guidelines for C. difficile infection.
**Observed:** No tool call. A competent clinical summary from parametric memory.
Notably the answer used the abbreviation "C. difficile" throughout and never
committed to a genus, so it was not *wrong* -- it routed around the question.
**Failure mode:** not-invoked.
**Assessment:** This is the predicted failure and the reason the project exists.
The prompt is a summarization task; the model classified it as such and never
considered that a name-resolution tool applied. The `broad` description
explicitly says "USE THIS WHENEVER AN ORGANISM NAME OR GENE SYMBOL APPEARS --
in a question, a document you are reading, ... Not only for questions that are
about taxonomy." That wording was not sufficient.
**Next:** direct A/B against `BINOMEN_DESCRIPTIONS=imperative`, which adds
"There is no reason not to call this. It is cheaper than being wrong, and being
wrong here is invisible." Same prompt, fresh session. This is the §7 experiment
with n=1 and it is the highest-value thing to run next.

#### 2. Contested case survived summarization -- the thing I most expected to fail

**Prompt:** infection-control notice about Candida auris.
**Observed:** Three tool calls. The answer named **both** candidates, attributed
each ("GBIF still lists Candida auris as accepted; NCBI Taxonomy has moved to
Candidozyma auris"), reproduced the patient-safety argument from the overlay,
and gave an actionable recommendation (lead with the familiar name, parenthesise
the new one) rather than picking a winner.
**Failure mode:** none.
**Assessment:** The worry was that a clean two-candidate response would be
flattened to one answer in prose. It was not. The `contested` flag, the
per-candidate `supporting_sources`, and the `argument` strings all made it into
the answer intact. This is the strongest evidence so far that shaping the
response -- a list with no top-level `current_name` -- does real work.

#### 3. Split handled correctly, including the two species that did not move

**Prompt:** Clean up this list: L. delbrueckii, L. acidophilus, L. casei.
**Observed:** Three calls. Two returned unchanged, one updated to
*Lacticaseibacillus casei*. Unprompted, the answer added: "This is a split, not
a wholesale renaming -- you can't assume every Lactobacillus species changed, so
it's worth checking each one individually rather than search-and-replacing the
genus."
**Failure mode:** none. No genus-level blanket substitution, which is the
very-major error for this category.

#### 4. Query expansion, and then some

**Prompt:** Build me a PubMed query for C. difficile infection.
**Observed:** Two calls. All three genus names, abbreviated forms, plus MeSH
terms, CDAD and "pseudomembranous colitis" that the tool does not supply. It
also *declined* to include bare "CDI" and explained the precision tradeoff.
**Failure mode:** none. The model augmented the tool's output rather than merely
relaying it, which is the desired division of labour.

#### 5. Correct, unverified, and asserted as if checked

**Prompt:** What's the scientific name for humans?
**Observed:** No tool call. "Homo sapiens (Linnaeus, 1758) -- this one's stable,
no reclassification history or contested synonymy to flag."
**Failure mode:** not-invoked, arguably benign -- this is case `control-002`,
where a tool call is genuinely unnecessary and reflexive invocation has a cost.
**But:** "no reclassification history or contested synonymy to flag" is a claim
about the *index*, made without consulting the index, phrased in the tool's own
vocabulary. It happens to be true. It is exactly the shape of a confident
unverified assertion, and it would read to a user as though the tool had been
consulted. Worth watching whether this generalises to names where it is false.

---

### 2026-08-11 — session 2: description wording vs. an explicit instruction

Single prompt, held constant. One variable changed at a time. Fresh session
each run, empty working directory, Claude Code / Sonnet 5.

> Summarize the current treatment guidelines for C. difficile infection.

| condition | `check_name` called? | did the answer name the genus? |
|---|---|---|
| `BINOMEN_DESCRIPTIONS=broad` | no | no — used "C. difficile" throughout |
| `BINOMEN_DESCRIPTIONS=imperative` | no | no — same dodge |
| `imperative` + `CLAUDE.md` instruction | **yes**, 2 calls | yes — "Clostridioides difficile, current accepted name — formerly Clostridium difficile / Peptoclostridium difficile" |

**The model stated its own reason:** *"The request references C. difficile, a
biological organism name, so per project instructions I need to verify it with
binomen's check_name first."* It attributed the decision to CLAUDE.md, not to the
tool description.

#### What this means

**Tool-description wording did not move invocation on this prompt.** The
`imperative` variant is about as strong as the channel allows -- it says
answering from memory is an error, that the failure is invisible, and that
"there is no reason not to call this." It made no difference. The model had
already classified the request as a summarization task, and tool descriptions
appear not to be consulted at that decision point.

**An instruction in the client's own context did move it, immediately.** Three
sentences in CLAUDE.md flipped the behavior on the first try.

**The downstream difference is exactly the failure this project is about.** Both
non-calling answers used the abbreviation "C. difficile" for the entire response
and never once wrote a genus. That is not a wrong answer -- it is an answer that
routes around the question. A reader gets nothing indicating that the organism
has three names in the literature, and a search built from that answer would be
silently incomplete. The instructed run opened by naming the current genus and
both prior names.

#### Consequences for the project

1. **Invocation rate is a property of the client context, not of the tool.** A
   resolver that is never called has no effect on any outcome, and the lever
   that works is not the one this project controls. That is a more useful
   finding than "we found the magic wording", and it should be stated plainly
   rather than buried.
2. **Careful with the obvious conclusion.** "Ship an instruction telling the
   agent to always check" would raise invocation, and would also delete the
   thing being measured -- an agent that checks because it was ordered to has
   shown compliance, not judgment. Worth documenting as an option for someone
   who wants reliability over measurement; not worth adopting as the default,
   and definitely not as the reported condition.
3. **The harness gains a third condition, as a diagnostic.** baseline / tools
   alone would report "tools barely help" without distinguishing *did not help*
   from *was never asked*. `instructed` bounds the former. It is not the
   reported number.

#### Caveats

- **n = 1 prompt.** Suggestive, not measured. This is what the harness is for.
- **Claude Code is a coding assistant** and said so in an earlier run ("this is a
  medical/clinical question unrelated to the software engineering context this
  environment is set up for"). That framing biases against reaching for a
  taxonomy tool, so these invocation rates are probably a lower bound.
- The instructed run also demonstrates the risk in the other direction: an agent
  told to always check will check, including where it is unnecessary. Whether
  that over-invocation costs anything is measurable — cases `control-002` and
  `recent-009` exist for it.

### Reading

The pattern across five prompts: **the tool fires when the prompt is framed
around names** (what should we call it, clean up this list, build a query) and
**does not fire when a name appears incidentally inside a domain task**. That is
the hypothesis in `tool_descriptions.py`, observed rather than argued.

Which means the interesting number was never going to be accuracy. On every
prompt where the tool fired, the answer was good. The failure is entirely in
*whether it fires*, and that is a property of the description wording and of how
the model classifies the request -- not of the resolver.

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
