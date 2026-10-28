# IncidentLab evaluation plan (milestone 3)

The decisions below were confirmed on 28 October 2026, before any evaluation run.

## Decisions (confirmed 28 October 2026)

1. **Missing-evidence cases M1 and M2 expect `insufficient_evidence`.** A cause reached only by ruling out the other causes counts as wrong, because the three causes are not the only problems a real system can have (the app container being down is one).
2. **Add this line to the investigator's instructions before freezing:** "The listed causes are not the only possible problems. Don't conclude a cause only because the others are ruled out; choose insufficient_evidence unless the evidence shows the cause itself." This matches decision 1.
3. **The rule-based baseline checks `/health` before it reads the logs**, the way most operators would. The alternative, logs before health, catches P3 but raises false alarms on H2 and H3. Either is fine if fixed before running.
4. **No `other` cause in this evaluation.** The app-down case goes in the README under limitations.
5. **Keep the 12-check limit and medium reasoning effort.** Record runs that use all 12 checks.

## Cases

Three runs of each case: 45 model runs. Every run starts from the baseline state described under Runner.

| ID | Type | Setup | Unavailable checks | Expected cause | Expected repair |
|---|---|---|---|---|---|
| V1 | Fault | Volume filled with stale `.part` files (current inject) | | volume_full | clear_stale_temp_files |
| V2 | Fault | Inodes exhausted by many small stale `.part` files; bytes still free | | volume_full | clear_stale_temp_files |
| V3 | Fault | Volume nearly full: 300–600 KB left for the app user, so `/health` passes but 1 MB uploads fail | | volume_full | clear_stale_temp_files |
| P1 | Fault | `uploads/` owned by root, mode 755 (current inject) | | upload_dir_not_writable | restore_upload_dir_access |
| P2 | Fault | `uploads/` owned by 1000:1000 but mode 555 | | upload_dir_not_writable | restore_upload_dir_access |
| P3 | Fault | `tmp/` staging directory owned by root; `uploads/` fine, so `/health` passes | | upload_dir_not_writable | restore_upload_dir_access |
| D1 | Fault | `db` stopped (current inject) | | database_unavailable | restore_database |
| D2 | Fault | `db` paused | | database_unavailable | restore_database |
| D3 | Fault | `db` disconnected from the app network; still running and healthy | | database_unavailable | restore_database |
| H1 | Healthy | Clean lab | | healthy | none |
| H2 | Healthy | `db` restarted and recovered; requests during the restart left errors in the app log | | healthy | none |
| H3 | Healthy | V1 injected, one upload failed, then cleaned up; old "No space left" errors in the app log | | healthy | none |
| M1 | Missing evidence | P1 | `path_access`, `container_logs` | insufficient_evidence | none |
| M2 | Missing evidence | V1 | `disk_usage`, `container_logs` | insufficient_evidence | none |
| M3 | Missing evidence | D1 | `container_logs` | database_unavailable | restore_database |

M3 checks the other direction: evidence is missing, but what remains still shows the cause, so hedging there counts as wrong.

## Lab changes needed

- **App:** database connect timeout of 2 seconds, so `/health` answers within investigate.py's 3-second limit while `db` is paused (D2).
- **Verifier:** deletes its test file after comparing hashes (already requested), so runs don't fill the volume.
- **`restore_upload_dir_access`:** restores both `uploads/` and `tmp/` to 1000:1000, mode 755 (needed for P3).
- **`restore_database`:** starts, unpauses, or reconnects `db` to the app network with `--alias db`. Without the alias the app still can't resolve `db` after reconnecting (D3).
- **`clear_stale_temp_files`:** deletes with `find ... -mmin +60 -delete` inside one helper container, so it copes with tens of thousands of files (V2).
- **`lab/faults.py`:** one explicit branch per variant (already requested). Do each fill in a single helper container. The V2 and V3 files follow the same rules as V1: random `.part` names, owned by 1000:1000, dated two days ago.
- **Healthy setups:** H2 restarts `db` and sends a few requests during the restart so the app logs real errors, then waits until `/health` returns 200. H3 injects V1, makes one upload fail, runs the repair, then waits until `/health` returns 200.

## Changes to investigate.py (then freeze)

- Add a one-line description of each cause to the report schema:
  - `volume_full`: the app's data filesystem has no usable space or inodes left
  - `upload_dir_not_writable`: the app user cannot write to its upload or staging directory
  - `database_unavailable`: the app cannot reach a working database
  - `healthy`: uploads work now
  - `insufficient_evidence`: the evidence does not show one cause
- Add the instruction from decision 2, if confirmed.
- Then freeze investigate.py, the cases and the baseline rules. Record the git commit in every result. If anything changes after seeing results, re-run all cases and say so in the README.

## Rule-based baseline (eval/baseline.py)

It runs the same check functions as investigate.py (import `run_check`), in a fixed order, and returns the first match:

1. `db` in a state other than `running` (exited, paused, restarting): `database_unavailable`. The app not running: `insufficient_evidence`.
2. `disk_usage` on `/srv/lab-data` with `use_percent` or `inode_use_percent` at 95 or more: `volume_full`.
3. `path_access` on `uploads/` with `app_user_can_write` false: `upload_dir_not_writable`.
4. `/health` returns 200: `healthy`.
5. The last 100 app log lines contain "No space left": `volume_full`. "Permission denied": `upload_dir_not_writable`. A database connection or name-resolution error: `database_unavailable`.
6. Otherwise: `insufficient_evidence`.

A rule whose check is unavailable is skipped. The baseline is read-only, so it runs before the model on the same case state.

## Runner (eval/run.py)

For each case and run:

1. **Baseline state:** recreate both containers (`docker compose up -d --force-recreate`) so the logs only cover this case. Wait for `/health` to return 200, then confirm that `status` shows no fault and the verifier passes. If any of this fails, record a harness error and skip the run.
2. **Set up the case** and confirm it took effect (`status` shows the variant; for healthy cases the verifier passes).
3. **Run the rule-based baseline.**
4. **Run the model:** `investigate.py --no-followup --json ...`, with `INCIDENTLAB_UNAVAILABLE_CHECKS` set for M1–M3. Its approval prompt declines, because stdin is closed.
5. **If the diagnosis maps to a repair,** apply it with `repair.py <repair> --yes` and record the verifier result. Otherwise run the verifier anyway to record the end state.
6. **Reset everything.**

Run unattended (for example in `tmux`), resumable (skip runs already recorded), with no prompts. Expect about two to three hours and $3–5 for 45 runs.

## What to record

Per run, one JSON line in `eval/results/<date>-<commit>/runs.jsonl`:

- case, run number, commit
- expected cause, model cause, correct, false alarm (a fault named on a healthy case)
- citations supported out of total, report warnings, checks used, whether all 12 were used
- repair applied, recovery verified
- baseline cause, baseline correct
- time, tokens, cost

`eval/summarize.py` turns this into `summary.md`: accuracy for the model and the baseline by case type, false alarms, citation support, recovery rate, cost and time. Results are never edited by hand.

## Order of work

1. Lab changes and the new variants, each shown failing and then passing after its repair alone.
2. The investigate.py changes, the baseline and the runner, tested without calling the model.
3. A dry run of one run per case, by the user. This shakes out harness bugs, and the results are labelled as a dry run.
4. Commit and freeze, then the full run of 45, by the user.
5. Summary and README results.
