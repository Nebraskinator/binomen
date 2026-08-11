## Results

Model `SIMULATED-ANSWERS-NOT-A-REAL-RUN` · tool descriptions `broad` · index `fixture-v1` · 101 cases · live authorities disabled (cached)

> **101 cases have unverified ground truth.** These numbers are exploratory and must not be cited. Run `eval/verify_cases.py` first.


### Pass rate by case category

| category | n | baseline | tools | delta |
|---|---|---|---|---|
| control | 10 | 60% | 80% | +20 pts |
| historic | 14 | 21% | 93% | +71 pts |
| recent | 10 | 60% | 80% | +20 pts |
| split | 8 | 50% | 88% | +38 pts |
| homonym | 7 | 71% | 86% | +14 pts |
| distractor | 10 | 30% | 80% | +50 pts |
| multihop | 6 | 50% | 67% | +17 pts |
| literature | 10 | 20% | 70% | +50 pts |
| crosscode | 12 | 33% | 92% | +58 pts |
| contested | 8 | 0% | 62% | +62 pts |
| gene | 6 | 50% | 50% | +0 pts |

Read this table by row, not by column mean. 'Accuracy improved' is not the finding; *which categories moved and which did not* is.


### Error class distribution

| error class | baseline | tools |
|---|---|---|
| very_major | 21 (21%) | 6 (6%) |
| major | 39 (39%) | 15 (15%) |
| minor | 13 (13%) | 18 (18%) |
| none | 28 (28%) | 62 (61%) |

Severity-weighted error load (very major = 10, major = 4, minor = 1, none = 0):

| condition | total severity | per case |
|---|---|---|
| baseline | 379 | 3.75 |
| tools | 138 | 1.37 |


### Tool invocation and abstention

| measure | value | meaning |
|---|---|---|
| tool invocation rate | 81% | cases where at least one tool was called |
| abstention failures | 18 (18%) | a tool was available and needed, and was not called |
| correct but unchecked | 11 | answered correctly without checking -- lucky, not reliable |

**This is the research question.** A model that answers correctly without calling the tool has not demonstrated that it knows the answer; it has demonstrated that this particular name happened to be well represented in its training data with the current form dominant. The 'correct but unchecked' row is the count of cases where accuracy and reliability come apart.


Calls per tool:

| tool | calls |
|---|---|
| resolve_name | 82 |


### False confidence on contested cases

| condition | contested cases | single-answer responses |
|---|---|---|
| baseline | 8 | 8 (100%) |
| tools | 8 | 3 (38%) |

On these cases a single answer is wrong however authoritative it sounds.



### Very major errors

| case | condition | why |
|---|---|---|
| historic-010 | baseline | gave a single answer where authorities genuinely disagree |
| historic-011 | baseline | gave a single answer where authorities genuinely disagree |
| recent-001 | baseline | gave a single answer where authorities genuinely disagree |
| recent-001 | tools | gave a single answer where authorities genuinely disagree |
| split-004 | baseline | applied a genus-level blanket substitution to a split; species that did not move would be silently renamed |
| split-005 | baseline | applied a genus-level blanket substitution to a split; species that did not move would be silently renamed |
| split-006 | baseline | applied a genus-level blanket substitution to a split; species that did not move would be silently renamed |
| split-006 | tools | applied a genus-level blanket substitution to a split; species that did not move would be silently renamed |
| distractor-002 | baseline | said same=True, truth same=False. Conflated distinct taxa. |
| distractor-003 | baseline | said same=True, truth same=False. Conflated distinct taxa. |
| distractor-004 | baseline | said same=True, truth same=False. Conflated distinct taxa. |
| distractor-007 | baseline | said same=True, truth same=False. Conflated distinct taxa. |
| distractor-009 | baseline | said same=True, truth same=False. Conflated distinct taxa. |
| distractor-009 | tools | said same=True, truth same=False. Conflated distinct taxa. |
| distractor-010 | baseline | said same=False, truth same=True. Split one taxon into two. |
| crosscode-009 | baseline | said same=True, truth same=False. Conflated distinct taxa. |
| contested-001 | baseline | gave a single answer where authorities genuinely disagree |
| contested-002 | baseline | gave a single answer where authorities genuinely disagree |
| contested-002 | tools | gave a single answer where authorities genuinely disagree |
| contested-003 | baseline | gave a single answer where authorities genuinely disagree |
| contested-004 | baseline | gave a single answer where authorities genuinely disagree |
| contested-005 | baseline | gave a single answer where authorities genuinely disagree |
| contested-005 | tools | gave a single answer where authorities genuinely disagree |
| contested-006 | baseline | gave a single answer where authorities genuinely disagree |
| contested-007 | baseline | gave a single answer where authorities genuinely disagree |


### Error class definitions

| class | definition |
|---|---|
| very_major | Asserted two names are different taxa when they are the same, or the same when they are different. Silent data loss or false conflation; the downstream conclusion is wrong and nothing signals it. |
| major | Used a superseded name as current without flagging it. Retrieval is incomplete and the results look valid. |
| minor | Correct identification, missing provenance, source, or date qualifier. Not reproducible, but not wrong. |
| abstention_failure | Answered from parametric memory when a tool was available and needed. Measured separately because it co-occurs with correct answers. |
| false_confidence | Gave a single answer where authorities genuinely disagree. |
