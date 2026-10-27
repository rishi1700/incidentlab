#!/usr/bin/env python3
"""IncidentLab investigator: starter for the Meta-driven diagnosis loop.

Flow
  1. Send the symptom to Muse Spark (Responses API) with a set of read-only checks.
  2. Run each check the model asks for and give every result an evidence ID (E1, E2, ...).
  3. Ask for a schema-constrained incident report that cites evidence by ID, field and value.
  4. Check every citation against the raw evidence. The schema guarantees the shape of
     the report, not that its claims are true.
  5. Answer follow-up questions in the same incident context (previous_response_id).

It never changes the system. Each diagnosis maps to one predefined repair in code
(REPAIR_FOR_CAUSE); approval, execution and the upload/read-back verifier come next.

Run it inside the lab VM, as a user that can run docker:
    export MODEL_API_KEY=...
    python3 investigate.py "Uploads have stopped working. Find out what is happening."
    python3 investigate.py --no-followup --json runs/case01.json     # batch and eval runs

Lab settings are INCIDENTLAB_* environment variables (see LAB below). To simulate missing
evidence for a test case, make checks unavailable: INCIDENTLAB_UNAVAILABLE_CHECKS=container_logs
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

from openai import OpenAI


def _env(name: str, default: str) -> str:
    return os.environ.get(f"INCIDENTLAB_{name}", default)


LAB = {
    "project": _env("PROJECT", "incidentlab"),            # docker compose project name
    "app": _env("APP", "incidentlab-app-1"),
    "db": _env("DB", "incidentlab-db-1"),
    "data_mount": _env("DATA_MOUNT", "/srv/lab-data"),     # the small test filesystem
    "upload_dir": _env("UPLOAD_DIR", "/srv/lab-data/uploads"),
    "app_uid": int(_env("APP_UID", "1000")),               # the non-root user the app runs as
    "app_gid": int(_env("APP_GID", "1000")),
    "app_url": _env("APP_URL", "http://localhost:8080"),
}
BASE_URL = _env("BASE_URL", "https://api.meta.ai/v1")
MODEL = _env("MODEL", "muse-spark-1.3")      # Standard mode. "-contributor" IDs let Meta train on your data.
EFFORT = _env("EFFORT", "medium")            # minimal | low | medium | high | xhigh ("" = model default)
MAX_CHECKS = int(_env("MAX_CHECKS", "12"))   # real checks per investigation or follow-up
MAX_TURNS = int(_env("MAX_TURNS", "16"))     # hard stop for one model/tool loop
UNAVAILABLE = {c.strip() for c in _env("UNAVAILABLE_CHECKS", "").split(",") if c.strip()}

# Muse Spark Standard mode, USD per 1M tokens (dev.meta.ai/docs/pricing-rate-limits, Oct 2026).
PRICE = {"input": 1.25, "cached": 0.15, "output": 4.25}


# ---------------------------------------------------------------------------
# Read-only checks. The model chooses a check and its arguments; every target
# it can reach is allowlisted here, and nothing runs through a shell.
# ---------------------------------------------------------------------------

CONTAINERS = [LAB["app"], LAB["db"]]
DISK_PATHS = [LAB["data_mount"], "/"]
ACCESS_PATHS = [LAB["upload_dir"], LAB["data_mount"]]


class CheckError(Exception):
    """A check could not produce data. The message goes back to the model as the result."""


def _run(cmd: list[str], timeout: int = 10, merge_stderr: bool = False) -> str:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise CheckError(f"command not found: {cmd[0]}") from None
    except subprocess.TimeoutExpired:
        raise CheckError(f"timed out after {timeout}s") from None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise CheckError(f"exit code {proc.returncode}: {detail}")
    return proc.stdout


def list_containers() -> dict:
    out = _run(["docker", "ps", "-a",
                "--filter", f"label=com.docker.compose.project={LAB['project']}",
                "--format", "{{json .}}"])
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    return {"containers": [{"name": r.get("Names"), "image": r.get("Image"),
                            "state": r.get("State"), "status": r.get("Status")} for r in rows]}


def inspect_container(name: str) -> dict:
    data = json.loads(_run(["docker", "inspect", "--type", "container", name]))[0]
    state = data.get("State") or {}
    config = data.get("Config") or {}
    networks = (data.get("NetworkSettings") or {}).get("Networks") or {}
    return {
        "name": name,
        "status": state.get("Status"),
        "running": state.get("Running"),
        "exit_code": state.get("ExitCode"),
        "error": state.get("Error") or None,
        "oom_killed": state.get("OOMKilled"),
        "health": (state.get("Health") or {}).get("Status") or "no healthcheck",
        "restart_count": data.get("RestartCount"),
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
        "user": config.get("User") or "(not set: runs as root)",
        "mounts": [{"type": m.get("Type"), "source": m.get("Source"),
                    "destination": m.get("Destination"), "rw": m.get("RW")}
                   for m in data.get("Mounts") or []],
        "networks": sorted(networks),
    }


def container_logs(name: str, tail: int) -> dict:
    out = _run(["docker", "logs", "--timestamps", "--tail", str(tail), name], merge_stderr=True)
    lines = [line[:300] for line in out.splitlines()]
    kept, budget = [], 8000                      # keep the newest lines within ~8k characters
    for line in reversed(lines):
        if budget - len(line) < 0:
            break
        kept.append(line)
        budget -= len(line)
    kept.reverse()
    return {"name": name, "lines": kept, "older_lines_omitted": len(lines) - len(kept)}


def _mountpoint(path: str) -> str:
    path = os.path.realpath(path)
    while not os.path.ismount(path):
        path = os.path.dirname(path)
    return path


def disk_usage(path: str) -> dict:
    s = os.statvfs(path)
    used = (s.f_blocks - s.f_bfree) * s.f_frsize
    available = s.f_bavail * s.f_frsize           # what a non-root process can still use
    mib = 2 ** 20
    return {
        "path": path,
        "mountpoint": _mountpoint(path),
        "size_mb": round(s.f_blocks * s.f_frsize / mib, 1),
        "used_mb": round(used / mib, 1),
        "available_mb": round(available / mib, 1),
        "use_percent": round(100 * used / (used + available), 1) if used + available else 0.0,
        "inode_use_percent": round(100 * (s.f_files - s.f_ffree) / s.f_files, 1) if s.f_files else 0.0,
    }


def path_access(path: str) -> dict:
    st = os.stat(path)
    mode = stat.S_IMODE(st.st_mode)
    is_dir = stat.S_ISDIR(st.st_mode)
    uid, gid = LAB["app_uid"], LAB["app_gid"]
    if uid == 0:
        allowed = True
    else:
        if st.st_uid == uid:
            write_bit, exec_bit = 0o200, 0o100
        elif st.st_gid == gid:
            write_bit, exec_bit = 0o020, 0o010
        else:
            write_bit, exec_bit = 0o002, 0o001
        allowed = bool(mode & write_bit) and (bool(mode & exec_bit) or not is_dir)
    return {
        "path": path,
        "is_dir": is_dir,
        "owner_uid": st.st_uid,
        "owner_gid": st.st_gid,
        "mode": f"{mode:04o}",
        "app_uid": uid,
        "app_gid": gid,
        "app_user_can_write": allowed,
        "method": "owner and mode bits only; ignores ACLs and supplementary groups",
    }


def recent_events(minutes: int) -> dict:
    now = int(time.time())
    out = _run(["docker", "events",
                "--since", str(now - minutes * 60), "--until", str(now),
                "--filter", "type=container",
                "--filter", f"label=com.docker.compose.project={LAB['project']}",
                "--format", "{{json .}}"], timeout=15)
    events = []
    for line in out.splitlines()[-50:]:
        event = json.loads(line)
        attrs = (event.get("Actor") or {}).get("Attributes") or {}
        events.append({
            "time": datetime.fromtimestamp(event.get("time", 0), timezone.utc).isoformat(timespec="seconds"),
            "container": attrs.get("name"),
            "action": event.get("Action") or event.get("status"),
            "exit_code": attrs.get("exitCode"),
        })
    return {"minutes": minutes, "events": events}


def app_health() -> dict:
    url = LAB["app_url"].rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return {"url": url, "status_code": resp.status,
                    "body": resp.read(300).decode("utf-8", "replace")}
    except urllib.error.HTTPError as err:
        return {"url": url, "status_code": err.code, "body": err.read(300).decode("utf-8", "replace")}
    except (urllib.error.URLError, OSError) as err:
        return {"url": url, "status_code": None, "error": str(getattr(err, "reason", err))}


def _choice(options: list):
    def check(value):
        if value not in options:
            raise CheckError(f"argument must be one of {options}")
        return value
    return check


def _bounded_int(low: int, high: int):
    def check(value):
        if isinstance(value, bool):
            raise CheckError(f"argument must be an integer from {low} to {high}")
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise CheckError(f"argument must be an integer from {low} to {high}") from None
        return max(low, min(number, high))
    return check


# name -> (function, description for the model, {argument: (JSON schema, validator)})
CHECKS = {
    "list_containers": (
        list_containers, "List the lab's containers with their state and status.", {}),
    "inspect_container": (
        inspect_container,
        "State, exit code, health, restart count, user, mounts and networks of one container.",
        {"name": ({"type": "string", "enum": CONTAINERS}, _choice(CONTAINERS))}),
    "container_logs": (
        container_logs, "Newest log lines of one container (stdout and stderr, timestamped).",
        {"name": ({"type": "string", "enum": CONTAINERS}, _choice(CONTAINERS)),
         "tail": ({"type": "integer", "description": "Number of lines, 1-200."}, _bounded_int(1, 200))}),
    "disk_usage": (
        disk_usage, "Capacity and inode usage of the filesystem that holds a path.",
        {"path": ({"type": "string", "enum": DISK_PATHS}, _choice(DISK_PATHS))}),
    "path_access": (
        path_access, "Owner, mode, and whether the application's user can write to a directory.",
        {"path": ({"type": "string", "enum": ACCESS_PATHS}, _choice(ACCESS_PATHS))}),
    "recent_events": (
        recent_events, "Docker events (start, stop, die, health changes) for the lab's containers.",
        {"minutes": ({"type": "integer", "description": "Look-back window, 1-120 minutes."},
                     _bounded_int(1, 120))}),
    "app_health": (
        app_health, "HTTP status of the application's /health endpoint.", {}),
}

TOOLS = [
    {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": {arg: schema for arg, (schema, _) in spec.items()},
            "required": list(spec),
            "additionalProperties": False,
        },
    }
    for name, (_, description, spec) in CHECKS.items()
]


def run_check(name: str, raw_arguments: str) -> dict:
    """Validate the model's arguments (they are not guaranteed to match the schema) and run one check."""
    if name not in CHECKS:
        raise CheckError(f"unknown check '{name}'")
    if name in UNAVAILABLE:
        raise CheckError("this data source is unavailable")
    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        raise CheckError("arguments were not valid JSON") from None
    if not isinstance(arguments, dict):
        raise CheckError("arguments must be a JSON object")
    function, _, spec = CHECKS[name]
    if set(arguments) != set(spec):
        raise CheckError(f"expected arguments {sorted(spec)}, got {sorted(arguments)}")
    return function(**{arg: validate(arguments[arg]) for arg, (_, validate) in spec.items()})


