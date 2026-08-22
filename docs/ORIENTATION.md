# What all of this is

A map of the repo for someone who has not built an MCP server before. Nothing
here is about *how to change* the project — that is EXTENDING.md. This is only
what each part is and why it exists.

---

## 1. The problem, in one paragraph

Biological names change. *Clostridium difficile* is now *Clostridioides
difficile*. Both names are all over the literature, so a name being familiar
tells you nothing about whether it is current. A language model has both names
in its weights and no way to know which one today's databases use, because
nothing it remembers carries a date. binomen gives it a way to look that up
against a dated copy of NCBI Taxonomy, and — the part that matters most — to
report when the authorities disagree instead of picking one.

---

## 2. What an MCP server actually is

Claude Desktop cannot reach your files, your databases, or NCBI. MCP (Model
Context Protocol) is how you give it a door.

An MCP server is **just a program**. Claude Desktop launches it and talks to it
over stdin/stdout in JSON. The conversation is roughly:

```
Desktop -> server:  "initialize"
server  -> Desktop:  name, version, and optionally an `instructions` string
Desktop -> server:  "tools/list"
server  -> Desktop:  4 tools, each with a name, a description, and an input schema
   ... later, if the model decides to ...
Desktop -> server:  "tools/call check_name {name: 'Candida auris'}"
server  -> Desktop:  {"verdict": "contested", ...}
```

Three consequences that explain most of this project:

**The description is the entire user interface.** When the model is deciding
whether to call your tool, the only thing it sees is that one line of prose.
Not your README, not your code. That is why a whole eval harness exists to
measure wording.

**The model decides, not you.** You cannot make it call the tool. You can only
describe the tool well. That is the "invocation" problem this project keeps
measuring.

**`instructions` is a second, stronger channel.** The optional string in the
initialize response gets rendered into the system prompt by at least some
clients. It is read *before* any tool description. That is why there are
`terse` / `conditional` / `unconditional` variants of it being compared.

---

## 3. The two implementations, and why

There are two copies of the same logic. This looks like a mistake and is not.

**`node/`  —  what users get.**
The shipping extension. `node/src/server.js` is the MCP server described above.
It is Node because Claude Desktop's extension format runs Node, and because it
must start in milliseconds with no Python install on the user's machine.

**`src/binomen/`  —  what the measurements use.**
The same resolution logic in Python, plus the parts users never see: building
the index, downloading releases, querying live authorities. The eval harness
talks to the Anthropic API **directly**, not through MCP, so it has to send the
tool descriptions itself — from this copy.

If the two copies drift, the eval measures a tool that no user has. That is what
`tests/test_instructions_parity.py` is for: it asks Node for its strings and
compares them to Python's, rather than restating either.

---

## 4. The installer

A biologist is not going to clone a repo.

`scripts/build_mcpb.py` packages `node/` into `dist/binomen.mcpb` — a bundle
Claude Desktop installs when you double-click it. `node/manifest.json` is what
tells the installer what is inside.

**Why the version number lives in three files:**

| file | who reads it |
|---|---|
| `node/manifest.json` | the installer, at install time |
| `node/package.json` | node and npm tooling |
| `node/src/server.js` | Claude Desktop, when the server introduces itself |

They are three different audiences, which is exactly why they drift apart. If
`server.js` reports 0.2.5 while the installer shipped 0.2.7, every bug report
points at code you did not ship.

---

## 5. The index

`data/*.sqlite` — built from NCBI's `taxdump` archive by
`binomen-build-index`. **Not in git**, deliberately: it is hundreds of MB, and
freezing a taxonomy inside a repo reintroduces the exact staleness the project
exists to detect. The databases the extension ships are built from it and
gitignored for the same reason; each records the release it came from, so a
snapshot always says how old it is.

