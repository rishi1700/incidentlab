#!/usr/bin/env python3
"""Fault injection for IncidentLab milestones 1-3.

Ground truth lives here and in the eval runner's records: nothing this module
passes to docker (helper container names, labels, env) or writes into the lab
data dir names the fault. Helpers run as anonymous `--rm` containers.

Cases V1-V3 (volume full), P1-P3 (upload dir not writable), D1-D3 (database
unavailable) and healthy setups H1-H3. The old fault names (volume_full,
upload_dir_not_writable, database_unavailable) still work as aliases for
V1, P1 and D1. Each fill runs in a single helper container.

The V1, V2 and V3 files look like orphaned partial uploads: random hex names
in the app's own `<id>.part` shape, owned by the app user, dated two days ago.

inject refuses unless the lab is healthy first: status must show no fault
and the upload/read-back verifier must pass. It changes nothing then.

Usage:
    python3 lab/faults.py inject V2
    python3 lab/faults.py reset V2
    python3 lab/faults.py status [variant]
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA_MOUNT = os.environ.get("INCIDENTLAB_DATA_MOUNT", "/srv/lab-data")
UPLOADS = "uploads"
TMP = "tmp"
APP_UID = int(os.environ.get("INCIDENTLAB_APP_UID", "1000"))
APP_GID = int(os.environ.get("INCIDENTLAB_APP_GID", "1000"))
DB = os.environ.get("INCIDENTLAB_DB", "incidentlab-db-1")
APP = os.environ.get("INCIDENTLAB_APP", "incidentlab-app-1")
DB_ALIAS = os.environ.get("INCIDENTLAB_DB_ALIAS", "db")
APP_URL = os.environ.get("INCIDENTLAB_APP_URL", "http://localhost:8080")

FAULT_UPLOAD_DIR = "upload_dir_not_writable"
FAULT_VOLUME_FULL = "volume_full"
FAULT_DATABASE = "database_unavailable"
V_VARIANTS = ("V1", "V2", "V3", FAULT_VOLUME_FULL)
P_VARIANTS = ("P1", "P2", "P3", FAULT_UPLOAD_DIR)
D_VARIANTS = ("D1", "D2", "D3", FAULT_DATABASE)
H_VARIANTS = ("H1", "H2", "H3")
VARIANTS = [*V_VARIANTS, *P_VARIANTS, *D_VARIANTS, *H_VARIANTS]

CHUNK_MB = 16          # injected pieces are this big until space runs low
INJECT_AGE_SECONDS = 2 * 24 * 3600   # injected orphans look two days old
MAX_ORPHANS = 100      # plenty for the 512 MB lab; guards against runaway loops
STALE_SECONDS = 3600   # shown in status only; repair.py owns the repair rule
V3_TARGET_FREE_KB = 450   # V3 leaves about this much for the app user (PLAN: 300-600)
V3_FREE_MIN_KB = 200   # guard rails: /health still passes, 1 MB uploads still fail
V3_FREE_MAX_KB = 700
DB_READY_TIMEOUT = 90  # seconds to wait for the database to accept connections
DB_POLL_SECONDS = 2
HEALTH_TIMEOUT = 60    # seconds to wait for /health to return 200 after reset
HEALTH_POLL_SECONDS = 2

# 32 random hex chars from the shell alone ($RANDOM works in the helper).
_HEX32 = ("$(printf '%04x%04x%04x%04x%04x%04x%04x%04x'"
          " $RANDOM $RANDOM $RANDOM $RANDOM $RANDOM $RANDOM $RANDOM $RANDOM)")


def _docker(*argv: str, user: str | None = None, timeout: int = 60) -> str:
    """Run a helper as an anonymous --rm container (no name/labels/env)."""
    cmd = ["docker", "run", "--rm"]
    if user is not None:
        cmd += ["--user", user]
    cmd += ["-v", f"{DATA_MOUNT}:/d", "alpine", *argv]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError("command not found: docker") from None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise RuntimeError(f"docker helper failed (exit {proc.returncode}): {detail}")
    return proc.stdout.strip()


def _docker_cmd(*argv: str, timeout: int = 60, merge_stderr: bool = False) -> str:
    """Run a docker control command on the host (stop, start, unpause ...)."""
    cmd = ["docker", *argv]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
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


def _container_networks(name: str) -> set[str]:
    data = _inspect_container(name)
    return set(((data.get("NetworkSettings") or {}).get("Networks") or {}))


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
    for network in sorted(_container_networks(APP) - _container_networks(DB)):
        _docker_cmd("network", "connect", "--alias", DB_ALIAS, network, DB)
    _wait_db_healthy()


def _available_bytes() -> int:
    s = os.statvfs(DATA_MOUNT)
    return s.f_bavail * s.f_frsize           # what a non-root process can still use


def _two_days_ago_stamp() -> str:
    # POSIX touch -t format [[CC]YY]MMDDhhmm[.ss]; the helper runs on UTC.
    moment = datetime.fromtimestamp(time.time() - INJECT_AGE_SECONDS, timezone.utc)
    return moment.strftime("%Y%m%d%H%M.%S")


def _check_lab_fs() -> None:
    s = os.statvfs(DATA_MOUNT)
    size_mb = s.f_blocks * s.f_frsize / (1024 * 1024)
    if size_mb > 1024:
        raise RuntimeError(
            f"refusing to fill {DATA_MOUNT} ({size_mb:.0f} MB):"
            " expected the 512 MB lab filesystem")


def _inject_v1() -> None:
    """Fill the volume completely: zero bytes free, so /health also fails."""
    _check_lab_fs()
    app_user = f"{APP_UID}:{APP_GID}"
    stamp = _two_days_ago_stamp()
    script = "\n".join([
        "mkdir -p /d/tmp",
        "i=0",
        "while [ $i -lt " + str(MAX_ORPHANS) + " ]; do",
        "  avail_kb=$(df -k /d | awk 'NR==2{print $4}')",
        "  if [ \"$avail_kb\" -ge 32768 ]; then bs=1M; count=" + str(CHUNK_MB),
        "  elif [ \"$avail_kb\" -ge 2048 ]; then bs=1M; count=1",
        "  else break",
        "  fi",
        "  name=" + _HEX32,
        "  if dd if=/dev/urandom of=/d/tmp/$name.part bs=$bs count=$count 2>/dev/null; then",
        "    touch -t " + stamp + " /d/tmp/$name.part",
        "  else",
        "    touch -t " + stamp + " /d/tmp/$name.part 2>/dev/null || true",
        "    break",
        "  fi",
        "  i=$((i + 1))",
        "done",
        "# top up to true exhaustion: byte-sized files until one fails",
        "while :; do",
        "  name=" + _HEX32,
        "  head -c 1 /dev/urandom > /d/tmp/$name.part 2>/dev/null || break",
        "  touch -t " + stamp + " /d/tmp/$name.part",
        "done",
    ])
    _docker("sh", "-c", script, user=app_user, timeout=300)
    if _available_bytes() != 0:
        raise RuntimeError("V1 setup missed: volume still not full")


def _inject_v2() -> None:
    """Exhaust inodes with many small stale .part files; bytes stay free."""
    _check_lab_fs()
    app_user = f"{APP_UID}:{APP_GID}"
    stamp = _two_days_ago_stamp()
    cap = os.statvfs(DATA_MOUNT).f_files + 1000
    script = "\n".join([
        "mkdir -p /d/tmp",
        "i=0",
        "while [ $i -lt " + str(cap) + " ]; do",
        "  name=" + _HEX32,
        "  head -c 1024 /dev/urandom > /d/tmp/$name.part 2>/dev/null || break",
        "  touch -t " + stamp + " /d/tmp/$name.part",
        "  i=$((i + 1))",
        "done",
    ])
    _docker("sh", "-c", script, user=app_user, timeout=600)
    s = os.statvfs(DATA_MOUNT)
    free_inodes = s.f_ffree
    free_mb = s.f_bavail * s.f_frsize / (1024 * 1024)
    if not (free_inodes < 100 and free_mb > 100):
        raise RuntimeError(
            f"V2 setup missed: {free_inodes} inodes and {free_mb:.0f} MB still free")


def _inject_v3() -> None:
    """Leave ~450 KB free, so /health passes but 1 MB uploads fail."""
    _check_lab_fs()
    app_user = f"{APP_UID}:{APP_GID}"
    stamp = _two_days_ago_stamp()
    fill = _available_bytes() - V3_TARGET_FREE_KB * 1024
    if fill <= 0:
        raise RuntimeError("V3 setup refused: volume already too full")
    full, rest = divmod(fill, CHUNK_MB * 1024 * 1024)
    rest_kb = rest // 1024
    script = "\n".join([
        "mkdir -p /d/tmp",
        "fail=0; i=0",
        "while [ $i -lt " + str(full) + " ]; do",
        "  name=" + _HEX32,
        "  dd if=/dev/urandom of=/d/tmp/$name.part bs=1M count=" + str(CHUNK_MB)
        + " 2>/dev/null || { fail=1; break; }",
        "  touch -t " + stamp + " /d/tmp/$name.part",
        "  i=$((i + 1))",
        "done",
        "if [ $fail = 0 ] && [ " + str(rest_kb) + " -gt 0 ]; then",
        "  name=" + _HEX32,
        "  dd if=/dev/urandom of=/d/tmp/$name.part bs=1K count=" + str(rest_kb)
        + " 2>/dev/null",
        "  touch -t " + stamp + " /d/tmp/$name.part",
        "fi",
    ])
    _docker("sh", "-c", script, user=app_user, timeout=300)
    free_kb = _available_bytes() / 1024
    if not (V3_FREE_MIN_KB <= free_kb <= V3_FREE_MAX_KB):
        raise RuntimeError(
            f"V3 setup missed: {free_kb:.0f} KB free, want 300-600 KB")


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
    if not os.path.isdir(os.path.join(DATA_MOUNT, TMP)):
        return
    _docker("find", f"/d/{TMP}", "-maxdepth", "1", "-name", "*.part",
            "-type", "f", "-delete", timeout=300)


def _run_child(*argv: str) -> tuple[bool, str]:
    """Run repair.py with argv as a child (no imports: this file runs from lab/)."""
    repair_py = Path(__file__).resolve().parent.parent / "repair.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(repair_py), *argv],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        return False, str(err)
    lines = proc.stdout.strip().splitlines()
    return proc.returncode == 0, lines[-1] if lines else f"exit {proc.returncode}"


def _run_verifier() -> tuple[bool, str]:
    return _run_child("verify")


def _hit_health() -> None:
    try:
        with urllib.request.urlopen(APP_URL.rstrip("/") + "/health", timeout=10):
            pass
    except Exception:
        pass


def _inject_d3() -> None:
    """Disconnect db from every network it shares with the app."""
    shared = sorted(_container_networks(APP) & _container_networks(DB))
    if not shared:
        raise RuntimeError(f"{DB} already shares no network with {APP}")
    for network in shared:
        _docker_cmd("network", "disconnect", network, DB)


def _setup_h2() -> None:
    """Restart db, fail requests into the outage, wait until healthy again."""
    _docker_cmd("restart", DB)
    for _ in range(3):
        _hit_health()
    for _ in range(2):
        _run_verifier()
    _wait_db_healthy()
    _wait_app_healthy()
    logs = _docker_cmd("logs", "--tail", "30", APP, merge_stderr=True)
    if not any(key in logs.lower() for key in
               ("connection", "refused", "translate", "operationalerror")):
        raise RuntimeError("H2 setup produced no errors in the app log")


def _setup_h3() -> None:
    """Fill the volume, fail one upload, run the repair, wait until healthy."""
    _inject_v1()
    ok, _ = _run_verifier()
    if ok:
        raise RuntimeError("H3 setup: upload succeeded on a full volume")
    repair_ok, repair_detail = _run_child("clear_stale_temp_files", "--yes", "--no-verify")
    if not repair_ok:
        raise RuntimeError(f"H3 setup: repair failed: {repair_detail}")
    _wait_app_healthy()
    logs = _docker_cmd("logs", "--tail", "50", APP, merge_stderr=True)
    if "no space left" not in logs.lower():
        raise RuntimeError("H3 setup produced no 'No space left' errors in the app log")


def _lab_problems() -> list[str]:
    """Why the lab is not healthy (empty means clean). Status first, then verify."""
    problems: list[str] = []
    try:
        uploads = status_uploads()
    except (OSError, RuntimeError) as err:
        problems.append(f"uploads/ status unreadable: {err}")
    else:
        if not uploads["app_user_can_write"]:
            problems.append(
                f"uploads/ not writable by app user"
                f" (owner {uploads['owner_uid']}:{uploads['owner_gid']},"
                f" mode {uploads['mode']})")
        if not uploads["tmp_app_user_can_write"]:
            problems.append(
                f"tmp/ not writable by app user"
                f" (owner {uploads['tmp_owner_uid']}:{uploads['tmp_owner_gid']},"
                f" mode {uploads['tmp_mode']})")
    try:
        volume = status_volume()
    except (OSError, RuntimeError) as err:
        problems.append(f"volume status unreadable: {err}")
    else:
        if volume["use_percent"] >= 95:
            problems.append(
                f"volume {volume['use_percent']}% full"
                f" ({volume['available_mb']} MB free)")
    try:
        db = status_database()
    except (OSError, RuntimeError) as err:
        problems.append(f"db status unreadable: {err}")
    else:
        if not db["running"]:
            problems.append(f"db {DB} not running (status {db['status']})")
        elif db["paused"]:
            problems.append(f"db {DB} is paused")
        elif db["health"] not in ("healthy", "no healthcheck"):
            problems.append(f"db {DB} health is {db['health']}")
        else:
            try:
                app_networks = _container_networks(APP)
            except (OSError, RuntimeError) as err:
                problems.append(f"app status unreadable: {err}")
            else:
                if not (app_networks & set(db["networks"])):
                    problems.append(f"db {DB} shares no network with the app")
    if problems:
        return problems
    ok, detail = _run_verifier()
    if not ok:
        problems.append(f"pre-inject verify failed: {detail}")
    return problems


def _wait_app_healthy() -> None:
    url = APP_URL.rstrip("/") + "/health"
    deadline = time.monotonic() + HEALTH_TIMEOUT
    last = "no response yet"
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    return
                last = f"status {resp.status}"
        except urllib.error.HTTPError as err:
            last = f"status {err.code}"
        except (urllib.error.URLError, OSError) as err:
            last = f"{type(err).__name__}: {err}"
        if time.monotonic() >= deadline:
            raise RuntimeError(f"app still unhealthy after {HEALTH_TIMEOUT}s ({url}: {last})")
        time.sleep(HEALTH_POLL_SECONDS)


def _reset_upload_dirs() -> None:
    for dirname in (UPLOADS, TMP):
        _docker("chown", f"{APP_UID}:{APP_GID}", f"/d/{dirname}")
        _docker("chmod", "755", f"/d/{dirname}")


def inject(variant: str) -> None:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant: {variant}")
    problems = _lab_problems()
    if problems:
        raise RuntimeError(
            f"lab is not healthy, refusing to inject {variant}: " + "; ".join(problems))
    if variant == "H1":
        print("H1: lab confirmed clean (nothing to set up)")
        return
    if variant in ("V1", FAULT_VOLUME_FULL):
        _inject_v1()
    elif variant == "V2":
        _inject_v2()
    elif variant == "V3":
        _inject_v3()
    elif variant in ("P1", FAULT_UPLOAD_DIR):
        _docker("chown", "0:0", f"/d/{UPLOADS}")
        _docker("chmod", "755", f"/d/{UPLOADS}")
    elif variant == "P2":
        _docker("chown", f"{APP_UID}:{APP_GID}", f"/d/{UPLOADS}")
        _docker("chmod", "555", f"/d/{UPLOADS}")
    elif variant == "P3":
        _docker("chown", "0:0", f"/d/{TMP}")
        _docker("chmod", "755", f"/d/{TMP}")
    elif variant in ("D1", FAULT_DATABASE):
        _docker_cmd("stop", DB)
    elif variant == "D2":
        _docker_cmd("pause", DB)
    elif variant == "D3":
        _inject_d3()
    elif variant == "H2":
        _setup_h2()
    elif variant == "H3":
        _setup_h3()
    else:
        raise RuntimeError(f"no inject branch for variant: {variant}")
    print(f"{variant}: injected")


def reset_all() -> None:
    """Reset every fault family without waiting (for teardown sequences)."""
    errors = []
    for name, fix in (("volume", _reset_volume_full),
                      ("permissions", _reset_upload_dirs),
                      ("database", _reset_database)):
        try:
            fix()
        except Exception as err:   # noqa: BLE001 - collected below
            errors.append(f"{name}: {err}")
    if errors:
        raise RuntimeError("reset incomplete: " + "; ".join(errors))


def reset(variant: str) -> None:
    if variant == "all":
        reset_all()
    elif variant in V_VARIANTS:
        _reset_volume_full()
    elif variant in P_VARIANTS:
        _reset_upload_dirs()
    elif variant in D_VARIANTS:
        _reset_database()
    elif variant in H_VARIANTS:
        pass   # already clean; the health wait below confirms it
    else:
        raise ValueError(f"unknown variant: {variant}")
    _wait_app_healthy()
    print(f"{variant}: reset")


def _dir_access(path: str) -> tuple:
    st = os.stat(path)
    mode = stat.S_IMODE(st.st_mode)
    if st.st_uid == APP_UID:
        writable = bool(mode & 0o200)
    elif st.st_gid == APP_GID:
        writable = bool(mode & 0o020)
    else:
        writable = bool(mode & 0o002)
    return st, mode, writable


def status_uploads() -> dict:
    upath = os.path.join(DATA_MOUNT, UPLOADS)
    ust, umode, uwritable = _dir_access(upath)
    tpath = os.path.join(DATA_MOUNT, TMP)
    tst, tmode, twritable = _dir_access(tpath)
    return {
        "path": upath,
        "owner_uid": ust.st_uid,
        "owner_gid": ust.st_gid,
        "mode": f"{umode:04o}",
        "app_user_can_write": uwritable,
        "tmp_path": tpath,
        "tmp_owner_uid": tst.st_uid,
        "tmp_owner_gid": tst.st_gid,
        "tmp_mode": f"{tmode:04o}",
        "tmp_app_user_can_write": twritable,
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
        "inode_use_percent": round(100 * (s.f_files - s.f_ffree) / s.f_files, 1) if s.f_files else 0.0,
        "inodes_free": s.f_ffree,
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


def status(variant: str | None = None) -> dict:
    if variant is None:
        report = {FAULT_UPLOAD_DIR: status_uploads(),
                  FAULT_VOLUME_FULL: status_volume(),
                  FAULT_DATABASE: status_database()}
    elif variant in V_VARIANTS:
        report = status_volume()
    elif variant in P_VARIANTS:
        report = status_uploads()
    elif variant in D_VARIANTS:
        report = status_database()
    elif variant in H_VARIANTS:
        raise ValueError(f"no status for healthy setup {variant}; run the verifier")
    else:
        raise ValueError(f"unknown variant: {variant}")
    print(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inject, reset or report the lab cases.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_inject = sub.add_parser("inject")
    p_inject.add_argument("variant")
    p_reset = sub.add_parser("reset")
    p_reset.add_argument("variant")
    p_status = sub.add_parser("status")
    p_status.add_argument("variant", nargs="?")
    args = parser.parse_args(argv)
    try:
        if args.command == "inject":
            inject(args.variant)
        elif args.command == "reset":
            reset(args.variant)
        else:
            status(args.variant)
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except (RuntimeError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
