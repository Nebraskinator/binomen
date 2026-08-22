# Findings

What binomen was built to fix, whether it fixes it, and what it turned out to
be for instead.

**The premise was wrong and the project survived it.** A current frontier model
does not need help with the failure this tool was built for. What it cannot do
— and what nothing else publishes — is tell you which of two reference
databases you are standing in. Measured against LPSN: **1,398 of 5,706
bacterial binomials that both NCBI and the ICNP register hold have different
accepted names, and in 695 of those the register explicitly recommends its own
name for medical use.**

This document is written to be useful to someone considering building the same
thing. The negative result is in §3–§5; the positive one is §6.

---

## 1. The premise

Biological names change. *Bacteroides vulgatus* is now *Phocaeicola vulgatus*;
*Penicillium marneffei* is now *Talaromyces marneffei*. Both names persist in
the literature, so familiarity is no evidence of currency.

The premise was that a language model, holding both names with no date attached
to either, would not reliably know which is current, and a lookup against a
dated copy of NCBI Taxonomy would fix that.

The problem is real. The premise about the model is not.

---

## 2. The problem is real

PubMed, title/abstract:

| query | hits |
|---|---|
| `"Phocaeicola vulgatus"[tiab] NOT "Bacteroides vulgatus"[tiab]` | 11 |
| `"Bacteroides vulgatus"` (Europe PMC, title/abstract) | 663 |

Literature does fragment across a rename. Everything below is about whether a
tool is needed to handle it.

---

## 3. Invocation — answered, and not the interesting question

A tool that is never called cannot affect any outcome, so this was measured
first. Method in `docs/TESTING.md`. Claude Code, Opus, five replicates.

Domain-framed prompts — an organism is named but the question is about the
domain:

| instructions variant | fired | 95% CI |
|---|---|---|
| `terse` (`cb741215bc0b`) | 3/27 | 0.04–0.28 |
| `unconditional` (`676923ddcfe9`) | 15/15 | 0.80–1.00 |

Fisher exact p ≈ 8×10⁻⁹. On prompts naming no organism, `terse` fired 0/21 and
`unconditional` 3/30 (p = 0.26) — a false-positive rate not yet distinguishable
between the two.

Ran under Claude Code, which frames itself as a coding assistant; rates are a
lower bound and are not comparable to Claude Desktop. The comparison between
arms is valid, the absolute numbers are not.

**Invocation is solvable by wording.** It was never the hard part.

---

## 4. Does the tool improve a literature search? No — it made it worse

`expand_query` returns every name recorded for a taxon so a search covers all
of them. Tested on four organisms chosen to be *obscure*, since famous renames
prove nothing about a model's weights.

Baseline: Claude Sonnet, in a subagent with no tools and no knowledge that
binomen exists, asked for a comprehensive PubMed query. Treatment: binomen's
`pubmed_query`.

**The baseline named the current genus for all four unaided**, including the
two published after 2018.

| query | hits | meaning |
|---|---|---|
| `"Pseudobacterium rectale" OR "Roseburia rectale"` | **1** | everything binomen found that the baseline missed |
| `"Agathobacter rectale" NOT "Agathobacter rectalis"` | **0** | the reverse, on that organism |
| `"M. smegmatis" NOT ("Mycobacterium smegmatis" OR "Mycolicibacterium smegmatis" OR "Bacterium smegmatis")` | **95** | papers the baseline retrieved and binomen's query did not |

binomen's total unique contribution across four organisms was **one paper**.
Its deficit on one organism was **95**, because `expand_query` computes
abbreviated forms and then omits them from the query it returns.

---

## 5. Is the model *wrong*, as opposed to incomplete?

Two experiments, and they point in opposite directions until you separate the
prominent names from the long tail.

**Five realistic questions on contested and homonym cases.** The model was
correct on all five, and on *"is M. smegmatis still in Mycobacterium?"* it
correctly called the Gupta split contested while binomen asserted it as settled.

