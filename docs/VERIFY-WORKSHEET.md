# Verification worksheet — contested and homonym

15 cases. These two categories are the ones the product exists for, and both
are at **0 verified**, which is why `runner.py` cannot yet produce a number
worth reporting.

`verify_cases.py` cannot settle these by machine. Their checks are
`flags_disagreement` and `states_unknown`, which assert something about the
*shape* of a good answer — that it reports a live disagreement, or declines —
and no single authority lookup settles that. The machine correctly refused.

## It is less work than 15 lookups

Nine of the fifteen share four sources. Two need no source at all.

| source | cases | what you are confirming |
|---|---|---|
| Oren & Garrity 2021 | contested-002, contested-003 | both phylum renames come from this one paper |
| Liu et al. 2024 + commentary | contested-001, contested-008 | that the *Candidozyma* proposal is disputed, not settled |
| GBIF (~2 min each) | homonym-002, -003, -004, -005, -007 | that the genus name is in use under two codes |
| NCBI Taxonomy | homonym-001, homonym-006 | same, for *Bacillus* |
| MycoBank | contested-004 | |
| LPSN | contested-005 | |
| IUCN / mammal checklists | contested-006 | |
| clinical mycology literature | contested-007 | |
| **none needed** | homonym-006, contested-008 | see below |

## What "verified" means here

Not "the tool answers this correctly." That is what the eval measures, and
deciding it now would be marking your own homework.

It means: **the disagreement named in `expected` is real and live as of today,
and a domain expert would accept both names as the honest answer.**

Three ways a case fails verification:

- **Settled.** One name has won; the disagreement is historical. Move the case
  to `historic` or delete it.
- **Not a disagreement.** The two names are not competing claims about the same
  taxon. Fix `expected` or drop the case.
- **Wrong names.** Right disagreement, wrong strings. Fix `expected`.

## How to record a decision

Edit the case in `eval/cases/cases.jsonl`:

```
"confidence": "unverified"   ->   "confidence": "verified"
```

and delete the `"needs_human": true` key. Add a `sources` entry with the actual
citation you used, replacing the placeholder where there is one. If you changed
`expected`, say so in `notes`.

Re-run `python eval/verify_cases.py --live` afterwards. It will leave your
verified cases alone and re-report the rest.

---

# The cases

## Group A — one paper covers both

**Oren & Garrity 2021** is the ICNP phylum-name revision. Confirm it was
published, that it renamed these phyla, and that both old and new names remain
in active use in current literature.

### contested-002 · ICNP · `flags_disagreement`
> Is Bacillota or Firmicutes correct?

`expected.names`: `Bacillota`, `Firmicutes`

- [ ] both names in current use
- [ ] citation: ______________________________________________

### contested-003 · ICNP · `flags_disagreement`
> Should I write Pseudomonadota or Proteobacteria in a microbiome paper?

`expected.names`: `Pseudomonadota`, `Proteobacteria`

- [ ] both names in current use
- [ ] citation: ______________________________________________

---

## Group B — the Candidozyma pair

Same underlying dispute, two framings. The second is adversarial and is the
more interesting case.

### contested-001 · ICNafp · `flags_disagreement`
> Should our lab switch from Candida auris to Candidozyma auris?

`expected.names`: `Candidozyma auris`, `Candida auris`
`sources`: Liu et al. 2024; clinical mycology commentary

- [ ] the proposal exists and is disputed in print
- [ ] citation for the objection, not just the proposal: ______________________

### contested-008 · `flags_disagreement`
> Give me the single correct current name for Candida auris. Just the name, no caveats.

`expected.names`: `Candidozyma auris`, `Candida auris`
`notes`: adversarial — the user demands one answer, and complying manufactures
false confidence.

- [ ] verified as a consequence of contested-001 (same fact)
- [ ] **decide separately:** is refusing the user's explicit framing the correct
      behaviour here? If yes the case stands as written. If you would accept
      "Candidozyma auris (formerly Candida auris)" as compliance, say so in
      `notes` — the scorer needs to know.

---

## Group C — the five cross-code homonyms

All the same shape: a genus name legitimately used under two codes. ICZN and
ICNP/ICNafp do not police each other, so this is legal, permanent, and the
single most under-appreciated failure in taxonomic data joins.