# ---------------------------------------------------------------------------
# The report and how its citations are checked.
# ---------------------------------------------------------------------------

CAUSES = ["volume_full", "upload_dir_not_writable", "database_unavailable",
          "healthy", "insufficient_evidence"]
FAULTS = CAUSES[:3]

# One predefined repair per diagnosis, chosen by code. The model never picks actions.
REPAIR_FOR_CAUSE = {
    "volume_full": "clear_stale_temp_files",
    "upload_dir_not_writable": "restore_upload_dir_access",
    "database_unavailable": "restore_database",
    "healthy": None,
    "insufficient_evidence": None,
}

_CITATION = {
    "type": "object",
    "properties": {
        "evidence_id": {"type": "string", "description": "For example E3."},
        "field": {"type": "string",
                  "description": "Dotted path into that evidence's data, e.g. use_percent or "
                                 "mounts.0.source. For logs use 'lines'."},
        "value": {"type": "string",
                  "description": "The exact value at that field. For logs, an exact quote from one line."},
    },
    "required": ["evidence_id", "field", "value"],
    "additionalProperties": False,
}

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "cause": {"type": "string", "enum": CAUSES},
        "summary": {"type": "string", "description": "Two or three sentences for the administrator."},
        "findings": {
            "type": "array",
            "items": {"type": "object",
                      "properties": {"claim": {"type": "string"}, "citation": _CITATION},
                      "required": ["claim", "citation"], "additionalProperties": False},
        },
        "ruled_out": {
            "type": "array",
            "items": {"type": "object",
                      "properties": {"cause": {"type": "string", "enum": FAULTS},
                                     "reason": {"type": "string"},
                                     "citation": _CITATION},
                      "required": ["cause", "reason", "citation"], "additionalProperties": False},
        },
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["cause", "summary", "findings", "ruled_out", "open_questions"],
    "additionalProperties": False,
}

