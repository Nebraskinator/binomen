# What's next

Written 2026-08-16, at the end of the session that turned this project around.
Enough context to pick up cold. `docs/FINDINGS.md` has the evidence behind
every number quoted here.

---

## Where the project actually stands

**The original premise is refuted.** A current frontier model does not need help
with species renames. Given four deliberately obscure ones it named the current
genus for all four unaided, and binomen's search query retrieved **95 fewer
papers** than the model's on one of them.

**What replaced it is narrower and better founded.** NCBI Taxonomy records what
sequence submitters call things; LPSN records what has standing under the ICNP.
Where they differ is information nobody publishes and no model can supply.
Measured over the full index:

```
binomials both sources hold          5,706
  disagree                           1,398   24.5%
  of those, LPSN recommends its
    own name for medical use           695
binomials NCBI retired, absent
  from LPSN                          6,497   53.2%  -- no ICNP standing
```

*Borreliella burgdorferi* (NCBI) vs *Borrelia burgdorferi* (LPSN, recommended
for medical use) is the headline case.

**Shipping state.** `data/lpsn.sqlite` (34,505 records) is harvested and joined.
`check_name` reports disagreements in both the Python resolver and the Node
extension, and stays cheap on agreement. 30 Node tests pass.

---

## 1. ICNafp register — the largest remaining win

**Why it is the priority.** Bacteria are 7.5% of the index. Fungi, plants and
algae are **43%** (129,410 species). A 20-name probe against GBIF found 4/18
disagreement (~22%, 3 substantive) — comparable to the 24.5% that justified the
LPSN build, over six times the coverage.

**What exists already**

- `src/binomen/authorities/mycobank.py` — `codes = (Code.ICNAFP,)`, and both
  `INDEX_FUNGORUM_BASE` (`indexfungorum.org/ixfwebservice/fungus.asmx`, a SOAP
  endpoint, **no credentials**) and `MYCOBANK_BASE` are already defined. It
  currently reports "not configured" unless `BINOMEN_MYCOBANK_ENDPOINT` is set.
- `scripts/harvest_lpsn.py` — the harvest/resolve/compare pattern to copy. It is
  LPSN-specific by name and mostly generic in shape.
- `src/binomen/registers.py` — the `Register` reader. It already reads a `code`
  key from the register's `meta` table and stays silent outside its own code.

**What has to change**

1. **`Register` opens exactly one file.** `DEFAULT_REGISTER` is hardcoded to
   `data/lpsn.sqlite`. Needs to become a set of registers keyed by code, with
   `Resolver` selecting on the NCBI verdict's `code`. Straightforward, but it is
   the piece of the design that was written for one register and now needs two.
2. **A harvester for Index Fungorum.** SOAP rather than JSON, so it will not be
   a copy-paste of the LPSN client. Budget real time for the transport.
3. **The register schema is generic enough** — `norm`, `full_name`,
   `correct_name`, `standing`, `medical_use`, `address`. `medical_use` will be
   NULL throughout (an LPSN concept). Add `code` to `meta`.

**The trap that nearly caught us on the bacterial side.** Index Fungorum is a
registry of *names*, not of *accepted names*. It tells you a name was validly
published; it does not always tell you which name is currently accepted. For
accepted-name opinions you want **Species Fungorum** (fungi) or **POWO / WFO**
(plants). Diff against the wrong one and every answer comes back "both names
exist" — true, and useless. Confirm which endpoint returns an accepted-name
opinion *before* building the harvester.

**ICZN has no register at all.** The ICZN mandates no central registry, so for
animals — 49% of the index — there is nothing to diff against. Best available is
aggregated expert opinion (Catalogue of Life, WoRMS), which is not the same kind
of source. This is a structural gap, not a backlog item, and the README should
say so rather than implying coverage is coming.

---

## 2. `expand_query` still drops abbreviated forms — measured cost, 95 papers

**Open. Not fixed.** `src/binomen/resolver.py` around line 766:

```python
"abbreviated_forms": sorted(abbreviated),
...
"pubmed_query": " OR ".join(f'"{t}"[tiab]' for t in ordered),
```

