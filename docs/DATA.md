# Data sources, versions, and terms

The MIT license on this repository covers **source code only**. Reference data
is governed by each source's own terms.

## Tier 1 — backbone

**NCBI Taxonomy** (`taxdump`)
- Terms: public domain, work of the US Government
- Redistributable: yes
- URL: https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz
- Version: taxdump has no internal version string. `build_index` records the
  modification time of `nodes.dmp` as `taxdump-YYYY-MM-DD` and verifies the
  archive against NCBI's published md5.
- Files used: `names.dmp`, `nodes.dmp`, `merged.dmp`, `delnodes.dmp`

**Not an authority.** NCBI Taxonomy is a pragmatic classification maintained to
organize sequence data. It is not a nomenclatural authority under any code and
not a phylogenetic hypothesis. For a nomenclatural question the code-specific
authority is what counts; NCBI is the backbone because it is complete, free, and
public domain, not because it is correct in the nomenclatural sense.

## Tier 2

**GBIF Backbone Taxonomy**
- Terms: CC BY 4.0 — attribution required
- Redistributable: yes, with attribution
- Accessed live via `api.gbif.org`, responses cached locally
- No build identifier is exposed on `/species/match`, so provenance records the
  retrieval timestamp and says so explicitly rather than implying a version

## Tier 3 — code-specific

| Source | Code | Terms | Redistributable | Configuration |
|---|---|---|---|---|
| LPSN (DSMZ) | ICNP | DSMZ terms; registration required | **No** | `BINOMEN_LPSN_USER`, `BINOMEN_LPSN_PASSWORD` |
| MycoBank | ICNafp | Site terms | **No** | `BINOMEN_MYCOBANK_ENDPOINT` |
| Index Fungorum | ICNafp | Site terms | **No** | — |
| ICTV MSL | viruses | Openly published | Yes, cite the MSL number | `BINOMEN_ICTV_MSL`, `BINOMEN_ICTV_VERSION` |

For the non-redistributable sources `binomen` queries and cites. It does not
ship derived data and the HTTP cache is gitignored. If you build something on top
of this that redistributes, check the terms yourself — they change.

## Tier 4

**HGNC** — freely available; cite genenames.org. Accessed via
`rest.genenames.org`. HGNC has no global release identifier, so provenance uses
each record's `date_modified` and says which field it is.

## Curated overlay

`src/binomen/data/contested.json` — written for this project, MIT.

It carries what taxdump structurally cannot express: that a synonymy is
*disputed*, and the year and reference of the nomenclatural act. Every entry
ships `confidence: "unverified"`, meaning it was drafted from secondary
knowledge and has not been checked against the primary source. Run
`eval/verify_cases.py` and adjudicate before relying on any of it.

## Citing

Every tool response carries provenance. A methods section should name the source,
its version, and the retrieval date — all three appear in `provenance` on every
return. `list_authorities` reports which sources were actually consulted for a
group, including which were configured and which were not, so "we consulted X"
can be stated accurately rather than aspirationally.
