# What the model actually sees

<!-- GENERATED FILE - do not edit by hand.
     Source: node/src/tool_descriptions.js
     Rebuild all three variants with `make model-context`. -->

Instructions variant: conditional
Descriptions set    : terse
Text fingerprint    : fbe7eb58bc51

## 1. System prompt — sent ONCE per conversation

The client renders this under a heading of its own:

```
# MCP Server Instructions

## binomen — biological name checker

binomen resolves biological names against a dated copy of NCBI Taxonomy. Every value is a lookup, not a generation.

Call check_name when an organism name appears and the answer depends on it being current -- writing about an organism, reconciling datasets, preparing a search. A lookup costs about 2 ms and one line.

Call expand_query before a literature search. The current name alone returns few results rather than an error, which reads like a finding.

Where authorities disagree, report every candidate. Do not pick one.
```

  527 chars, ~132 tokens, paid once

## 2. Tools block — sent with EVERY request

Each tool arrives as a name, a description, and an input schema.
The description is the only prose the model has when choosing a tool.

### `check_name`

```
Call on every genus and/or species name you read or write
```

  57 chars, ~14 tokens

### `resolve_name`

```
Call if a genus and/or species name has changed
```

  47 chars, ~12 tokens

### `get_synonyms`

```
Call when it is useful to view every name recorded for a taxon
```

  62 chars, ~16 tokens

### `expand_query`

```
Call when designing a search query concerning a genus and/or species
```

  68 chars, ~17 tokens

## Budget

  instructions          527 chars  ~ 132 tok   once per conversation
  tool descriptions     234 chars  ~  59 tok   EVERY request

  For comparison, a check_name reply on a stable name is ~98 chars, ~25 tok.
  Token counts are estimates at ~4 chars/token, not measured.