`abbreviated_forms` is computed and then omitted from the query the tool hands
back. Measured against PubMed:

```
"M. smegmatis"[tiab] NOT ("Mycobacterium smegmatis"[tiab]
  OR "Mycolicibacterium smegmatis"[tiab] OR "Bacterium smegmatis"[tiab])   = 95
```

Ninety-five papers use only the abbreviated form in title or abstract. A no-tool
baseline model included `"M. smegmatis"[tiab]` unprompted and beat binomen by
those 95 on that organism. This is the single defect with the largest measured
cost in the project and it is a two-line change.

Mirror it in `node/src/resolver.js`. Add a test asserting the abbreviated forms
appear in `pubmed_query`, since this has now been identified twice without being
fixed.

---

## 3. Rank disagreements are being reported as name disagreements

`compare()` in `scripts/harvest_lpsn.py` buckets **395** cases as
`different_target`. Some of those are not name disputes at all — they are
disagreements about *rank*:

- *Treponema pertenue* — NCBI ranks it a subspecies of *T. pallidum*; LPSN holds
  it at species.
- *Francisella novicida* — same shape, NCBI subsp. of *F. tularensis*.

Neither name is wrong; the sources disagree about whether the taxon is a species
or an infraspecific. That is a third finding type and deserves its own bucket in
`compare()` and its own message in `registers.py:annotate` — the advice to a
caller is different from "two different names".

Detection: NCBI's accepted name contains a rank marker (`subsp.`, `var.`,
`pv.`) and the register's does not, or the register's `category` field is
`species` where NCBI's name is infraspecific. The register table already stores
`category`.

---

## 4. The 102-case harness has still never been run

`eval/runner.py --condition both` is the instrument that compares answer quality
with and without tools. It has produced no real result, ever.
`eval/runs/example-run.jsonl` is stamped `SIMULATED-ANSWERS-NOT-A-REAL-RUN`.

**It refuses to run** because 65 of 102 cases carry unverified ground truth.

**Do not run the verified 37 as a shortcut.** They are 10/10 `control`, 11
`historic`, 8 `literature` — precisely the prominent, already-in-the-weights
material that the whole project was criticised for testing. `contested` 0/8,
`homonym` 0/7, `crosscode` 0/12 and `distractor` 0/10 have **zero** verified
cases. Running the subset would reproduce the original methodological error and
look like a result.

**The unblock** is `docs/VERIFY-WORKSHEET.md` — the 8 `contested` and 7
`homonym` cases, grouped by source. Less work than it sounds: Oren & Garrity
2021 settles two, Liu et al. 2024 settles two, five are ~2-minute GBIF checks,
and two need a judgement call rather than a citation.

**Three CONFLICTs still need adjudication**, and one may now be resolvable:

| case | term | status |
|---|---|---|
| `gene-004` | `SEPTIN9`, `MARCHF5` | Current HGNC symbols (renamed 2020 so Excel stops turning them into dates). Terms are right; check whether the verifier routes gene terms to `authorities/hgnc.py` at all. |
| `literature-001` | `C. difficile` | `check_name` now returns `ambiguous_abbreviation` for this. `verify_cases.py` should accept that when the expansions include another required term of the same case. |
| `multihop-001` | `Bacillus difficilis` | The 1935 basionym. NCBI does not carry pre-*Clostridium* history. **LPSN now works** — query it directly; this may simply resolve. |

Also check `eval/scorer.py`'s `flags_disagreement` predicate before spending
time in the literature. `expected.names` mixes three conventions — real names
(`Candidozyma auris`), display forms (`Prunella (plant)`, `Bacillus <bacteria>`)
and positions (`one species`, `multiple species`). If it string-matches taxon
names, `contested-006` and `contested-007` cannot pass no matter how good the
answer is.

---

## 5. Smaller open items

- **123 gender near-misses** (1.5%) inflating the "absent from LPSN" count —
  `vibrio cholera`/`cholerae`, `empedobacter breve`/`brevis`,
  `kingella kingii`/`kingae`. The `same_stem` helper in
  `scripts/compare_authority.py` already handles this shape; apply it on the
  join in `harvest_lpsn.py:compare()`.
