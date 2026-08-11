# Contributing

## Before you change anything

Run the tests. They encode design decisions, not just behavior — several will
fail if you make a change that looks like a simplification:

- `test_no_toplevel_current_name_field` fails if you add a convenience
  `current_name` string. That is the point; see README §3.
- `test_input_status_and_candidate_status_are_distinct` fails if you merge the
  two status fields back together.
- `test_unresolvable_comparison_is_unknown_not_different` fails if
  `compare_names` returns `false` for a name it could not resolve.
- `test_status_vocabularies_differ_between_codes` fails if you normalize away a
  code's native vocabulary.

If one of these fails and you believe the design is wrong, change the test and
say why in the PR. Do not route around it.

## Adding an authority

See `docs/EXTENDING.md`. Four rules: never raise, never pre-reconcile, always
pass the native status term through, always fill in real provenance.

## Adding cases

Add to `eval/cases/build_cases.py` and regenerate. New cases ship
`confidence: "unverified"` and must go through `eval/verify_cases.py`.

Write prompts the way a scientist would type them. A prompt that announces
itself as a nomenclature question tests the easy path; the failure lives in
prompts that do not.

**Never add to `heldout.jsonl` after looking at held-out results.** If you have
seen the holdout fail, it is burned and you need a new one.

## What not to do

- Do not commit the built index or downloaded taxdump.
- Do not tune tool descriptions against held-out cases.
- Do not add data from a non-redistributable source to the repo. Query and cite.
- Do not fill an unknown year, authority, or reference with a plausible value.
  Null with an explanation is correct; a citable fabrication is not.
