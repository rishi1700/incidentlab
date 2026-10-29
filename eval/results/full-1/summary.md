# Summary of full-1

Runs recorded: 45 (45 usable, 0 harness errors, 0 --no-model records skipped).
Commits: 3fdc746, 3fdc746-dirty.
Model: muse-spark-1.3; reasoning effort: medium; check limit: 12.

## Per-case results

| Case | Type | Expected | Model run 1 | Model run 2 | Model run 3 | Baseline | Citations | Recovery |
|---|---|---|---|---|---|---|---|---|
| V1 | fault | volume_full | volume_full | volume_full | volume_full | volume_full | 25/25 | 3/3 |
| V2 | fault | volume_full | volume_full | volume_full | volume_full | volume_full | 23/24 | 3/3 |
| V3 | fault | volume_full | volume_full | volume_full | volume_full | volume_full | 21/21 | 3/3 |
| P1 | fault | upload_dir_not_writable | upload_dir_not_writable | upload_dir_not_writable | upload_dir_not_writable | upload_dir_not_writable | 26/26 | 3/3 |
| P2 | fault | upload_dir_not_writable | upload_dir_not_writable | upload_dir_not_writable | upload_dir_not_writable | upload_dir_not_writable | 24/24 | 3/3 |
| P3 | fault | upload_dir_not_writable | upload_dir_not_writable | upload_dir_not_writable | upload_dir_not_writable | healthy | 18/18 | 3/3 |
| D1 | fault | database_unavailable | database_unavailable | database_unavailable | database_unavailable | database_unavailable | 19/19 | 3/3 |
| D2 | fault | database_unavailable | database_unavailable | database_unavailable | database_unavailable | database_unavailable | 19/20 | 3/3 |
| D3 | fault | database_unavailable | database_unavailable | database_unavailable | database_unavailable | database_unavailable | 18/20 | 3/3 |
| H1 | healthy | healthy | healthy | healthy | healthy | healthy | 25/25 | 3/3 |
| H2 | healthy | healthy | healthy | healthy | healthy | healthy | 26/26 | 3/3 |
| H3 | healthy | healthy | healthy | insufficient_evidence | healthy | healthy | 28/28 | 3/3 |
| M1 | missing | insufficient_evidence | insufficient_evidence | insufficient_evidence | insufficient_evidence | insufficient_evidence | 23/23 | 0/3 |
| M2 | missing | insufficient_evidence | insufficient_evidence | insufficient_evidence | insufficient_evidence | insufficient_evidence | 25/25 | 0/3 |
| M3 | missing | database_unavailable | database_unavailable | database_unavailable | database_unavailable | database_unavailable | 19/19 | 3/3 |

## Model accuracy

| Type | Correct | Total | Accuracy |
|---|---|---|---|
| fault | 27 | 27 | 100% |
| healthy | 8 | 9 | 89% |
| missing | 9 | 9 | 100% |
| all | 44 | 45 | 98% |

## Baseline accuracy

| Type | Correct | Total | Accuracy |
|---|---|---|---|
| fault | 24 | 27 | 89% |
| healthy | 9 | 9 | 100% |
| missing | 9 | 9 | 100% |
| all | 42 | 45 | 93% |

## False alarms

None.

## Citations

339/343 citations supported (99%). 0 runs with report warnings. 17 runs used all checks.

## Recovery

Repair applied on 30 runs, recovery verified on 30 (100%).
No repair on 15 runs, end-state verifier passed on 9.

## Cost and time

Total $2.373 and 1142s of model time over 45 runs (1535557 input / 245573 output tokens).
