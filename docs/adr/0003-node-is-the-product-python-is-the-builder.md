# Node is the product; Python builds what it ships

The Node extension is the artefact users install: four tools, bundled data, no
network access at runtime. The Python package builds the index, harvests the
registers, and runs the evaluation — and the evaluation drives the shipped Node
server over MCP rather than importing a second implementation of the same logic.

## Context

The two implementations had already drifted six tools apart: `tool_descriptions.py`
defined ten, `node/src/server.js` registered four, and the only parity enforced
between them compared description strings. Register policy — jurisdiction,
cross-code homonyms, when a disagreement is worth reporting — existed as two
hand-synchronised copies of the same hundred lines. `docs/FINDINGS.md` §8
catalogues eight wrong results this project produced through instrument drift; a
harness measuring a tool surface no user has is the same fault class.

## Consequences

The six Python-only tools are build and analysis code, not product.
`consult_authorities` belongs there by definition: it queries the network, and the
product no longer does.

`resolve_name` is answered from the cluster rather than from stage 2, so the common
install has no fetch step at all. Stage 2 remains optional depth for
`get_synonyms` and `expand_query`.

Build credentials (`BINOMEN_LPSN_USER` / `BINOMEN_LPSN_PASSWORD`) stay on the
maintainer's machine and are never placed in CI. Releases are cut by hand as a
result; that trade was made deliberately and is a one-line change to reverse.
