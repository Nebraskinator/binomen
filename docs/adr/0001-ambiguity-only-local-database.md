# The local database holds only ambiguous names

The database shipped with binomen stores a row only for names that carry an
**ambiguity**; a name absent from it has no recorded alternatives. This keeps the
shipped artefact small enough to bundle — 663,228 rows in 29 MB, against 107 MB
for a full copy of the backbone's name table — and makes the check itself trivial:
presence *is* the finding.

## Consequences

Absence has to mean two different things, so it is not allowed to be silent. One
Bloom filter per code, over the names with no recorded history, separates "clean"
from "unknown" at a cost of 1.9 MB. Without them a misspelling would return the
same nothing as a settled name, and a silent all-clear on a typo is worse than an
unfamiliar wrong name — see `docs/FINDINGS.md` §5.

Environmental and *Candidatus* placeholders, unparseable strings and ICNafp bare
names are excluded (~495,000 rows). None of them is a name a person writes in
prose, and bare names have no standing by definition.

The 40 MB budget is enforced as a build failure with per-source row counts, not as
a guideline. A size preference erodes one register at a time and the erosion is
invisible until an install takes ten minutes.
