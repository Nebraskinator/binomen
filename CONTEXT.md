# binomen

Biological names are not stable identifiers, and which name is "right" depends on
which body you asked. This context is the vocabulary for describing names, the
sources that hold opinions about them, and the disagreements between those
sources — which are the thing binomen exists to surface.

## Language

### Sources

**Backbone**:
The single classification every name is looked up against first, covering all
organisms. NCBI Taxonomy is the backbone: it exists to hang sequence records on,
so it holds a name for anything submitted, whether or not that name has standing.
_Avoid_: authority (the backbone is explicitly not one), reference database

**Register**:
A code's list of names that have standing under it, shipped as local data.
LPSN under the ICNP, Species Fungorum Plus under the ICNafp, the ICTV Master
Species List for viruses.
_Avoid_: authority list, taxonomy, checklist

**Authority**:
The organisation that publishes a register, consulted only when a register is
built. The same thing as a register seen from the other side: DSMZ publishes
LPSN, Kew publishes Species Fungorum Plus.
_Avoid_: source, provider, vendor

**Code**:
The body of rules governing how names in one group of organisms are formed,
published and superseded — ICNP for prokaryotes, ICNafp for algae, fungi and
plants, ICZN for animals, ICTV for viruses. Codes disagree with one another by
design, so "the current name" is only answerable once you know which code applies.

**Jurisdiction**:
The relationship between a register and a name: a register has jurisdiction over
a name when the name falls under that register's code. LPSN has nothing to say
about *Homo sapiens*, and its silence there is not evidence.

**Snapshot**:
The dated state of a source at the moment it was harvested. Every answer names
the snapshot it came from, because a claim about a name without a date is not
checkable.
_Avoid_: version, release, current data

### Names and their trouble

**Ambiguity**:
The property that makes a name worth reporting: it has alternatives, or others
have it. A name is ambiguous when the backbone supersedes it, when the backbone
records alternative names for it, when a register's accepted name differs from the
backbone's, when a register with jurisdiction has no record of it, when it is a
homonym, or when the sources agree on the name but disagree on its rank.
_Avoid_: conflict, issue, problem

**Cluster**:
All the names that are alternatives of one another for a single organism under a
single code, together with what each source says about them. The unit a lookup
returns.
_Avoid_: group, record, entry, taxon

**Alternative name**:
Any other name in a name's cluster — a superseded name, a current one, or a
register's preferred one. Deliberately broader than *synonym*, which under each
code means something narrower and code-specific.
_Avoid_: synonym (except where a code's own meaning is intended), alias

**Standing**:
Whether a register recognises a name as validly published under its code. Absence
from a register with jurisdiction is a real answer — under the ICNP a name has no
standing until validly published — and is different from a name being unknown.
_Avoid_: validity, status (both mean several things at once)

**Homonym**:
One spelling used for unrelated organisms under different codes — *Bacillus* the
bacterium and *Bacillus* the stick insect. Homonyms are not alternatives of each
other and never share a cluster.

**Rank disagreement**:
Sources naming the same organism identically but placing it at different ranks —
NCBI holding *Treponema pertenue* as a subspecies where LPSN holds it at species.
A distinct finding from a name disagreement, and not evidence of one.

**Medical-use recommendation**:
A register's statement that its own name should be preferred in clinical contexts,
LPSN's `lorn_status`. Reporting the backbone's name alone where one of these
exists is an error, not a stylistic difference.

### Answers

**Verdict**:
What a lookup returns about a name: ambiguous, clean, or unknown. The three are
kept distinct because a misspelling reported as clean is a silent all-clear, which
is worse than an unfamiliar wrong name.
_Avoid_: result, status, response

**Escalation**:
A verdict's signal that a cheap answer has more behind it and a fuller lookup is
warranted. Escalation is offered, never performed on the caller's behalf.

**Provenance**:
The source, its snapshot date, and the record's own link or DOI, attached to every
value binomen reports. Nothing binomen returns is generated; provenance is what
makes that claim checkable rather than merely asserted.