**Two hundred names sampled mechanically from the index** — 80 from clinically
prominent genera, 70 other bacteria and fungi, 50 animals and plants, fixed
seed:

| | count | 95% CI |
|---|---|---|
| declined (`UNKNOWN`) | 137/200 | 0.62–0.75 |
| **disagreed with NCBI when it committed** | **18/63** | 0.19–0.41 |

The model declines two-thirds of the long tail — the safe behaviour. When it
commits it disagrees with NCBI about 29% of the time, and **half of those are
the model asserting a superseded name is still current**: a silent all-clear,
worse than an unfamiliar wrong name because it gives the reader nothing to
check.

Adjudicated against GBIF, binomen won 7 of the 8 non-bacterial disagreements
GBIF could rule on. Adjudicated against LPSN, three of the four bacterial ones
went the other way — *Mycobacterium arupense*, *Pseudomonas guguanensis* and
*Streptomyces paucisporeus* are all names LPSN keeps and NCBI retired, so the
model's "still current" was right and NCBI was the outlier.

That last result is what turned this project around.

---

## 6. What it is actually for

**NCBI Taxonomy records what sequence submitters call things. LPSN records what
has standing under the ICNP. Those are different questions and they have
different answers.**

Full harvest of LPSN (34,505 records: 28,958 species, 4,622 genera, 925
subspecies) joined against every ICNP name NCBI records as superseded:

```
BINOMIALS BOTH SOURCES HOLD          5,706
  disagree                           1,398   24.5%
    LPSN kept the name NCBI retired  1,003
    LPSN points somewhere else         395
  of the disagreements, LPSN
    recommends its own name for
    medical use                        695

BINOMIALS NCBI RETIRED, ABSENT
  FROM LPSN                          6,497   53.2%  -- no ICNP standing
```

Both figures fall inside the confidence intervals of an independently
hand-checked 100-name sample (21% [12–33%] and 47% [38–57%]) at 250× the scale.
A residual check found only 1.5% of the "absent" to be spelling near-misses;
5,105 of 8,279 do not have their genus in LPSN at all.

The 695 are the product:

| NCBI says | LPSN says | |
|---|---|---|
| *Borreliella burgdorferi* | ***Borrelia burgdorferi*** | recommended for medical use |
| *Fluoribacter dumoffii* | ***Legionella dumoffii*** | recommended for medical use |
| *Sinorhizobium meliloti* | ***Ensifer meliloti*** | recommended for medical use |
| *Ectopseudomonas mendocina* | ***Pseudomonas mendocina*** | recommended for medical use |
| *Novacetimonas hansenii* | ***Komagataeibacter hansenii*** | recommended for medical use |

Without this table binomen hands a clinician writing about Lyme disease the
genus name the ICNP register advises against. With it, `check_name` returns
both names, the standing, the medical-use recommendation and the register's
DOI, and says explicitly that reporting one alone is an error.

This is a narrower claim than the project started with, and a much better
founded one. It is not "the model does not know the new name." It is "no model
can tell you which of two databases you are in, and nobody publishes the
difference."

---

## 7. Limitations

- **ICNP only.** Bacteria are 7.5% of the index. ICNafp is 43% and showed ~22%
  disagreement against GBIF in a 20-name probe; MycoBank and Index Fungorum are
  the equivalent build and are not done. ICZN has no central register at all,
  which is a structural gap, not a backlog item.
- **§4 and §5 are small.** Four organisms, five questions, one baseline model
  (Claude Sonnet, August 2026), no replicates on the outcome experiments. The
  200-name study has replicates and a fixed seed; the rest do not.
- **PubMed hit counts are a proxy.** Retrieving a paper is not a better
  meta-analysis. Nothing downstream of retrieval was measured.
- **The 102-case severity-graded harness has still never been run.** 65 cases
  carry unverified ground truth and the categories that matter most —
  `contested` 0/8, `homonym` 0/7, `crosscode` 0/12 — have no verified cases.
  See `docs/VERIFY-WORKSHEET.md`.
