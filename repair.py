#!/usr/bin/env python3
"""Predefined repairs and verifier for IncidentLab milestone 1.

Step 3 only: restore_upload_dir_access plus the upload/read-back verifier.
This module never learns which fault was injected: it must not import or
call lab/faults.py. Repairs run through anonymous docker helper containers.

Usage:
    python3 repair.py restore_upload_dir_access   # asks, applies, verifies
    python3 repair.py restore_upload_dir_access --yes --no-verify  # apply only
    python3 repair.py verify                      # verifier only
"""
from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import subprocess
import sys

DATA_MOUNT = os.environ.get("INCIDENTLAB_DATA_MOUNT", "/srv/lab-data")
UPLOADS = "uploads"
APP_UID = int(os.environ.get("INCIDENTLAB_APP_UID", "1000"))
APP_GID = int(os.environ.get("INCIDENTLAB_APP_GID", "1000"))

REPAIR_NAME = "restore_upload_dir_access"


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


def restore_upload_dir_access() -> dict:
    """Owner 1000:1000, mode 755 on uploads/. Touches only that directory."""
    try:
        _docker("chown", f"{APP_UID}:{APP_GID}", f"/d/{UPLOADS}")
        _docker("chmod", "755", f"/d/{UPLOADS}")
    except RuntimeError as err:
        return {"repair": REPAIR_NAME, "applied": True, "ok": False, "detail": str(err)}
    return {"repair": REPAIR_NAME, "applied": True, "ok": True,
            "detail": f"{UPLOADS}/ owner {APP_UID}:{APP_GID} mode 0755"}


REPAIRS = {REPAIR_NAME: restore_upload_dir_access}


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
    parser.add_argument("repair", choices=[REPAIR_NAME, "verify"],
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
