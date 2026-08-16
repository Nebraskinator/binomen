# Published guidance on tool design, and where binomen sits against it

Collected 2026-08-13. Sources at the bottom. Recorded because two of these
findings cut against decisions made this week, and a decision that survives
only by not being written down is not a decision.

---

## 1. The `instructions` field

**Where it lands.** The client injects it, typically into the system prompt, so
the model reads it **before** it sees any tool schema, resource list, or user
message. That ordering is the reason the field exists: it frames everything
that follows.

**What belongs in it**

- Guidance that applies **across** tools — required sequences, shared
  conventions, when the server as a whole is relevant.
- The most important content in the **first 512 characters**. Attention is not
  uniform down the block.

**What does not**

- Repeating tool descriptions. They arrive separately and cost more.
- Anything trying to change the model's persona.

**The caution worth quoting.** *"No instructions are better than poorly written
instructions."* Because it is injected into the system prompt, a bad one is a
standing tax on every conversation.

**Support, as of late 2025.** Claude Code injects it and respects it
consistently. VS Code / Copilot Chat injects it. Most servers do not set it at
all. Confirmed independently here: Claude Desktop renders it verbatim under its
own heading — see DISCOVERY-LOG session 3.

### binomen's position

The shipped `terse` instruction is 79 characters and states what the server is,
with all trigger guidance moved into the tool descriptions. That is consistent
with "don't repeat tool descriptions" and comfortably inside 512 characters.

It is **not** consistent with "use it for guidance that applies across tools".
The cross-cutting rules this server actually has — *authorities disagree, report
every candidate*; *do not answer a name question from memory* — now appear in
neither channel as prose. The first is carried by the response payload
(`contested: true`, and a literal `AUTHORITIES DISAGREE` warning string), which
is defensible and is the project's stated architecture. The second is carried
nowhere. Whether that matters is measurable and unmeasured.

---

## 2. Tool descriptions

This is where the guidance and this project's own data disagree, and the
disagreement should not be smoothed over.

**Published guidance** is unambiguous:

> **Provide extremely detailed descriptions.** This is by far the most important
> factor in tool performance. [...] Aim for at least 3–4 sentences for each tool
> description, more if the tool is complex.

It asks each description to cover: what the tool does; when it should be used
**and when it shouldn't**; what each parameter means; caveats and limitations,
**including what the tool does not return**.

**binomen shipped the opposite** in 0.2.6 — four one-line descriptions, 317
characters total, down from 2000.

**Both positions have support, and they are answering different questions.**

The guidance is about *tool performance*: choosing the right tool, passing the
right arguments, interpreting the result. binomen's failure mode is none of
those. Session 1 found that on every prompt where the tool fired, the answer was
good. The failure is entirely **whether it fires at all**, and session 2
measured description wording — `broad` versus `imperative`, both detailed — as
not moving that number.

So the honest reading is: detailed descriptions are well-evidenced for tools an
agent has already decided to call. binomen's problem sits upstream of that, and
the evidence there is one hand-run comparison. Neither is strong enough to
settle it. `broad` is retained as a switchable variant precisely so the
comparison can be run rather than asserted.

**What terse gives up, explicitly**, so it is a decision and not an oversight:

- What `check_name` returns, and what each verdict means.
- That `resolve_name` returns a **list** because authorities disagree.
- That `expand_query` exists because searching the current name alone returns
  few results rather than an error.
- What the tools do **not** do — no lineage, no phylogeny, no live authority
  queries.

The bet is that the response payload can carry the first two, and that the
third and fourth are not needed to decide whether to call.

---

## 3. Model choice — this explains the Sonnet/Opus observation

Directly from the docs:

> Use the latest Claude Opus model, Claude Opus 5, for **complex tools and
> ambiguous queries; it handles multiple tools better** and seeks clarification
> when needed.
>
> Use Claude Haiku models for straightforward tools, but note they **may infer
> missing parameters**.

Tool-selection capability is documented as varying by model. The observation
here — Sonnet 5 not calling binomen where Opus 5 called three tools — is
consistent with that, and it means **model is a first-class variable, not
noise**.

