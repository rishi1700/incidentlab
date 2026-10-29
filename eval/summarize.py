#!/usr/bin/env python3
"""Summarize one evaluation results directory into summary.md.

Reads runs.jsonl (keeping the last record per case and run), then reports
a per-case table (expected cause, model answer per run, baseline answer,
citations supported, recovery verified) plus model and baseline accuracy
by case type, false alarms, citation support, recovery rate, cost and
time. Harness-error and --no-model records are counted separately and
excluded from the metrics. Results are never edited by hand; to change
anything, re-run and summarize again.

Usage:
    python3 eval/summarize.py eval/results/<date>-<commit>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TYPES = {"V": "fault", "P": "fault", "D": "fault", "H": "healthy", "M": "missing"}
CASE_PREFIX_ORDER = {"V": 0, "P": 1, "D": 2, "H": 3, "M": 4}


def case_type(case_id: str) -> str:
    return TYPES.get(case_id[:1], "fault")


def case_order(case_id: str) -> tuple[int, int]:
    try:
        return (CASE_PREFIX_ORDER[case_id[:1]], int(case_id[1:]))
    except (KeyError, ValueError):
        return (len(CASE_PREFIX_ORDER), 0)


def load_records(results_dir: Path) -> list[dict]:
    runs_path = results_dir / "runs.jsonl"
    latest: dict[tuple[str, int], dict] = {}
    with open(runs_path) as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            latest[(record["case"], record["run"])] = record
    return list(latest.values())


def pct(numerator: int, denominator: int) -> str:
    if not denominator:
        return "n/a"
    return f"{100 * numerator / denominator:.0f}%"


def accuracy_table(records: list[dict], key: str) -> list[str]:
    lines = ["| Type | Correct | Total | Accuracy |",
             "|---|---|---|---|"]
    for ctype in ("fault", "healthy", "missing"):
        group = [r for r in records if case_type(r["case"]) == ctype]
        right = sum(1 for r in group if r[key])
        lines.append(f"| {ctype} | {right} | {len(group)} | {pct(right, len(group))} |")
    right = sum(1 for r in records if r[key])
    lines.append(f"| all | {right} | {len(records)} | {pct(right, len(records))} |")
    return lines


def per_case_table(records: list[dict]) -> list[str]:
    runs = sorted({r["run"] for r in records})
    cases = sorted({r["case"] for r in records}, key=case_order)
    by_case_run = {(r["case"], r["run"]): r for r in records}
    header = ["Case", "Type", "Expected"]
    header += [f"Model run {n}" for n in runs]
    header += ["Baseline", "Citations", "Recovery"]
    lines = ["| " + " | ".join(header) + " |"]
    lines.append("|" + "---|" * len(header))
    for case in cases:
        by_run = [by_case_run.get((case, n)) for n in runs]
        expected = next((r.get("expected_cause", "?") for r in by_run if r), "?")
        cells: list[str] = []
        for r in by_run:
            if r is None:
                cells.append("missing")
            elif "harness_error" in r:
                cells.append("error")
            elif r.get("no_model"):
                cells.append("skipped")
            else:
                cells.append(str(r.get("model_cause", "?")))
        base = sorted({str(r.get("baseline_cause", "?")) for r in by_run if r
                       and "harness_error" not in r and not r.get("no_model")})
        baseline = "; ".join(base) if base else "n/a"
        cited = [(r.get("citations_supported"), r.get("citations_total"))
                 for r in by_run if r and "harness_error" not in r
                 and not r.get("no_model")]
        if cited and all(s is not None and t is not None for s, t in cited):
            citations = f"{sum(s for s, _ in cited)}/{sum(t for _, t in cited)}"
        else:
            citations = "n/a"
        settled = [r for r in by_run if r and "recovery_verified" in r]
        if settled:
            recovery = f"{sum(1 for r in settled if r['recovery_verified'])}/{len(settled)}"
        else:
            recovery = "n/a"
        lines.append("| " + " | ".join(
            [case, case_type(case), str(expected)] + cells
            + [baseline, citations, recovery]) + " |")
    return lines


def summarize(results_dir: Path) -> str:
    records = load_records(results_dir)
    errors = [r for r in records if "harness_error" in r]
    skipped = [r for r in records if r.get("no_model")]
    usable = [r for r in records if r not in errors and r not in skipped]

    out = [f"# Summary of {results_dir.name}", ""]
    commits = sorted({r.get("commit", "?") for r in records})
    out.append(f"Runs recorded: {len(records)}"
               f" ({len(usable)} usable, {len(errors)} harness errors,"
               f" {len(skipped)} --no-model records skipped).")
    out.append(f"Commits: {', '.join(commits)}.")
    models = sorted({str(r.get('model')) for r in usable})
    efforts = sorted({str(r.get('reasoning_effort')) for r in usable})
    limits = sorted({str(r.get('check_limit')) for r in usable})
    if usable:
        out.append(f"Model: {', '.join(models)};"
                   f" reasoning effort: {', '.join(efforts)};"
                   f" check limit: {', '.join(limits)}.")
    out.append("")

    out.append("## Per-case results")
    out.append("")
    out.extend(per_case_table(records))
    out.append("")

    out.append("## Model accuracy")
    out.append("")
    out.extend(accuracy_table(usable, "correct"))
    out.append("")
    out.append("## Baseline accuracy")
    out.append("")
    out.extend(accuracy_table(usable, "baseline_correct"))
    out.append("")

    alarms = [r for r in usable if r.get("false_alarm")]
    out.append("## False alarms")
    out.append("")
    if alarms:
        out.append(f"{len(alarms)} runs named a fault on a healthy case: "
                   + ", ".join(f"{r['case']} run {r['run']} ({r['model_cause']})"
                               for r in alarms) + ".")
    else:
        out.append("None.")
    out.append("")

    supported = sum(r["citations_supported"] or 0 for r in usable)
    total = sum(r["citations_total"] or 0 for r in usable)
    warned = sum(1 for r in usable if r.get("report_warnings"))
    used_all = sum(1 for r in usable if r.get("used_all_checks"))
    out.append("## Citations")
    out.append("")
    out.append(f"{supported}/{total} citations supported ({pct(supported, total)})."
               f" {warned} runs with report warnings."
               f" {used_all} runs used all checks.")
    out.append("")

    applied = [r for r in usable if r.get("repair_applied")]
    verified = sum(1 for r in applied if r.get("recovery_verified"))
    unrepaired = [r for r in usable if not r.get("repair_applied")]
    end_ok = sum(1 for r in unrepaired if r.get("recovery_verified"))
    out.append("## Recovery")
    out.append("")
    out.append(f"Repair applied on {len(applied)} runs,"
               f" recovery verified on {verified} ({pct(verified, len(applied))}).")
    out.append(f"No repair on {len(unrepaired)} runs,"
               f" end-state verifier passed on {end_ok}.")
    out.append("")

    cost = sum(r["cost_usd"] or 0 for r in usable)
    seconds = sum(r["seconds"] or 0 for r in usable)
    tokens_in = sum((r["tokens"] or {}).get("input", 0) for r in usable)
    tokens_out = sum((r["tokens"] or {}).get("output", 0) for r in usable)
    out.append("## Cost and time")
    out.append("")
    out.append(f"Total ${cost:.3f} and {seconds:.0f}s of model time"
               f" over {len(usable)} runs"
               f" ({tokens_in} input / {tokens_out} output tokens).")
    out.append("")

    if errors:
        out.append("## Harness errors")
        out.append("")
        for r in errors:
            out.append(f"- {r['case']} run {r['run']}"
                       f" ({r.get('stage', '?')}): {r['harness_error']}")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize an evaluation results directory.")
    parser.add_argument("results_dir", help="directory holding runs.jsonl")
    args = parser.parse_args(argv)
    results_dir = Path(args.results_dir)
    if not (results_dir / "runs.jsonl").exists():
        parser.error(f"no runs.jsonl in {results_dir}")
    text = summarize(results_dir)
    with open(results_dir / "summary.md", "w") as f:
        f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