INSTRUCTIONS = f"""You are IncidentLab, investigating one Docker application on a Linux test VM.
The application container is {LAB['app']}. It stores uploaded files in {LAB['upload_dir']} on the
host, which is bind-mounted into the container, and it depends on the database container {LAB['db']}.

How to work:
- Collect evidence with the read-only checks before concluding. Start broad, then narrow down.
- Every check result has an evidence ID (E1, E2, ...). Base every conclusion on evidence you
  collected. Never assume a value you have not seen.
- Separate the root cause from its symptoms: an application error can be caused by a dependency.
- Prefer checks that can rule a cause in or out over checks that repeat what you already know.
- "healthy" and "insufficient_evidence" are valid outcomes. Do not force a diagnosis.
- You cannot change the system. Repairs happen separately, after a person approves them.
- You have a budget of {MAX_CHECKS} checks.
- In follow-up answers, cite evidence IDs for every factual claim."""

REPORT_REQUEST = (
    "Write the incident report now, using only the evidence collected above. Every finding and "
    "every ruled-out cause must cite one evidence ID with the exact field and value it relies on "
    "(for logs: field 'lines' and an exact quote). In ruled_out, list the other causes you "
    "considered. Choose insufficient_evidence if the evidence does not single out one cause."
)


def _norm(value) -> str:
    return " ".join(str(value).split()).lower()


