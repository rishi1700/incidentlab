#!/usr/bin/env python3
"""Predefined repairs and verifier for IncidentLab milestones 1-2.

restore_upload_dir_access, clear_stale_temp_files and restore_database
plus the upload/read-back verifier. This module never learns which fault was
injected: it must not import or call lab/faults.py. Repairs run through
docker: anonymous helper containers for files, control commands for the db.

Usage:
    python3 repair.py restore_upload_dir_access   # asks, applies, verifies
    python3 repair.py clear_stale_temp_files --yes   # apply without asking, then verify
    python3 repair.py restore_database --yes      # apply without asking, then verify
    python3 repair.py verify                      # verifier only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
import subprocess
import sys
import time

DATA_MOUNT = os.environ.get("INCIDENTLAB_DATA_MOUNT", "/srv/lab-data")
UPLOADS = "uploads"
TMP = "tmp"
APP_UID = int(os.environ.get("INCIDENTLAB_APP_UID", "1000"))
APP_GID = int(os.environ.get("INCIDENTLAB_APP_GID", "1000"))
APP = os.environ.get("INCIDENTLAB_APP", "incidentlab-app-1")
DB = os.environ.get("INCIDENTLAB_DB", "incidentlab-db-1")

STALE_SECONDS = 3600   # a .part file older than one hour is a stale orphan
DB_READY_TIMEOUT = 90  # seconds to wait for the database to accept connections
DB_POLL_SECONDS = 2

REPAIR_NAME = "restore_upload_dir_access"
REPAIR_TMP = "clear_stale_temp_files"
REPAIR_DB = "restore_database"


def _docker(*argv: str) -> str:
    """Run a helper as an anonymous --rm container (no name/labels/env)."""
    cmd = ["docker", "run", "--rm", "-v", f"{DATA_MOUNT}:/d", "alpine", *argv]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=60,
        )
    except FileNotFoundError:
        raise RuntimeError("command not found: docker") from None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise RuntimeError(f"docker helper failed (exit {proc.returncode}): {detail}")
    return proc.stdout.strip()


def _docker_cmd(*argv: str, timeout: int = 60) -> str:
    """Run a docker control command on the host (start, unpause, network ...)."""
    cmd = ["docker", *argv]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError("command not found: docker") from None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise RuntimeError(f"docker failed (exit {proc.returncode}): {detail}")
    return proc.stdout.strip()


def _inspect_container(name: str) -> dict:
    return json.loads(_docker_cmd("inspect", "--type", "container", name))[0]


def _db_ready(data: dict) -> bool:
    state = data.get("State") or {}
    if not state.get("Running"):
        return False
    health = (state.get("Health") or {}).get("Status")
    return health in (None, "healthy")   # no healthcheck: running is ready


def restore_upload_dir_access() -> dict:
    """Owner 1000:1000, mode 755 on uploads/. Touches only that directory."""
    try:
        _docker("chown", f"{APP_UID}:{APP_GID}", f"/d/{UPLOADS}")
        _docker("chmod", "755", f"/d/{UPLOADS}")
    except RuntimeError as err:
        return {"repair": REPAIR_NAME, "applied": True, "ok": False, "detail": str(err)}
    return {"repair": REPAIR_NAME, "applied": True, "ok": True,
            "detail": f"{UPLOADS}/ owner {APP_UID}:{APP_GID} mode 0755"}


def clear_stale_temp_files() -> dict:
    """Delete .part files older than one hour in tmp/. Touches only those files."""
    host_tmp = os.path.join(DATA_MOUNT, TMP)
    try:
        entries = os.listdir(host_tmp)
    except FileNotFoundError:
        return {"repair": REPAIR_TMP, "applied": True, "ok": True,
                "detail": f"{TMP}/ not present, nothing to delete"}
    except OSError as err:
        return {"repair": REPAIR_TMP, "applied": True, "ok": False,
                "detail": f"cannot list {TMP}/: {err}"}
    now = time.time()
    stale: list[str] = []
    freed = 0
    for entry in sorted(entries):
        if not entry.endswith(".part"):
            continue
        try:
            st = os.stat(os.path.join(host_tmp, entry))
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        if now - st.st_mtime <= STALE_SECONDS:
            continue
        stale.append(entry)
        freed += st.st_size
    if not stale:
        return {"repair": REPAIR_TMP, "applied": True, "ok": True,
                "detail": f"no stale .part files in {TMP}/, nothing to delete"}
    try:
        _docker("rm", "-f", *(f"/d/{TMP}/{name}" for name in stale))
    except RuntimeError as err:
        return {"repair": REPAIR_TMP, "applied": True, "ok": False, "detail": str(err)}
    return {"repair": REPAIR_TMP, "applied": True, "ok": True,
            "detail": f"deleted {len(stale)} stale .part file(s), freed {freed / 2 ** 20:.1f} MB"}


def restore_database() -> dict:
    """Start, unpause or reconnect the db as needed, then wait until ready.

    Touches only the db container. The app needs no restart: it opens a
    fresh database connection per request.
    """
    actions: list[str] = []
    try:
        state = (_inspect_container(DB).get("State") or {})
        if state.get("Paused"):
            _docker_cmd("unpause", DB)
            actions.append("unpaused")
        if not state.get("Running"):
            _docker_cmd("start", DB)
            actions.append("started")
        app_networks = set(((_inspect_container(APP).get("NetworkSettings") or {})
                            .get("Networks") or {}))
        db_networks = set(((_inspect_container(DB).get("NetworkSettings") or {})
                           .get("Networks") or {}))
        for network in sorted(app_networks - db_networks):
            _docker_cmd("network", "connect", network, DB)
            actions.append(f"connected to {network}")
        deadline = time.monotonic() + DB_READY_TIMEOUT
        while True:
            if _db_ready(_inspect_container(DB)):
                break
            if time.monotonic() >= deadline:
                return {"repair": REPAIR_DB, "applied": True, "ok": False,
                        "detail": f"{DB} did not become healthy within {DB_READY_TIMEOUT}s"}
            time.sleep(DB_POLL_SECONDS)
    except RuntimeError as err:
        return {"repair": REPAIR_DB, "applied": True, "ok": False, "detail": str(err)}
    if actions:
        detail = f"{DB}: {', '.join(actions)}; healthy"
    else:
        detail = f"{DB} already running and healthy"
    return {"repair": REPAIR_DB, "applied": True, "ok": True, "detail": detail}


REPAIRS = {REPAIR_NAME: restore_upload_dir_access, REPAIR_TMP: clear_stale_temp_files,
           REPAIR_DB: restore_database}


def ask_approval(repair_name: str, cause: str | None = None) -> bool:
    """True only on explicit yes. Empty input, no and EOF decline."""
    if cause:
        prompt = f"Apply repair '{repair_name}' for diagnosis '{cause}'? [y/N]: "
    else:
        prompt = f"Apply repair '{repair_name}'? [y/N]: "
    try:
        answer = input(prompt)
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def verify_recovery() -> dict:
    """Upload a new random 1 MB file, download it, compare SHA-256 hashes."""
    from lab.verify import SIZE, get_file, post_upload

    payload = secrets.token_bytes(SIZE)
    sent_hash = hashlib.sha256(payload).hexdigest()
    try:
        result = post_upload(payload)
    except Exception as err:
        return {"ok": False, "detail": f"UPLOAD FAILED: {type(err).__name__}: {err}"}
    file_id, returned_hash = result.get("id"), result.get("sha256")
    if returned_hash != sent_hash:
        return {"ok": False, "detail": f"MISMATCH: server hash {returned_hash} != local hash {sent_hash}",
                "id": file_id}
    try:
        downloaded = get_file(file_id)
    except Exception as err:
        return {"ok": False, "detail": f"DOWNLOAD FAILED: {type(err).__name__}: {err}", "id": file_id}
    downloaded_hash = hashlib.sha256(downloaded).hexdigest()
    if downloaded_hash != sent_hash:
        return {"ok": False, "detail": "MISMATCH: downloaded hash != uploaded hash", "id": file_id}
    return {"ok": True, "detail": "upload/download SHA-256 hashes match",
            "id": file_id, "sha256": sent_hash, "bytes": len(payload)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply one predefined repair and verify recovery.")
    parser.add_argument("repair", choices=[REPAIR_NAME, REPAIR_TMP, REPAIR_DB, "verify"],
                        help="repair to apply, or 'verify' to run the verifier only")
    parser.add_argument("--yes", action="store_true", help="apply without asking (testing and eval runs)")
    parser.add_argument("--no-verify", action="store_true", help="skip the verifier after the repair")
    args = parser.parse_args(argv)

    if args.repair == "verify":
        result = verify_recovery()
        print(f"Verification: {'OK' if result['ok'] else 'FAILED'}: {result['detail']}")
        return 0 if result["ok"] else 1

    if not args.yes and not ask_approval(args.repair):
        print(f"Repair {args.repair} declined; nothing changed.")
        return 2
    result = REPAIRS[args.repair]()
    print(f"Repair {args.repair}: {'OK' if result['ok'] else 'FAILED'}: {result['detail']}")
    if not result["ok"]:
        return 1
    if args.no_verify:
        return 0
    verification = verify_recovery()
    print(f"Verification: {'OK' if verification['ok'] else 'FAILED'}: {verification['detail']}")
    return 0 if verification["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