The product consequence is uncomfortable: a bench biologist does not choose
their model deliberately, and no amount of description tuning compensates for a
model that does not reach for the tool. That belongs in the README's honest
limitations, and in `docs/INSTALL.md`, once it is measured rather than observed
twice.

---

## 4. Structuring returned values

The goal — *make the return self-explanatory so the description does not have
to be* — is directly supported.

**From the tool-design guidance**

- **Return only high-signal information.** Include only fields the model needs
  to reason about its next step. Bloated responses waste context and make it
  harder to extract what matters.
- **Prefer semantic, stable identifiers over opaque internal ones.** Resolving
  cryptic IDs to interpretable language *significantly improves precision and
  reduces hallucination*. Avoid `uuid`, `mime_type`, `256px_image_url`; prefer
  `name`, `file_type`.
- **A `response_format` enum** (`concise` / `detailed`) lets the caller choose
  verbosity, when both are genuinely needed.
- **Response structure — JSON vs XML vs Markdown — measurably affects
  performance, and there is no universal winner.** Choose by evaluation.
- **Error responses are prompt-engineerable.** A useful error names the
  correction; an opaque code or traceback does not.

**From the MCP spec (2025-06-18)**

- **`outputSchema`** — an optional JSON Schema on the tool definition describing
  the return. If provided, the server **MUST** return conforming
  `structuredContent`, and clients **SHOULD** validate it.
- **`structuredContent`** — the parsed object, alongside the serialized JSON in
  a text block for backwards compatibility.
- **`isError: true`** for tool-execution failures, so the model can see the
  failure and self-correct, rather than a protocol-level error.

### `outputSchema` is the mechanism being asked for

It documents what every field means, **once**, in structured form, in the tools
block — instead of prose in the description repeated for a human reader. Each
property carries its own `description`. That is precisely "give better hints at
what the returned values mean so they don't need to be in the tool
descriptions", and it is the protocol's sanctioned way to do it.

**The cost is real and has to be measured, not assumed.** A schema is not free;
it lands in the same tools block that was just cut from 2000 characters to 317.
An `outputSchema` for four tools could plausibly cost more than the descriptions
it replaces. And `tests/test_stages.py` asserts a token budget for `check_name`
that a duplicated `structuredContent` payload could break.

Recommended order: draft the schema, measure the tools block before and after,
and only then decide. Do not ship it on the strength of the argument alone —
the argument for the long descriptions was also good.

### binomen's current returns, assessed

```json
{"name":"Homo sapiens","verdict":"stable","code":"ICZN","escalate":false,
 "as_of":"taxdump-2026-08-13"}
```

```json
{"name":"Clostridium difficile","verdict":"superseded","code":"ICNP",
 "escalate":true,"reason":"recorded as a synonym; the accepted name differs",
 "next":"resolve_name","accepted_name":"Clostridioides difficile"}
```

Against the guidance these hold up well.

- `verdict` uses words, not codes. `escalate` is a boolean decision, not a
  score to interpret. `next` names the exact tool. `reason` is a sentence.
  This is the "semantic over opaque" rule already followed.
- `accepted_name` is the answer, not a taxid to look up. Good.
- **`code` is the weak field.** `ICZN` / `ICNP` / `ICNafp` are exactly the kind
  of opaque identifier the guidance warns about, and on a `stable` verdict it
  is not needed to decide anything. Candidate for removal from stage 1 — which
  would also shrink the response.
- **`as_of` appears only on `stable`.** Provenance that vanishes when the answer
  is interesting is backwards.

---

## Sources

- [Define tools — Claude Platform Docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)
- [Writing effective tools for agents — Anthropic Engineering](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [MCP specification 2025-06-18: Tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
- [MCP specification 2025-06-18: Lifecycle](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle)
- [MCP Server Instructions — MCPJam](https://www.mcpjam.com/blog/server-instructions)
- [Server Instructions: Giving LLMs a User Manual for Your Server](https://modelcontextprotocol.info/blog/server-instructions/)
- [MCPB should include static server instructions — modelcontextprotocol/mcpb#123](https://github.com/modelcontextprotocol/mcpb/issues/123)