def _resolve(data, path: str):
    current = data
    for part in path.split("."):
        if isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise KeyError(part)
    return current


def _matches(actual, claimed) -> bool:
    want = _norm(claimed)
    if not want:
        return False
    if isinstance(actual, bool):
        return want == str(actual).lower()
    if isinstance(actual, (int, float)):
        try:
            return abs(float(want.rstrip("%").strip()) - float(actual)) <= 0.5
        except ValueError:
            return False
    if actual is None:
        return want in ("none", "null")
    if isinstance(actual, list):          # log lines and similar: the quote must sit inside one item
        return any(want in _norm(json.dumps(item) if isinstance(item, dict) else item) for item in actual)
    if isinstance(actual, dict):
        return want in _norm(json.dumps(actual))
    text = _norm(actual)
    if want.isdigit() and text.isdigit():  # e.g. mode "0700" cited as "700"
        return want.lstrip("0") == text.lstrip("0")
    return want == text or (len(want) >= 8 and want in text)


def check_citation(citation: dict, evidence: dict) -> tuple[bool, str]:
    eid, field = citation.get("evidence_id", ""), citation.get("field", "")
    if eid not in evidence:
        return False, f"{eid} does not exist"
    try:
        actual = _resolve(evidence[eid]["data"], field)
    except KeyError:
        return False, f"{eid} has no field '{field}'"
    if _matches(actual, citation.get("value", "")):
        return True, f"{eid}.{field} matches"
    shown = json.dumps(actual, default=str)
    if len(shown) > 80:
        shown = shown[:77] + "..."
    return False, f"{eid}.{field} is {shown}, not {citation.get('value')!r}"