- **`different_target` (395) conflates two things.** *Treponema pertenue* is
  NCBI ranking it a subspecies while LPSN holds it at species — a rank
  disagreement, not a different name.
- **NCBI's own table contains 1,164 no-ops** among binomial supersessions: rows
  whose replacement is the same name in different case or spelling.

---

## 8. What the harness caught about itself

Independent of any result, this project produced clean, plausible, wrong
numbers **eight times**, and every one came from an instrument rather than from
the data. Four share a single shape.

**The echo.** A resolver that returns its input when it does not know the
answer. Written independently in three places — `gbif.py` returning
`canonicalName` for a synonym because GBIF's match endpoint omits `accepted`;
`lpsn.py` falling back to `full_name` twice. Downstream, a comparison script
read those echoes as "the authority says this retired name is still current"
and reported a **98% disagreement rate** that did not exist. The fix that
sticks is structural: `harvest_lpsn.py` asserts at write time that an accepted
name cannot equal the record's own name, and the invariant is re-enforced in
SQL after the join.

**Wrong denominators, four times.** A regex over prompt text reported a 39%
index coverage hole that was almost entirely strings like "Actinobacteria
still". Counting `also_acceptable` variants as required names invented a
coverage gap in a case file that was correct. Sampling uniformly from *all*
accepted names to measure disagreement returned 89/90 agreement, because most
names have never moved — the signal lives in names that changed. Including
strain designations and *Candidatus* placeholders turned a 47% no-standing rate
into 92.8%.

**Null runs scored as zeros.** Rate limiting produced rows with no assistant
text and no tool call — 12 of 45, nine of them an entire replicate. They
carried no error field, so "errors are excluded, not zeroed" never saw them.
The positive control read 4/5 when it had fired on every observation that
existed.

**Outcome-correlated crashes.** A cp1252 decode failure killed runs that *wrote
prose* and spared runs that called a tool immediately. Excluding them deleted
zeros and inflated the fire rate. The same bug then reappeared in a new test
written after the fix.

**A comparator that shared an upstream.** GBIF was used to adjudicate NCBI on
bacteria. GBIF's bacterial backbone derives from LPSN, so agreement between
them measured nothing; it returned 0/16 and looked like a finding.

**A cascade queried in the wrong order.** `authorities_for` sorts by tier
ascending — correct for a cheap-first runtime cascade, exactly wrong for
adjudication. A comparison script broke on the first authority that answered,
so GBIF (tier 2) shadowed LPSN (tier 3) on 93 of 100 names and silently
measured GBIF again.

**An unverified claim in a docstring.** `lpsn.py` asserted that LPSN's terms
restrict bulk redistribution. LPSN is CC BY-SA 4.0 and explicitly permits
automated download via its API. That sentence steered the architecture toward a
per-user design for two rounds before anyone read the terms.

The taxonomy result will age. This section will not.

---

## 9. Reproducing

```bash
make bootstrap
python scripts/sweep_cases.py                       # offline verdict distribution
python scripts/compare_authority.py --code ICNP -n 100 --authority lpsn
python scripts/harvest_lpsn.py --probe              # 2 requests
python scripts/harvest_lpsn.py --all                # ~870 requests, ~10 min
python scripts/harvest_lpsn.py --all --category genus
python scripts/harvest_lpsn.py --all --category subspecies
python scripts/harvest_lpsn.py --resolve
python scripts/harvest_lpsn.py --compare
```

`BINOMEN_LPSN_USER` / `BINOMEN_LPSN_PASSWORD` are needed to **build** a
register, never to use one. §4 and §5 were run by hand and are reproducible
from their descriptions: the baseline is any capable model given the same
prompt with no tools.

## 10. Licensing

NCBI Taxonomy is public domain. **LPSN is CC BY-SA 4.0**, and an artefact
embedding it inherits share-alike — which is why the register ships as its own
database (`data/lpsn.sqlite`) rather than as columns on the index. The MIT
licence on this repository covers source code only. Every register row carries
the record's DOI, because LPSN's terms require a link to the page material came
from and a footnote is easier to drop than a column.
