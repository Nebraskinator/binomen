# Case format

One JSON object per line (JSONL). Fields:

| field | required | meaning |
|---|---|---|
| `id` | yes | stable identifier, `<category>-NNN` |
| `category` | yes | one of the eleven categories below |
| `code` | yes | governing nomenclatural code, or `n/a` |
| `prompt` | yes | what the agent is asked. Written the way a user would write it, not as a taxonomy quiz |
| `expected` | yes | ground-truth object, shape depends on `check` |
| `check` | yes | which scorer predicate applies |
| `tool_expected` | yes | whether a correct agent should have called a tool. Used for the abstention measure |
| `confidence` | yes | `verified` or `unverified` — see below |
| `sources` | yes | where the ground truth comes from |
| `notes` | no | why the case is interesting, what the trap is |
| `holdout` | no | `true` if reserved; held-out cases live in `heldout.jsonl` |

## `check` predicates

| predicate | expected fields | passes when |
|---|---|---|
| `current_name` | `accepted`, optional `also_acceptable` | the answer asserts one of the accepted names as current |
| `same_taxon` | `same` (bool) | the answer's sameness judgement matches |
| `flags_disagreement` | `names` (list) | the answer names more than one candidate AND says authorities disagree |
| `must_include_terms` | `terms` (list), `min_fraction` | the answer's search terms cover at least that fraction |
| `must_not_substitute` | `wrong` (list) | the answer does NOT assert any of these as the current name |
| `split_disambiguation` | `mapping` (dict) | per-species mapping is right and no genus-level blanket substitution is made |
| `states_unknown` | — | the answer declines to assert, or explicitly flags uncertainty |
| `lineage_contains` | `taxa` (list) | named higher taxa appear |

## Categories

1. `control` — current, stable, unambiguous
2. `historic` — well-known changes, likely inside the training distribution
3. `recent` — near or after plausible training cutoffs
4. `split` — one name becomes many
5. `homonym` — one string, different taxa
6. `distractor` — similar names that are genuinely different organisms
7. `multihop` — changed more than once
8. `literature` — retrieval tasks where query expansion is the correct behavior
9. `crosscode` — spanning bacteria, fungi, viruses, plants, animals
10. `contested` — active disputes; flagging disagreement is the correct answer
11. `gene` — non-taxonomic identifiers (Tier 4)

## `confidence` and why it matters

`unverified` means the ground truth was drafted from secondary knowledge and
has NOT been checked against a primary source. **Do not report results computed
over unverified cases.** Run:

    python eval/verify_cases.py --live

which checks each case against the configured authorities and rewrites
`confidence` where it can confirm the ground truth, and flags conflicts where it
cannot. Anything still `unverified` after that needs a human with the primary
literature.

This is not bureaucratic. A benchmark whose ground truth was written from the
same parametric memory being evaluated measures nothing, and this project's
whole thesis is that confident unverified assertion is the failure mode.

## Holdout discipline

`cases.jsonl` is the development set: iterate on prompts, tool descriptions and
the scorer against it freely. `heldout.jsonl` is run once, at the end, for the
reported numbers. If you look at held-out failures and change anything, the
holdout is burned and you need a new one.