- **116 dangling correct-name pointers** after `--resolve` — targets whose
  record was never harvested. Re-run `--resolve` after any additional
  `--category` pass.
- **1,164 no-ops in NCBI's own table** — binomial supersession rows whose
  replacement is the same name in different case or spelling. A data-quality
  observation about NCBI worth recording somewhere, not a bug to fix.
- **Release licensing.** The index artifact embedding LPSN must be **CC BY-SA
  4.0**; the repository's MIT licence covers source code only. `dist/manifest.json`
  needs an LPSN entry, the harvest date, and a licence field. `docs/DATA.md`
  already draws the code/data line for NCBI — extend it.
- **Version bump.** `check_name` gained `sources_disagree` / `register` /
  `do_not`. That is a user-visible contract change: bump `node/manifest.json`,
  `node/package.json` and `node/src/server.js` together (currently 0.2.8).

---

## Facts worth not re-deriving

| | |
|---|---|
| index composition | ICZN 144,787 species (49%) · ICNafp 129,410 (43%) · ICNP 22,368 (7.5%) |
| genus transfers in index | 262,936 total; ICNP 8,008 |
| LPSN harvest | 34,505 records — 28,958 species, 4,622 genera, 925 subspecies |
| LPSN enumeration | `advanced_search?category=species` pages 100 at a time; ~870 requests total; **not** per-genus, which costs 20–28k |
| LPSN auth | OIDC password grant at `sso.dsmz.de`, client id `api.lpsn.public`, realm `dsmz`; bearer token ~15 min |
| LPSN record shape | no `correct_name` field — `lpsn_correct_name_id` is an **integer** to resolve; `lpsn_address` is a DOI; `lorn_status` carries the medical-use flag |
| LPSN licence | CC BY-SA 4.0; automated download permitted via API or download page; link to the source page required |
| invocation | `terse` 3/27 vs `unconditional` 15/15 on domain-framed prompts, p ≈ 8×10⁻⁹. Solved; stop tuning it. |
| text fingerprint | `terse` `cb741215bc0b` · `unconditional` `676923ddcfe9` — runs are poolable only within a fingerprint |

---

## Gotchas that cost real time

**The echo bug.** A resolver that returns its input when it does not know the
answer. Written independently in three places and it produced a **98%
disagreement rate that did not exist**. `harvest_lpsn.py` now asserts at write
time that an accepted name cannot equal the record's own name, and re-enforces
it in SQL after the join. Keep that invariant in anything new.

**Wrong denominators, four separate times.** A regex over prompt text; counting
`also_acceptable` variants as required names; sampling from *all* accepted names
to measure disagreement (89/90 agreement, because most names never moved); and
including strain designations and *Candidatus* placeholders (47% became 92.8%).
Before believing any rate, ask what population the denominator is drawn from.

**Cheap-first ordering is wrong for adjudication.** `authorities_for` sorts tier
*ascending*, correct for the runtime cascade. A comparison script broke on the
first authority that answered, so GBIF (tier 2) shadowed LPSN (tier 3) on 93 of
100 names and silently measured GBIF again. Pin the authority explicitly when
adjudicating.

**GBIF is not independent of NCBI for bacteria** — its bacterial backbone
derives from LPSN. Agreement between them measures nothing. It is a reasonable
second opinion for eukaryotes and worthless for ICNP.

**Unverified claims in docstrings.** `lpsn.py` asserted that LPSN's terms
restrict bulk redistribution. They do not — CC BY-SA, API harvesting explicitly
permitted. That one sentence steered the architecture toward a per-user design
for two rounds. Quote and date the source, or do not assert it.

**Environment.** The Cowork bridge shell has no network — LPSN, GBIF and PubMed
calls must run on the user's machine. Cloud-container egress blocks
`api.gbif.org` and `dsmz.de` (WebFetch reaches GBIF; the direct proxy does not).
Git commands issued through the bridge leave a stale `.git/index.lock` that the
bridge cannot delete — the user has to remove it. Node tests run against a
synthetic fixture, not the real index, so any test needing real data must build
its own fixture.
