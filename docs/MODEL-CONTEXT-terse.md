# What the model actually sees

<!-- GENERATED FILE - do not edit by hand.
     Source: node/src/tool_descriptions.js
     Rebuild all three variants with `make model-context`. -->

Instructions variant: terse
Descriptions set    : terse
Text fingerprint    : cb741215bc0b

## 1. System prompt — sent ONCE per conversation

The client renders this under a heading of its own:

```
# MCP Server Instructions

## binomen — biological name checker

binomen resolves genus and species names against a dated copy of NCBI Taxonomy.
```

  79 chars, ~20 tokens, paid once

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

  instructions           79 chars  ~  20 tok   once per conversation
  tool descriptions     234 chars  ~  59 tok   EVERY request

  For comparison, a check_name reply on a stable name is ~98 chars, ~25 tok.
  Token counts are estimates at ~4 chars/token, not measured.

