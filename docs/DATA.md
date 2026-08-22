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
| LPSN (DSMZ) | ICNP | **CC BY-SA 4.0**; registration required for the API | **Yes**, share-alike + link to the source page | `BINOMEN_LPSN_USER`, `BINOMEN_LPSN_PASSWORD` |
| Index Fungorum (Kew) | ICNafp | **CC BY 4.0** under Kew's science-data terms, DOI `10.48580/d38h` | Yes, with attribution | — (ChecklistBank dataset 1028) |
| Species Fungorum Plus (Kew) | ICNafp | **CC BY 4.0**, DOI `10.15468/ts7wsb` | Yes, with attribution | — (dataset 2073; the Apr 2024 fallback) |
| ICTV MSL | viruses | Openly published, CC BY | Yes, cite the MSL number | — (ChecklistBank dataset 1014) |
| MycoBank | ICNafp | Site terms | Unverified — query and cite | `BINOMEN_MYCOBANK_ENDPOINT` |

**On the fungal register's licence.** ChecklistBank leaves dataset 1028's
`license` field empty, which is an omission at registration rather than a
different grant. Kew's own terms settle it: general website content is
all-rights-reserved and bars commercial use without a licence, but the terms
carve out "pages containing science data and digital resources", which "are
available under the Creative Commons Attribution Licence (CC-BY) and © copyright
The Trustees of the Royal Botanic Gardens, Kew." Nomenclatural data is science
data, and attribution travels in every row's `link`. Two further supports: Kew
publishes the same nomenclator as dataset 2073 under `cc by`, and GBIF hosts a
Kew Index Fungorum crawl under CC BY 4.0.

This matters beyond compliance. Under CC BY a hospital laboratory or an industry
researcher may use the tool; under non-commercial terms they could not, and a
non-commercial file could not lawfully sit beside LPSN's CC BY-SA data in any
case, since share-alike forbids adding that restriction.

Catalogue of Life 2026 was checked as an alternative and is no fresher — it still
holds *Candida auris* as accepted.

Verified 2026-08-21 against lpsn.dsmz.de/text/copyright and the GBIF/ChecklistBank
dataset registries. An earlier version of this table said LPSN was not
redistributable and that fungal bulk reuse was not granted; both were wrong, and
the LPSN claim is one of the eight instrument faults recorded in `FINDINGS.md` §8.
Registers are harvested from ChecklistBank's versioned exports, which need no
credentials; LPSN's own API is used only to add `lorn_status` (medical-use
recommendations), which the mirror does not carry.

Share-alike is why the registers ship as their own database rather than as columns
on the NCBI index — see `docs/adr/0002-two-files-for-licence-containment.md`.

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