Since v0.3.0 the extension carries its own data and users fetch nothing.
`binomen-build-ambiguity` reduces stage 1 to the names that carry an
*ambiguity* — 1,007,862 rows to 661,406, 107 MB to 48 MB — and
`binomen-harvest-registers` builds the register file beside it. Anyone wanting
ranks, lineages or author citations can still fetch the full index with
`binomen-fetch-index`; the server prefers it when it is there.

The build stages, and the split that is the main design idea in the codebase:

- **Stage 1** (`binomen-stage1.sqlite`) — small and fast. Answers only "is
  there anything about this name worth a closer look?" This is what
  `check_name` uses, and it is meant to be cheap enough to call on every name.
- **Stage 2** (`binomen-field.sqlite`) — the full record: synonyms, lineages,
  authorities, taxids. This is what `resolve_name` uses.

Stage 1 uses a **Bloom filter** for stable names. A Bloom filter can tell you a
name is definitely *absent*, but cannot list what it contains. That property is
load-bearing — it is why the file is small, and also why an abbreviation
expansion can never promise to be complete.

---

## 6. The four tools

| tool | what it answers | cost |
|---|---|---|
| `check_name` | anything odd about this name? | stage 1, ~20 tokens back |
| `resolve_name` | the full record, all candidates, provenance | stage 2 |
| `get_synonyms` | every name recorded for this taxon | stage 2 |
| `expand_query` | a search string covering all historical names | stage 2 |

`check_name` returning `escalate: true` means "now call `resolve_name`". The
intended shape is a cheap filter that usually says "fine, carry on".

---

## 7. The evaluation, and the two different questions

This is where most of the recent work has gone, and the two questions are
constantly confused. They are not the same.

**Question 1 — does the model call the tool at all?**
Measured by `eval/invocation.py` (via the API) and `eval/invocation_cc.py` (via
`claude -p`). Prompts live in `eval/prompts_*.jsonl`. A tool that is never
called has no effect on anything, so this had to be answered first. **It has
been:** the `unconditional` instruction fires on 15/15 domain-framed prompts
where `terse` fires on 3/27.

**Question 2 — does the tool make the answers better?**
Measured by `eval/runner.py` against `eval/cases/cases.jsonl` — 102 cases with
written-down ground truth, scored by `eval/scorer.py`. It runs each case
**with** and **without** the tools and compares.

**This has never been run.** That is the current state of the project. Every
number produced so far answers question 1.

`eval/verify_cases.py` exists because a test is only as good as its answer key.
It checks each case's ground truth against real authorities before anything is
measured against it, and marks the case `verified`. `runner.py` refuses to run
on unverified cases, which is why it refused.

---

## 8. Directory summary

```
node/                 the shipping extension (MCP server, Node)
  src/server.js         speaks MCP over stdin/stdout
  src/resolver.js       the lookups
  src/names.js          name parsing and normalisation
  manifest.json         what the installer reads

src/binomen/          the Python package
  resolver.py           same lookups, for the harness and the CLI
  db.py                 sqlite access, stage 1 and stage 2
  build/                taxdump -> sqlite
  authorities/          live queries to LPSN, GBIF, MycoBank, HGNC, ICTV
  tool_descriptions.py  the mirror of node/src/tool_descriptions.js

eval/                 measurement
  cases/cases.jsonl     102 cases with ground truth
  runner.py             question 2: does it help  (never run)
  scorer.py             grades an answer against a case
  verify_cases.py       checks the ground truth itself
  invocation*.py        question 1: does it get called
  runs/                 outputs (gitignored)

scripts/              build, install, diagnostics
tests/                158 Python tests; node/test has 26 more
docs/                 you are here
data/                 the sqlite indexes (gitignored, hundreds of MB)
```

---

## 9. Git, honestly

Splitting work into many small commits is a habit that pays off on a team,
where someone else has to review or revert one change without the others. On a
solo project it is optional. One commit that says what happened is fine, and a
tidy history is worth much less than a working tool.

The one rule worth keeping: **never commit the index or a real eval run.** Both
are already in `.gitignore` — the index because it is huge and freezing a
taxonomy defeats the point, and eval runs because they contain model output.