def validate_report(report: dict, evidence: dict) -> dict:
    rows = []
    for item in report.get("findings", []):
        ok, detail = check_citation(item.get("citation", {}), evidence)
        rows.append({"kind": "finding", "text": item.get("claim", ""), "supported": ok, "detail": detail})
    for item in report.get("ruled_out", []):
        ok, detail = check_citation(item.get("citation", {}), evidence)
        rows.append({"kind": "ruled_out", "text": f"{item.get('cause')}: {item.get('reason', '')}",
                     "supported": ok, "detail": detail})
    warnings = []
    cause = report.get("cause")
    if cause in FAULTS and not any(r["supported"] for r in rows if r["kind"] == "finding"):
        warnings.append("no finding behind this diagnosis matches the collected evidence")
    if cause in {item.get("cause") for item in report.get("ruled_out", [])}:
        warnings.append("the diagnosed cause is also listed as ruled out")
    return {"citations": rows, "supported": sum(r["supported"] for r in rows),
            "total": len(rows), "warnings": warnings}


# ---------------------------------------------------------------------------
# The model loop.
# ---------------------------------------------------------------------------

class Investigation:
    def __init__(self, client: OpenAI):
        self.client = client
        self.evidence: dict[str, dict] = {}
        self.last_response_id: str | None = None
        self.checks_in_loop = 0
        self.usage = {"requests": 0, "input": 0, "cached": 0, "output": 0}
        self.notes = ""

    def _create(self, input_items: list, **extra):
        # instructions, tools and reasoning effort are per request: they do not carry
        # over through previous_response_id, so they are sent every time.
        kwargs = {"model": MODEL, "instructions": INSTRUCTIONS, "input": input_items, **extra}
        if EFFORT:
            kwargs["reasoning"] = {"effort": EFFORT}
        if self.last_response_id:
            kwargs["previous_response_id"] = self.last_response_id
        resp = self.client.responses.create(**kwargs)
        self.last_response_id = resp.id
        self._count(resp)
        return resp

    def _count(self, resp) -> None:
        self.usage["requests"] += 1
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        details = getattr(usage, "input_tokens_details", None)
        self.usage["input"] += getattr(usage, "input_tokens", 0) or 0
        self.usage["cached"] += (getattr(details, "cached_tokens", 0) or 0) if details else 0
        self.usage["output"] += getattr(usage, "output_tokens", 0) or 0   # includes reasoning tokens

    def cost_usd(self) -> float:
        u = self.usage
        return ((u["input"] - u["cached"]) * PRICE["input"] + u["cached"] * PRICE["cached"]
                + u["output"] * PRICE["output"]) / 1e6

    def _answer(self, call) -> dict:
        """Run one requested check and package the result for the model."""
        if self.checks_in_loop >= MAX_CHECKS:
            result = {"error": f"check budget of {MAX_CHECKS} reached; this check was not run. "
                               "Conclude with the evidence you have."}
            print(f"  --  {call.name}: not run (budget reached)")
        else:
            self.checks_in_loop += 1
            eid = f"E{len(self.evidence) + 1}"
            try:
                data, ok = run_check(call.name, call.arguments), True
            except CheckError as err:
                data, ok = {"error": str(err)}, False
            except Exception as err:            # a bug in one check should not end the investigation
                data, ok = {"error": f"check failed: {type(err).__name__}: {err}"}, False
            self.evidence[eid] = {"check": call.name, "arguments": call.arguments, "data": data}
            print(f"  {eid:<4}{call.name}({_short_args(call.arguments)})  "
                  f"{'ok' if ok else 'error: ' + data['error']}")
            result = {"evidence_id": eid, "data": data}
        return {"type": "function_call_output", "call_id": call.call_id,
                "output": json.dumps(result, default=str)}

    def _tool_loop(self, input_items: list):
        """Let the model run checks until it answers in text. Returns (response, pending outputs)."""
        self.checks_in_loop = 0
        for _ in range(MAX_TURNS):
            resp = self._create(input_items, tools=TOOLS)
            calls = [item for item in resp.output if item.type == "function_call"]
            if not calls:        # tool_choice is always "auto": the model may answer in text at any turn
                return resp, []
            input_items = [self._answer(call) for call in calls]
        return None, input_items   # hard stop: these results still have to be sent

    def investigate(self, symptom: str) -> dict:
        resp, pending = self._tool_loop([{"role": "user", "content": symptom}])
        self.notes = resp.output_text if resp is not None else ""
        resp = self._create(
            pending + [{"role": "user", "content": REPORT_REQUEST}],
            text={"format": {"type": "json_schema", "name": "incident_report",
                             "schema": REPORT_SCHEMA, "strict": True}},
        )
        if not resp.output_text:
            raise RuntimeError(f"the report came back empty (status: {getattr(resp, 'status', '?')})")
        return json.loads(resp.output_text)

    def follow_up(self, question: str) -> str:
        resp, pending = self._tool_loop([{"role": "user", "content": question}])
        if resp is None:
            resp = self._create(pending + [{"role": "user", "content":
                                            "Answer the question now with the evidence you have."}])
        return resp.output_text


