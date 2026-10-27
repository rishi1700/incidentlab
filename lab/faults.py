#!/usr/bin/env python3
"""Fault injection for IncidentLab milestone 1 (upload_dir_not_writable only).

Ground truth lives here and in the eval runner's records: nothing this module
passes to docker (helper container names, labels, env) or writes into the lab
data dir names the fault. Helpers run as anonymous `--rm` containers.

Usage:
    python3 lab/faults.py inject upload_dir_not_writable
    python3 lab/faults.py reset upload_dir_not_writable
    python3 lab/faults.py status
"""
from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys

DATA_MOUNT = os.environ.get("INCIDENTLAB_DATA_MOUNT", "/srv/lab-data")
UPLOADS = "uploads"
APP_UID = int(os.environ.get("INCIDENTLAB_APP_UID", "1000"))
APP_GID = int(os.environ.get("INCIDENTLAB_APP_GID", "1000"))

FAULT = "upload_dir_not_writable"


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


def inject(fault: str) -> None:
    if fault != FAULT:
        raise ValueError(f"unknown fault: {fault}")
    _docker("chown", "0:0", f"/d/{UPLOADS}")
    _docker("chmod", "755", f"/d/{UPLOADS}")
    print(f"{fault}: injected")


def reset(fault: str) -> None:
    if fault != FAULT:
        raise ValueError(f"unknown fault: {fault}")
    _docker("chown", f"{APP_UID}:{APP_GID}", f"/d/{UPLOADS}")
    _docker("chmod", "755", f"/d/{UPLOADS}")
    print(f"{fault}: reset")


def status() -> dict:
    path = os.path.join(DATA_MOUNT, UPLOADS)
    st = os.stat(path)
    mode = stat.S_IMODE(st.st_mode)
    if st.st_uid == APP_UID:
        writable = bool(mode & 0o200)
    elif st.st_gid == APP_GID:
        writable = bool(mode & 0o020)
    else:
        writable = bool(mode & 0o002)
    report = {
        "path": path,
        "owner_uid": st.st_uid,
        "owner_gid": st.st_gid,
        "mode": f"{mode:04o}",
        "app_user_can_write": writable,
    }
    print(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inject, reset or report the lab fault.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_inject = sub.add_parser("inject")
    p_inject.add_argument("fault")
    p_reset = sub.add_parser("reset")
    p_reset.add_argument("fault")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    try:
        if args.command == "inject":
            inject(args.fault)
        elif args.command == "reset":
            reset(args.fault)
        else:
            status()
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except (RuntimeError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
