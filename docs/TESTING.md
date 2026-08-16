# Measuring invocation

How binomen's central open question is tested, and the rules that keep the
numbers meaning something.

---

## The question

Not "is the answer right". Session 1 found the answer was good on **every**
prompt where a tool fired. The failure is entirely **whether it fires**, and a
biologist can install this successfully and have it sit idle.

So the measured quantity is: **given a prompt, does the model call a binomen
tool at all?**

---

## Four instruments, in increasing cost

| instrument | what it uses | what it answers | cost |
|---|---|---|---|
| `docs/INVOCATION-RUNS.md` | hand-run chats in Claude Desktop | does it fire in the real product | free, slow, n≈12 |
| `eval/invocation_cc.py` | `claude -p`, subscription auth | relative comparison between conditions | subscription tokens |
| `eval/invocation.py` | Messages API, one call per row | invocation rate in a clean environment | API credit |
| `eval/runner.py` | full agentic loop, 102 cases | does the tool *help* — severity-scored | API credit, much more |

They are not interchangeable, and the reason is in the next section.

### Which to reach for

- **Changed the wording and want to know if it helped?** `invocation_cc.py`,
  `--minimal`. Relative comparison only.
- **Need a number to report?** `invocation.py`. Its environment matches the
  product; Claude Code's does not.
- **Need to know if the answers are good?** `runner.py`. Different question.
- **Need to know what a real user sees?** The hand protocol. Nothing else
  substitutes.

---

## The variables

| variable | how it is set | notes |
|---|---|---|
| **prompt framing** | `eval/prompts_invocation.jsonl` | the variable session 1 identified: name-framed fires, domain-framed does not |
| **descriptions** | `BINOMEN_DESCRIPTIONS` = `terse` \| `broad` | sent with **every** request |
| **instructions** | `BINOMEN_INSTRUCTIONS` = `terse` \| `conditional` \| `unconditional` \| `off` | sent **once**, into the system prompt |
| **model** | `--models` | documented to affect tool selection; observed to. **Not noise.** |
| **replicates** | `-n` | the model is stochastic; one sample is an anecdote |

Both text variables are switchable without rebuilding, and the active pair is
recorded in `boot.log` at every server start.

`node scripts/show_model_context.js terse` prints the finished text exactly as
the model receives it — no string concatenation, no comments. Review wording
against that, not against the source.

---

## Rules that keep results honest

Every one of these exists because its absence already cost something.

**1. Always run a positive control.** `nf-01` fires reliably. If it does not
fire in a block, the block is void — the extension did not attach, the model
changed, or something else broke. A broken install and a treatment with no
effect look identical from the chat window. A whole hand comparison was
invalidated this way and would have been written up as a null result.

**2. Never compare across environments.** A rate from Claude Code is not
comparable to a rate from Claude Desktop. Claude Code frames itself as a coding
assistant and has declined taxonomy prompts outright for that reason. Use it
for *relative* comparison on one host, in one session.

**3. Record the model, per block.** Sonnet 5 and Opus 5 behave differently
enough that pooling them destroys the measurement.

**4. Never test on an organism named in the text under test.** These appear
inside `broad` and the longer instruction variants:

```
Clostridium difficile · Clostridioides difficile · Enterobacter aerogenes
Klebsiella aerogenes · Lactobacillus
```

A prompt containing one has the strongest possible lexical match to the thing
being measured. *Candida auris* appears in none of them, which is why the test
prompts use it.

**5. Change one thing between blocks.** Both text variables move together
easily and neither is neutral.

**6. Trust the interval, not the point estimate.** The report prints Wilson
intervals. At n=5, 2/5 is 0.12–0.77 and 4/5 is 0.38–0.96 — those overlap almost
entirely. n=5 can separate "never" from "usually" and nothing narrower.

**7. Check the fingerprint before pooling runs.** Every row carries a hash of
the exact text sent. Variant *names* are not enough: `terse` meant something
different three commits ago. The report warns when rows span fingerprints.

**8. Errored rows are excluded, not counted as zero.** If a large fraction
errored, the summary is not trustworthy — it says so.

---

## Running it

```bash
# prove the wiring first: one call, full output
python eval/invocation_cc.py --smoke

# the smallest grid that answers something (12 runs)
python eval/invocation_cc.py --minimal --instructions terse unconditional -n 5

# stopped early? pick up where it left off
python eval/invocation_cc.py --minimal --instructions terse unconditional -n 5 \
    --resume eval/runs/invocation-cc-<stamp>.jsonl

# summarise any run file, from either harness
python eval/invocation.py --report eval/runs/<file>.jsonl
```

`--plan` prints what would run without running it. `--max-runs N` caps a
session. Replicates are outermost, so an interrupted run leaves a **balanced**
partial grid — n=2 everywhere rather than n=5 on one corner. A session was
spent learning that.

---

## Maintaining it

**When you change description or instruction text**

1. Edit `node/src/tool_descriptions.js`.
2. Mirror it into `src/binomen/tool_descriptions.py` — the harness talks to the
   API directly and must send what the extension sends.
3. `python -m pytest tests/test_instructions_parity.py` asserts the two match,
   by asking Node for its copy rather than restating it.
4. Bump the version in **three** files: `node/manifest.json`,
   `node/package.json`, `node/src/server.js` (`serverInfo.version`).
5. **Earlier runs are now about different text.** Their fingerprints will not
   match. Do not pool them.

**When you add a prompt**

Give it a `framing` (`name_framed`, `domain_framed`, `over_trigger_stable`,
`no_organism`) — the report groups by it. Avoid the organisms in rule 4. Say in
`notes` why it exists.

**When you add a variant**

Add it to both implementations, and to `INSTRUCTION_SETS` / `DESCRIPTION_SETS`.
The parity test checks neither side has a variant the other lacks: one is
shippable and unmeasurable, the other measurable and unshipped.

**Before believing any result**

- Did the positive control fire?
- Is the model recorded, and the same within the comparison?
- Do the Wilson intervals actually separate?
- Do all rows share a fingerprint?
- What fraction errored or truncated?

---

## State as of 2026-08-13

**Known**

- Name-framed prompts fire reliably. Opus 5, `terse`/`terse`, 9/9 across
  `nf-01` and `nf-02`. The 6× description cut did not break invocation on
  prompts that already worked.
- The `instructions` field reaches the model: Claude Desktop renders it into
  the system prompt verbatim, under its own heading.
- Tool *selection* varies. On a "build me a pubmed query" prompt,
  `expand_query` fired 3/5 and `get_synonyms` 4/5. Answers were still good.

**Open**

- **Domain-framed invocation.** `df-01` — *"give me the latest research into
  candida auris"* — failed live with the extension attached and instructions
  rendered. No grid has reached it yet.
- **Model.** Sonnet 5 produced 0/4 including the control in one hand block;
  Opus 5 fires. Unreplicated, and confounded with an install problem that was
  fixed afterwards.
- **Over-triggering.** The cost side of an unconditional trigger. `ot-01`
  through `ot-05` exist for it; none have been run.
- **Whether tool annotations reduce permission prompts.** Shipped in 0.2.5,
  never counted.
