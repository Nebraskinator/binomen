## Results

Model `SIMULATED-ANSWERS-NOT-A-REAL-RUN` · tool descriptions `broad` · index `taxdump-2026-08-11` · 101 cases · live authorities disabled (cached)

> **101 cases have unverified ground truth.** These numbers are exploratory and must not be cited. Run `eval/verify_cases.py` first.


### Pass rate by case category

| category | n | baseline | tools | instructed | delta (baseline→instructed) |
|---|---|---|---|---|---|
| control | 10 | 70% | 50% | 90% | +20 pts |
| historic | 14 | 64% | 79% | 79% | +14 pts |
| recent | 10 | 50% | 40% | 90% | +40 pts |
| split | 8 | 12% | 62% | 75% | +62 pts |
| homonym | 7 | 29% | 57% | 86% | +57 pts |
| distractor | 10 | 40% | 50% | 90% | +50 pts |
| multihop | 6 | 83% | 33% | 83% | +0 pts |
| literature | 10 | 10% | 60% | 90% | +80 pts |
| crosscode | 12 | 50% | 75% | 83% | +33 pts |
| contested | 8 | 38% | 50% | 75% | +38 pts |
| gene | 6 | 67% | 33% | 83% | +17 pts |

Read this table by row, not by column mean. 'Accuracy improved' is not the finding; *which categories moved and which did not* is.


### Error class distribution

| error class | baseline | tools | instructed |
|---|---|---|---|
| very_major | 17 (17%) | 16 (16%) | 4 (4%) |
| major | 33 (33%) | 27 (27%) | 12 (12%) |
| minor | 15 (15%) | 15 (15%) | 18 (18%) |
| none | 36 (36%) | 43 (43%) | 67 (66%) |

Severity-weighted error load (very major = 10, major = 4, minor = 1, none = 0):

| condition | total severity | per case |
|---|---|---|
| baseline | 317 | 3.14 |
| tools | 283 | 2.80 |
| instructed | 106 | 1.05 |


### Tool invocation and abstention


**tools**

| measure | value | meaning |
|---|---|---|
| tool invocation rate | 28% | cases where at least one tool was called |
| abstention failures | 72 (71%) | a tool was available and needed, and was not called |
| correct but unchecked | 39 | answered correctly without checking -- lucky, not reliable |

**instructed**

| measure | value | meaning |
|---|---|---|
| tool invocation rate | 95% | cases where at least one tool was called |
| abstention failures | 0 (0%) | a tool was available and needed, and was not called |
| correct but unchecked | 0 | answered correctly without checking -- lucky, not reliable |


### False confidence on contested cases

| condition | contested cases | single-answer responses |
|---|---|---|
| baseline | 8 | 5 (62%) |
| tools | 8 | 4 (50%) |
| instructed | 8 | 2 (25%) |

On these cases a single answer is wrong however authoritative it sounds.



### Very major errors

| case | condition | why |
|---|---|---|
| recent-001 | tools | gave a single answer where authorities genuinely disagree |
| recent-004 | tools | gave a single answer where authorities genuinely disagree |
| split-001 | baseline | applied a genus-level blanket substitution to a split; species that did not move would be silently renamed |
| split-001 | tools | applied a genus-level blanket substitution to a split; species that did not move would be silently renamed |
| split-003 | baseline | applied a genus-level blanket substitution to a split; species that did not move would be silently renamed |
| split-004 | baseline | applied a genus-level blanket substitution to a split; species that did not move would be silently renamed |
| split-004 | tools | applied a genus-level blanket substitution to a split; species that did not move would be silently renamed |
| split-005 | baseline | applied a genus-level blanket substitution to a split; species that did not move would be silently renamed |
| split-005 | tools | applied a genus-level blanket substitution to a split; species that did not move would be silently renamed |
| split-006 | baseline | applied a genus-level blanket substitution to a split; species that did not move would be silently renamed |
| homonym-001 | baseline | gave a single answer where authorities genuinely disagree |
| homonym-001 | tools | gave a single answer where authorities genuinely disagree |
| homonym-001 | instructed | gave a single answer where authorities genuinely disagree |
| distractor-001 | tools | said same=True, truth same=False. Conflated distinct taxa. |
| distractor-004 | baseline | said same=True, truth same=False. Conflated distinct taxa. |
| distractor-004 | tools | said same=True, truth same=False. Conflated distinct taxa. |
| distractor-004 | instructed | said same=True, truth same=False. Conflated distinct taxa. |
| distractor-006 | baseline | said same=False, truth same=True. Split one taxon into two. |
| distractor-007 | baseline | said same=True, truth same=False. Conflated distinct taxa. |
| distractor-007 | tools | said same=True, truth same=False. Conflated distinct taxa. |
| distractor-008 | baseline | said same=False, truth same=True. Split one taxon into two. |
| distractor-009 | baseline | said same=True, truth same=False. Conflated distinct taxa. |
| distractor-009 | tools | said same=True, truth same=False. Conflated distinct taxa. |
| distractor-010 | baseline | said same=False, truth same=True. Split one taxon into two. |
| distractor-010 | tools | said same=False, truth same=True. Split one taxon into two. |


### Error class definitions

| class | definition |
|---|---|
| very_major | Asserted two names are different taxa when they are the same, or the same when they are different. Silent data loss or false conflation; the downstream conclusion is wrong and nothing signals it. |
| major | Used a superseded name as current without flagging it. Retrieval is incomplete and the results look valid. |
| minor | Correct identification, missing provenance, source, or date qualifier. Not reproducible, but not wrong. |
| abstention_failure | Answered from parametric memory when a tool was available and needed. Measured separately because it co-occurs with correct answers. |
| false_confidence | Gave a single answer where authorities genuinely disagree. |
