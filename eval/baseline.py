#!/usr/bin/env python3
"""Rule-based baseline for the IncidentLab evaluation (eval/PLAN.md).

Runs the same check functions as investigate.py (imported as run_check) in
a fixed order and returns the first match. Read-only. A rule whose check is
unavailable is skipped.

Rule order (fixed before running, per PLAN decision 3):
  1. db in a state other than running -> database_unavailable;
     app not running -> insufficient_evidence
  2. disk_usage on the data mount at 95% bytes or inodes -> volume_full
  3. path_access on uploads/ not writable -> upload_dir_not_writable
  4. /health returns 200 -> healthy
  5. last 100 app log lines: "No space left" -> volume_full,
     "Permission denied" -> upload_dir_not_writable,
     database connection or name-resolution error -> database_unavailable
  6. otherwise -> insufficient_evidence

Usage:
    python3 eval/baseline.py [--unavailable path_access,container_logs]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import investigate
from investigate import CheckError, run_check

FULL_PERCENT = 95
LOG_PATTERNS = [
    ("volume_full", ("no space left",)),
    ("upload_dir_not_writable", ("permission denied",)),
    ("database_unavailable", (
        "connection failed", "connection refused", "could not translate",
        "name or service not known", "nodename nor servname", "operationalerror",
    )),
]


def _check(name: str, arguments: dict):
    """Run one check, or return None when it is unavailable or fails."""
    try:
        return run_check(name, json.dumps(arguments))
    except CheckError:
        return None


def run(unavailable: set[str] | None = None) -> dict:
    """Return {"cause", "rule", "detail"} for the current lab state."""
    investigate.UNAVAILABLE = set(unavailable or ())

    containers = _check("list_containers", {})
    if containers is not None:
        by_name = {row.get("name"): row for row in containers.get("containers", [])}
        db_state = (by_name.get(investigate.LAB["db"]) or {}).get("state")
        if db_state != "running":
            return {"cause": "database_unavailable", "rule": 1,
                    "detail": f"db state is {db_state}"}
        app_state = (by_name.get(investigate.LAB["app"]) or {}).get("state")
        if app_state != "running":
            return {"cause": "insufficient_evidence", "rule": 1,
                    "detail": f"app state is {app_state}"}

    disk = _check("disk_usage", {"path": investigate.LAB["data_mount"]})
    if disk is not None and (disk.get("use_percent", 0) >= FULL_PERCENT
                             or disk.get("inode_use_percent", 0) >= FULL_PERCENT):
        return {"cause": "volume_full", "rule": 2,
                "detail": f"use {disk.get('use_percent')}% bytes,"
                          f" {disk.get('inode_use_percent')}% inodes"}

    access = _check("path_access", {"path": investigate.LAB["upload_dir"]})
    if access is not None and not access.get("app_user_can_write", True):
        return {"cause": "upload_dir_not_writable", "rule": 3,
                "detail": f"owner {access.get('owner_uid')}:{access.get('owner_gid')},"
                          f" mode {access.get('mode')}"}

    health = _check("app_health", {})
    if health is not None and health.get("status_code") == 200:
        return {"cause": "healthy", "rule": 4, "detail": "/health is 200"}

    logs = _check("container_logs", {"name": investigate.LAB["app"], "tail": 100})
    if logs is not None:
        text = "\n".join(logs.get("lines", [])).lower()
        for cause, patterns in LOG_PATTERNS:
            for pattern in patterns:
                if pattern in text:
                    return {"cause": cause, "rule": 5,
                            "detail": f"log contains {pattern!r}"}

    return {"cause": "insufficient_evidence", "rule": 6,
            "detail": "no rule matched"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rule-based baseline diagnosis.")
    parser.add_argument("--unavailable", default="",
                        help="comma-separated checks to treat as unavailable")
    args = parser.parse_args(argv)
    unavailable = {c.strip() for c in args.unavailable.split(",") if c.strip()}
    print(json.dumps(run(unavailable)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
