# Backbone and registers ship as two files, not one

The bundle carries `ambiguity.sqlite` (derived from NCBI Taxonomy, public domain)
and `registers.sqlite` (derived from LPSN, Species Fungorum Plus and the ICTV MSL,
CC BY-SA) as separate databases joined at query time on the normalised name.
LPSN's CC BY-SA is share-alike and viral: merged into one file, the combined
artefact is adapted material and the NCBI-derived half loses its unqualified
public-domain claim.

## Considered options

A single file licensed CC BY-SA throughout would be simpler to read and would cost
the one property worth protecting — two licence claims, neither needing a
qualifying sentence. Dropping share-alike sources would cost the 695 medical-use
recommendations, which `docs/FINDINGS.md` §6 identifies as the product.

## Consequences

A lookup touches two databases. Both are indexed on the normalised name, so this
is a second indexed read or an `ATTACH`.

**Verified 2026-08-21 under the real runtime**, which had been the open question
here: Claude Desktop 1.34493.1.0, built-in Node 24.18.1 inside Electron 42.9.2,
`process.parentPort` present, files read from the installed extension directory.
Both `ATTACH` plus a SQL join and two `DatabaseSync` handles joined in JavaScript
returned the *Borreliella* / *Borrelia* disagreement across the two files. The
reader uses two handles: it is the narrower dependency, and point lookups on an
indexed key gain nothing from the join being done in SQL.

Two facts about that runtime constrain the reader. `cwd` is `C:\WINDOWS\system32`,
so every path must be resolved from `__dirname`. Extensions unpack into
`%APPDATA%\Claude\Claude Extensions\`, which is writable — the read-only
`Program Files\WindowsApps` case does not arise.

Every register row carries its own link and DOI. LPSN's terms require a link to
the page material came from, and attribution that travels in the row cannot be
dropped the way a footnote can.