def _short_args(raw: str) -> str:
    try:
        return ", ".join(f"{k}={v}" for k, v in json.loads(raw or "{}").items())
    except (json.JSONDecodeError, AttributeError):
        return raw


def print_report(report: dict, validation: dict) -> None:
    print(f"\nDiagnosis: {report['cause']}\n{report['summary']}")
    for kind, heading in (("finding", "Findings"), ("ruled_out", "Ruled out")):
        rows = [r for r in validation["citations"] if r["kind"] == kind]
        if rows:
            print(f"\n{heading}")
            for r in rows:
                print(f"  [{'supported' if r['supported'] else 'UNSUPPORTED'}] {r['text']}  ({r['detail']})")
    if report["open_questions"]:
        print("\nOpen questions")
        for question in report["open_questions"]:
            print(f"  - {question}")
    print(f"\nCitations matching the evidence: {validation['supported']}/{validation['total']}")
    for warning in validation["warnings"]:
        print(f"WARNING: {warning}")


def main() -> None:
    parser = argparse.ArgumentParser(description="IncidentLab investigator (starter)")
    parser.add_argument("symptom", nargs="?",
                        default="Uploads have stopped working. Find out what is happening.")
    parser.add_argument("--no-followup", action="store_true", help="exit after the report (batch runs)")
    parser.add_argument("--json", metavar="PATH", help="write report, checks, evidence and usage to a file")
    args = parser.parse_args()

    api_key = os.environ.get("MODEL_API_KEY")
    if not api_key:
        sys.exit("Set MODEL_API_KEY first.")
    # The SDK retries 429s and 5xx errors with backoff on its own (max_retries).
    client = OpenAI(base_url=BASE_URL, api_key=api_key, timeout=180, max_retries=3)
    inv = Investigation(client)

    print(f"Investigating: {args.symptom}  [model {MODEL}, effort {EFFORT or 'default'}]\n")
    started = time.monotonic()
    report = inv.investigate(args.symptom)
    seconds = round(time.monotonic() - started, 1)
    validation = validate_report(report, inv.evidence)
    print_report(report, validation)
    repair = REPAIR_FOR_CAUSE[report["cause"]]
    print(f"\nRepair for this diagnosis: {repair or 'none'} (approval and execution not built yet)")
    print(f"{len(inv.evidence)} checks, {seconds}s, {inv.usage['requests']} requests, "
          f"{inv.usage['input']} input / {inv.usage['output']} output tokens, ~${inv.cost_usd():.3f}")

    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w") as f:
            json.dump({"symptom": args.symptom, "model": MODEL, "effort": EFFORT,
                       "unavailable_checks": sorted(UNAVAILABLE), "report": report,
                       "validation": validation, "repair": repair, "evidence": inv.evidence,
                       "investigation_notes": inv.notes, "seconds": seconds, "usage": inv.usage,
                       "cost_usd": round(inv.cost_usd(), 4),
                       "last_response_id": inv.last_response_id}, f, indent=2, default=str)

    if args.no_followup:
        return
    while True:
        try:
            question = input("\nFollow-up (blank to finish): ").strip()
        except EOFError:
            break
        if not question:
            break
        print(inv.follow_up(question))
    print(f"\nSession total: {inv.usage['requests']} requests, ~${inv.cost_usd():.3f}")


if __name__ == "__main__":
    main()