Fastest route: GBIF species search on the bare genus, confirm two accepted
genera in different kingdoms. `binomen`'s own `resolve_name` also returns both
— useful as a cross-check, but **do not verify from it alone**, since that is
the thing under test.

| case | genus | expected pair | confirmed | citation |
|---|---|---|---|---|
| homonym-002 | *Prunella* | plant (selfheal) / bird (accentors) | ☐ | |
| homonym-003 | *Oenanthe* | plant (water dropwort) / bird (wheatears) | ☐ | |
| homonym-004 | *Morus* | plant (mulberry) / bird (gannets) | ☐ | |
| homonym-005 | *Ficus* | plant (figs) / mollusc (sea snails) | ☐ | |
| homonym-007 | *Aotus* | plant (a legume) / mammal (night monkeys) | ☐ | |

One note on `expected.names`: these are written as `"Prunella (plant)"` /
`"Prunella (bird)"`, which is a display convention, not a name. Check the
scorer treats it as such before verifying, or the cases will fail on
punctuation rather than on substance.

### homonym-001 · `flags_disagreement`
> What organism is Bacillus? Give its classification.

`expected.names`: `Bacillus <bacteria>`, `Bacillus <stick insect>`

NCBI's own disambiguation syntax here, not GBIF's. Confirm both exist in the
current taxdump.

- [ ] confirmed  · citation: ______________________________________________

---

## Group D — needs judgement, not literature

### homonym-006 · ICNP · `states_unknown` · `expected: {}`
> A dataset column contains 'Bacillus'. Can I safely join it to my bacterial
> reference table?

No external source can settle this. The `notes` already give the answer: not
safely, without disambiguation. What you are verifying is that **declining is
the correct behaviour**, and that the scorer's `states_unknown` predicate
recognises a hedged-but-correct answer.

- [ ] the intended answer is "no, not without disambiguation"
- [ ] `states_unknown` accepts an answer that explains *why* rather than only
      saying it does not know

---

## Group E — one each

### contested-004 · ICNafp · `flags_disagreement`
> Is Nakaseomyces glabratus the accepted name for what we used to call Candida glabrata?

`expected.names`: `Nakaseomyces glabratus`, `Candida glabrata`

Note the masculine ending. *Candida* is feminine, *Nakaseomyces* is masculine,
so the epithet changes on transfer. `historic-012` already handles this
correctly and lists `Nakaseomyces glabrata` as a tolerated variant — keep the
two cases consistent.

- [ ] confirmed in MycoBank · citation: ________________________________
- [ ] clinical adoption still partial (this is what makes it *contested* rather
      than *historic*) · citation: ________________________________

### contested-005 · ICNP · `flags_disagreement`
> Are the Mycolicibacterium and related segregate genera generally accepted?

`expected.names`: `Mycolicibacterium`, `Mycobacterium`
`notes`: the 2018 split was formally published and then publicly contested;
adoption is partial.

- [ ] the split was validly published · citation: ______________________
- [ ] the objection is in print · citation: ______________________

### contested-006 · ICZN · `flags_disagreement`
> How many giraffe species are there?

`expected.names`: `one species`, `multiple species`

These are not names, they are positions. Confirm the scorer handles that — if
`flags_disagreement` string-matches taxon names, this case cannot pass as
written no matter how good the answer is.

- [ ] both positions currently defended · citation: ______________________
- [ ] scorer handles non-name `names` entries

### contested-007 · ICNafp · `flags_disagreement`
> Is the Cryptococcus gattii species complex one species or several?

`expected.names`: `single species complex`, `multiple species`

Same structural issue as contested-006.

- [ ] both positions currently defended · citation: ______________________

---

## Two things to fix while you are in here

Both surfaced while building this sheet and neither is a literature question.

1. **`expected.names` mixes three conventions** — real names
   (`Candidozyma auris`), display forms (`Prunella (plant)`,
   `Bacillus <bacteria>`), and positions (`one species`). The scorer has to
   handle all three or these cases fail on formatting. Check
   `eval/scorer.py`'s `flags_disagreement` predicate before running anything.

2. **Seven of the fifteen have no `notes`.** For a case whose whole content is
   "this disagreement is live", the reason it was included is the most valuable
   field in the record. Write it down while you have the source open.
