#!/usr/bin/env python3
"""Fault injection for IncidentLab milestones 1-2.

Ground truth lives here and in the eval runner's records: nothing this module
passes to docker (helper container names, labels, env) or writes into the lab
data dir names the fault. Helpers run as anonymous `--rm` containers.

The volume_full files look like orphaned partial uploads: random hex names in
the app's own `<id>.part` shape, owned by the app user, dated two days ago.

Usage:
    python3 lab/faults.py inject upload_dir_not_writable
    python3 lab/faults.py inject volume_full
    python3 lab/faults.py inject database_unavailable
    python3 lab/faults.py reset database_unavailable
    python3 lab/faults.py status [fault]
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone

DATA_MOUNT = os.environ.get("INCIDENTLAB_DATA_MOUNT", "/srv/lab-data")
UPLOADS = "uploads"
TMP = "tmp"
APP_UID = int(os.environ.get("INCIDENTLAB_APP_UID", "1000"))
APP_GID = int(os.environ.get("INCIDENTLAB_APP_GID", "1000"))
DB = os.environ.get("INCIDENTLAB_DB", "incidentlab-db-1")

FAULT_UPLOAD_DIR = "upload_dir_not_writable"
FAULT_VOLUME_FULL = "volume_full"
FAULT_DATABASE = "database_unavailable"
FAULTS = [FAULT_UPLOAD_DIR, FAULT_VOLUME_FULL, FAULT_DATABASE]

CHUNK_MB = 16          # injected pieces are this big until space runs low
INJECT_AGE_SECONDS = 2 * 24 * 3600   # injected orphans look two days old
MAX_ORPHANS = 100      # plenty for the 512 MB lab; guards against runaway loops
STALE_SECONDS = 3600   # shown in status only; repair.py owns the repair rule
DB_READY_TIMEOUT = 90  # seconds to wait for the database to accept connections
DB_POLL_SECONDS = 2


def _docker(*argv: str, user: str | None = None) -> str:
    """Run a helper as an anonymous --rm container (no name/labels/env)."""
    cmd = ["docker", "run", "--rm"]
    if user is not None:
        cmd += ["--user", user]
    cmd += ["-v", f"{DATA_MOUNT}:/d", "alpine", *argv]
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
    """Run a docker control command on the host (stop, start, unpause ...)."""
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


def _db_healthy(data: dict) -> bool:
    state = data.get("State") or {}
    if not state.get("Running"):
        return False
    health = (state.get("Health") or {}).get("Status")
    return health in (None, "healthy")   # no healthcheck: running is ready


def _wait_db_healthy() -> None:
    deadline = time.monotonic() + DB_READY_TIMEOUT
    while True:
        if _db_healthy(_inspect_container(DB)):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"{DB} did not become healthy within {DB_READY_TIMEOUT}s")
        time.sleep(DB_POLL_SECONDS)


def _reset_database() -> None:
    state = (_inspect_container(DB).get("State") or {})
    if state.get("Paused"):
        _docker_cmd("unpause", DB)
    if not state.get("Running"):
        _docker_cmd("start", DB)
    _wait_db_healthy()


def _available_bytes() -> int:
    s = os.statvfs(DATA_MOUNT)
    return s.f_bavail * s.f_frsize           # what a non-root process can still use


def _two_days_ago_stamp() -> str:
    # POSIX touch -t format [[CC]YY]MMDDhhmm[.ss]; the helper runs on UTC.
    moment = datetime.fromtimestamp(time.time() - INJECT_AGE_SECONDS, timezone.utc)
    return moment.strftime("%Y%m%d%H%M.%S")


def _inject_volume_full() -> None:
    s = os.statvfs(DATA_MOUNT)
    size_mb = s.f_blocks * s.f_frsize / (1024 * 1024)
    if size_mb > 1024:
        raise RuntimeError(
            f"refusing to fill {DATA_MOUNT} ({size_mb:.0f} MB):"
            " expected the 512 MB lab filesystem")
    app_user = f"{APP_UID}:{APP_GID}"
    if not os.path.isdir(os.path.join(DATA_MOUNT, TMP)):
        _docker("mkdir", "-p", f"/d/{TMP}", user=app_user)
    stamp = _two_days_ago_stamp()
    created = 0
    while True:
        avail_mb = _available_bytes() // (1024 * 1024)
        if avail_mb < 1:
            break
        if created >= MAX_ORPHANS:
            raise RuntimeError(
                f"volume still not full after {created} files;"
                f" is {DATA_MOUNT} the 512 MB lab filesystem?")
        name = secrets.token_hex(16) + ".part"   # the app's own <id>.part shape
        target = f"/d/{TMP}/{name}"
        try:
            _docker("dd", "if=/dev/urandom", f"of={target}", "bs=1M",
                    f"count={min(CHUNK_MB, avail_mb)}", user=app_user)
        except RuntimeError as err:
            if "no space left on device" not in str(err).lower():
                raise
            full = True
        else:
            full = False
        _docker("touch", "-t", stamp, target, user=app_user)
        created += 1
        if full:
            break


def _tmp_part_files() -> list[str]:
    """Basenames of regular .part files in tmp/ (never directories)."""
    host_tmp = os.path.join(DATA_MOUNT, TMP)
    try:
        entries = os.listdir(host_tmp)
    except FileNotFoundError:
        return []
    return sorted(n for n in entries
                  if n.endswith(".part") and os.path.isfile(os.path.join(host_tmp, n)))


def _reset_volume_full() -> None:
    names = _tmp_part_files()
    if names:
        _docker("rm", "-f", *(f"/d/{TMP}/{name}" for name in names))


def inject(fault: str) -> None:
    if fault == FAULT_UPLOAD_DIR:
        _docker("chown", "0:0", f"/d/{UPLOADS}")
        _docker("chmod", "755", f"/d/{UPLOADS}")
    elif fault == FAULT_VOLUME_FULL:
        _inject_volume_full()
    elif fault == FAULT_DATABASE:
        _docker_cmd("stop", DB)
    else:
        raise ValueError(f"unknown fault: {fault}")
    print(f"{fault}: injected")


def reset(fault: str) -> None:
    if fault == FAULT_UPLOAD_DIR:
        _docker("chown", f"{APP_UID}:{APP_GID}", f"/d/{UPLOADS}")
        _docker("chmod", "755", f"/d/{UPLOADS}")
    elif fault == FAULT_VOLUME_FULL:
        _reset_volume_full()
    elif fault == FAULT_DATABASE:
        _reset_database()
    else:
        raise ValueError(f"unknown fault: {fault}")
    print(f"{fault}: reset")


def status_uploads() -> dict:
    path = os.path.join(DATA_MOUNT, UPLOADS)
    st = os.stat(path)
    mode = stat.S_IMODE(st.st_mode)
    if st.st_uid == APP_UID:
        writable = bool(mode & 0o200)
    elif st.st_gid == APP_GID:
        writable = bool(mode & 0o020)
    else:
        writable = bool(mode & 0o002)
    return {
        "path": path,
        "owner_uid": st.st_uid,
        "owner_gid": st.st_gid,
        "mode": f"{mode:04o}",
        "app_user_can_write": writable,
    }


def status_volume() -> dict:
    s = os.statvfs(DATA_MOUNT)
    used = (s.f_blocks - s.f_bfree) * s.f_frsize
    available = s.f_bavail * s.f_frsize
    mib = 2 ** 20
    names = _tmp_part_files()
    part_bytes = 0
    stale = 0
    now = time.time()
    for name in names:
        try:
            st = os.stat(os.path.join(DATA_MOUNT, TMP, name))
        except OSError:
            continue
        part_bytes += st.st_size
        if now - st.st_mtime > STALE_SECONDS:
            stale += 1
    return {
        "path": DATA_MOUNT,
        "size_mb": round(s.f_blocks * s.f_frsize / mib, 1),
        "used_mb": round(used / mib, 1),
        "available_mb": round(available / mib, 1),
        "use_percent": round(100 * used / (used + available), 1) if used + available else 0.0,
        "tmp_part_files": len(names),
        "tmp_part_mb": round(part_bytes / mib, 1),
        "tmp_part_stale_files": stale,
    }


def status_database() -> dict:
    data = _inspect_container(DB)
    state = data.get("State") or {}
    networks = (data.get("NetworkSettings") or {}).get("Networks") or {}
    return {
        "name": DB,
        "status": state.get("Status"),
        "running": state.get("Running"),
        "paused": bool(state.get("Paused")),
        "health": (state.get("Health") or {}).get("Status") or "no healthcheck",
        "started_at": state.get("StartedAt"),
        "networks": sorted(networks),
    }


def status(fault: str | None = None) -> dict:
    if fault is None:
        report = {FAULT_UPLOAD_DIR: status_uploads(),
                  FAULT_VOLUME_FULL: status_volume(),
                  FAULT_DATABASE: status_database()}
    elif fault == FAULT_UPLOAD_DIR:
        report = status_uploads()
    elif fault == FAULT_VOLUME_FULL:
        report = status_volume()
    elif fault == FAULT_DATABASE:
        report = status_database()
    else:
        raise ValueError(f"unknown fault: {fault}")
    print(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inject, reset or report the lab faults.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_inject = sub.add_parser("inject")
    p_inject.add_argument("fault")
    p_reset = sub.add_parser("reset")
    p_reset.add_argument("fault")
    p_status = sub.add_parser("status")
    p_status.add_argument("fault", nargs="?")
    args = parser.parse_args(argv)
    try:
        if args.command == "inject":
            inject(args.fault)
        elif args.command == "reset":
            reset(args.fault)
        else:
            status(args.fault)
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except (RuntimeError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
