#!/usr/bin/env python3
"""Evaluation runner for IncidentLab (eval/PLAN.md).

For each case and run: recreate both containers for a clean baseline, set up
the case, run the rule-based baseline, run the model, apply the mapped repair
(repairs run without the approval prompt; during evaluation there is no
approval prompt), then reset every fault family. Resumable: runs already
recorded are skipped. No prompts; run unattended (for example in tmux).

Results go to eval/results/<date>-<commit>/runs.jsonl, one JSON line per run.
--no-model does every step except investigate.py (repairs never apply without
a diagnosis, so the verifier-only branch runs instead) and writes to a
separate nomodel- directory so smoke tests never pollute real results.
With the model, MODEL_API_KEY is checked before the first run and the whole
run stops after two model errors in a row.

Usage:
    .venv/bin/python eval/run.py [--cases V1,D1] [--runs 3] [--no-model]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

import investigate
from lab import faults

import baseline as baseline_module

CASES_PATH = ROOT / "eval" / "cases.json"
RESULTS_ROOT = ROOT / "eval" / "results"


class HarnessError(Exception):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


def _check_api_key() -> str | None:
    """Error unless MODEL_API_KEY is set and clean. Never returns the key."""
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        return "MODEL_API_KEY is not set; export it before running the evaluation."
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in key):
        return ("MODEL_API_KEY contains whitespace or control characters;"
                " check it for copy-paste errors (the key itself is never printed).")
    return None


class _ModelErrorStop:
    """Stops the run after model errors in consecutive executed runs."""

    def __init__(self, limit: int = 2):
        self.limit = limit
        self.streak = 0

    def note(self, record: dict) -> bool:
        """Note one executed run. True when the whole run must stop."""
        if record.get("stage") == "model":
            self.streak += 1
        else:
            self.streak = 0
        return self.streak >= self.limit


def _git_commit() -> str:
    try:
        short = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, check=True, timeout=30,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return f"{short}-dirty" if dirty else short


def _load_recorded(path: Path) -> set[tuple[str, int]]:
    """(case, run) pairs with a completed, non-error record."""
    done: set[tuple[str, int]] = set()
    if not path.exists():
        return done
    with open(path) as f:
        for number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as err:
                raise SystemExit(f"corrupt line {number} in {path}: {err}")
            if "harness_error" not in record and not record.get("no_model"):
                done.add((record["case"], record["run"]))
    return done


def _append_record(path: Path, record: dict) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def _recreate_lab() -> None:
    try:
        subprocess.run(
            ["docker", "compose", "-f", "lab/compose.yaml", "up", "-d",
             "--force-recreate"],
            cwd=ROOT, timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as err:
        raise HarnessError("recreate", f"compose up failed: {err}") from None
    try:
        faults._wait_app_healthy()   # compose only waits for db, not the app
    except Exception as err:   # noqa: BLE001 - reported as a harness error below
        raise HarnessError("recreate",
                           f"app never healthy after recreate: {err}") from None


def _reset_everything() -> None:
    try:
        faults.reset("all")
    except Exception as err:   # noqa: BLE001 - next run's baseline check catches it
        print(f"WARNING: reset all failed: {err}", file=sys.stderr)


def _run_model(case: dict, model_path: Path, timeout: int) -> dict:
    env = dict(os.environ)
    env["INCIDENTLAB_UNAVAILABLE_CHECKS"] = ",".join(case["unavailable_checks"])
    try:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "investigate.py"),
             "--no-followup", "--json", str(model_path)],
            cwd=ROOT, stdin=subprocess.DEVNULL, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        raise HarnessError("model", f"model run failed: {err}") from None
    if proc.returncode != 0:
        raise HarnessError("model", f"investigate.py exited {proc.returncode}")
    try:
        with open(model_path) as f:
            data = json.load(f)
        return {
            "cause": data["report"]["cause"],
            "supported": data["validation"]["supported"],
            "total": data["validation"]["total"],
            "warnings": data["validation"]["warnings"],
            "checks_used": len(data["evidence"]),
            "seconds": data["seconds"],
            "usage": data["usage"],
            "cost_usd": data["cost_usd"],
        }
    except (OSError, ValueError, KeyError, TypeError) as err:
        raise HarnessError("model", f"bad model json: {err}") from None


def _run_repair_step(repair: str | None) -> tuple[str | None, bool]:
    """Apply the repair with --yes, or verify the end state without one."""
    if repair is None:
        argv = ["verify"]
    else:
        argv = [repair, "--yes"]
    try:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "repair.py"), *argv], cwd=ROOT,
        )
    except OSError as err:
        raise HarnessError("repair", f"repair.py failed to run: {err}") from None
    return repair, proc.returncode == 0


def _one_run(case: dict, run_no: int, commit: str, no_model: bool,
             model_timeout: int, results_dir: Path) -> dict | None:
    """Run one case once. Returns the record, or None when skipped."""
    case_id = case["id"]
    print(f"=== {case_id} run {run_no} ===", flush=True)
    started = time.monotonic()

    try:
        _recreate_lab()
    except HarnessError as err:
        return {"case": case_id, "run": run_no, "commit": commit,
                "harness_error": str(err), "stage": err.stage,
                "run_seconds": round(time.monotonic() - started, 1)}

    record: dict | None = None
    try:
        problems = faults._lab_problems()
        if problems:
            raise HarnessError("baseline-confirm",
                               "baseline not clean: " + "; ".join(problems))

        try:
            faults.inject(case["setup"])
        except Exception as err:   # noqa: BLE001 - setup must never fail silently
            raise HarnessError("setup", f"inject {case['setup']} failed: {err}") from None
        problems = faults._lab_problems()
        if case["type"] == "healthy":
            if problems:
                raise HarnessError("setup-confirm",
                                   "healthy setup not clean: " + "; ".join(problems))
        elif not problems:
            raise HarnessError("setup-confirm",
                               f"{case['setup']} shows no fault")

        try:
            base = baseline_module.run(set(case["unavailable_checks"]))
        except Exception as err:   # noqa: BLE001 - baseline must never fail silently
            raise HarnessError("baseline", f"baseline failed: {err}") from None
        print(f"baseline: {base['cause']} (rule {base['rule']})", flush=True)

        if no_model:
            model = None
            repair, verified = _run_repair_step(None)
        else:
            model_path = results_dir / f"model-{case_id}-{run_no}.json"
            model = _run_model(case, model_path, model_timeout)
            print(f"model: {model['cause']} "
                  f"({model['supported']}/{model['total']} citations,"
                  f" ${model['cost_usd']:.3f})", flush=True)
            repair, verified = _run_repair_step(
                investigate.REPAIR_FOR_CAUSE.get(model["cause"]))

        expected = case["expected_cause"]
        record = {
            "case": case_id, "run": run_no, "commit": commit,
            "model": investigate.MODEL, "reasoning_effort": investigate.EFFORT,
            "check_limit": investigate.MAX_CHECKS,
            "expected_cause": expected,
            "model_cause": model["cause"] if model else None,
            "correct": (model["cause"] == expected) if model else None,
            "false_alarm": (case["type"] == "healthy"
                            and model["cause"] in investigate.FAULTS) if model else None,
            "citations_supported": model["supported"] if model else None,
            "citations_total": model["total"] if model else None,
            "report_warnings": model["warnings"] if model else None,
            "checks_used": model["checks_used"] if model else None,
            "used_all_checks": (model["checks_used"] >= investigate.MAX_CHECKS)
            if model else None,
            "repair_applied": repair,
            "recovery_verified": verified,
            "baseline_cause": base["cause"],
            "baseline_rule": base["rule"],
            "baseline_correct": base["cause"] == expected,
            "seconds": model["seconds"] if model else None,
            "tokens": model["usage"] if model else None,
            "cost_usd": model["cost_usd"] if model else None,
        }
        if no_model:
            record["no_model"] = True
    except HarnessError as err:
        record = {"case": case_id, "run": run_no, "commit": commit,
                  "model": investigate.MODEL,
                  "reasoning_effort": investigate.EFFORT,
                  "check_limit": investigate.MAX_CHECKS,
                  "harness_error": str(err), "stage": err.stage}
        if no_model:
            record["no_model"] = True
    finally:
        _reset_everything()
    record["run_seconds"] = round(time.monotonic() - started, 1)
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the IncidentLab evaluation.")
    parser.add_argument("--cases", default="",
                        help="comma-separated case ids (default: all 15)")
    parser.add_argument("--runs", type=int, default=3,
                        help="runs per case (default: 3)")
    parser.add_argument("--no-model", action="store_true",
                        help="every step except investigate.py (smoke test)")
    parser.add_argument("--results-dir", default="",
                        help="override the results directory")
    parser.add_argument("--model-timeout", type=int, default=1200,
                        help="seconds per model run (default: 1200)")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if not args.no_model:
        key_error = _check_api_key()
        if key_error is not None:
            print(f"error: {key_error}", file=sys.stderr)
            return 2

    with open(CASES_PATH) as f:
        cases = json.load(f)["cases"]
    wanted = {c.strip() for c in args.cases.split(",") if c.strip()}
    unknown = wanted - {c["id"] for c in cases}
    if unknown:
        parser.error(f"unknown cases: {sorted(unknown)}")
    if wanted:
        cases = [c for c in cases if c["id"] in wanted]

    commit = _git_commit()
    stamp = datetime.now().strftime("%Y%m%d")
    if args.results_dir:
        results_dir = Path(args.results_dir)
    elif args.no_model:
        results_dir = RESULTS_ROOT / f"nomodel-{stamp}-{commit}"
    else:
        results_dir = RESULTS_ROOT / f"{stamp}-{commit}"
    results_dir.mkdir(parents=True, exist_ok=True)
    runs_path = results_dir / "runs.jsonl"
    done = _load_recorded(runs_path)
    print(f"results: {results_dir} (commit {commit}, resuming,"
          f" {len(done)} runs already recorded)")

    stop = _ModelErrorStop()
    for case in cases:
        for run_no in range(1, args.runs + 1):
            if (case["id"], run_no) in done:
                print(f"=== {case['id']} run {run_no} === already recorded, skipping")
                continue
            record = _one_run(case, run_no, commit, args.no_model,
                              args.model_timeout, results_dir)
            _append_record(runs_path, record)
            if "harness_error" in record:
                print(f"harness error ({record['stage']}): {record['harness_error']}")
            if stop.note(record):
                print(f"stopping after two model errors in a row; see {runs_path}")
                return 1
    print(f"done: {runs_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
