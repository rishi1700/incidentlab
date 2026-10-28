# Summary of dry-run-3

Runs recorded: 15 (15 usable, 0 harness errors, 0 --no-model records skipped).
Commits: 963239b.
Model: muse-spark-1.3; reasoning effort: medium; check limit: 12.

## Model accuracy

| Type | Correct | Total | Accuracy |
|---|---|---|---|
| fault | 9 | 9 | 100% |
| healthy | 2 | 3 | 67% |
| missing | 3 | 3 | 100% |
| all | 14 | 15 | 93% |

## Baseline accuracy

| Type | Correct | Total | Accuracy |
|---|---|---|---|
| fault | 8 | 9 | 89% |
| healthy | 3 | 3 | 100% |
| missing | 3 | 3 | 100% |
| all | 14 | 15 | 93% |

## False alarms

None.

## Citations

113/115 citations supported (98%). 0 runs with report warnings. 7 runs used all checks.

## Recovery

Repair applied on 10 runs, recovery verified on 10 (100%).
No repair on 5 runs, end-state verifier passed on 3.

## Cost and time

Total $0.812 and 441s of model time over 15 runs (520936 input / 88068 output tokens).
