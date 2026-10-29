#!/usr/bin/env python3
"""Demo page server (milestone 4, step 1).

One static page where a visitor types the usual complaint, watches the
evidence trail grow live, reads the report with supported/unsupported
citations, asks follow-ups, and approves the mapped repair. Progress streams
over server-sent events. Fault injection stays in the terminal: this server
never imports lab/faults and never shows which fault is active.

Start it (MODEL_API_KEY needed for real runs) and open the URL in a browser:
    .venv/bin/python web/server.py    # http://127.0.0.1:8090
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import investigate
import repair as repair_module

HOST = "127.0.0.1"
PORT = 8090

app = FastAPI(title="IncidentLab demo")

_state_lock = threading.Lock()
_state = {"busy": False, "inv": None, "report": None, "validation": None,
          "seconds": None}


def _try_start() -> bool:
    """Claim the single session. False when another request is running."""
    with _state_lock:
        if _state["busy"]:
            return False
        _state.update(busy=True, inv=None, report=None, validation=None,
                      seconds=None)
        return True


def _finish() -> None:
    with _state_lock:
        _state["busy"] = False


def _client() -> OpenAI:
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        raise RuntimeError("Set MODEL_API_KEY first.")
    return OpenAI(base_url=investigate.BASE_URL, api_key=key,
                  timeout=180, max_retries=3)


def _sse(event: dict) -> str:
    return "data: " + json.dumps(event, default=str) + "\n\n"


def _stream(events: queue.Queue):
    try:
        while True:
            event = events.get()
            yield _sse(event)
            if event["type"] in ("done", "answer", "error"):
                return
    finally:
        _finish()


def _run_investigation(events: queue.Queue, symptom: str) -> None:
    try:
        inv = investigate.Investigation(
            _client(), on_check=lambda e: events.put({"type": "check", **e}))
        started = time.monotonic()
        report = inv.investigate(symptom)
        seconds = round(time.monotonic() - started, 1)
        validation = investigate.validate_report(report, inv.evidence)
        with _state_lock:
            _state.update(inv=inv, report=report, validation=validation,
                          seconds=seconds)
        events.put({"type": "done", "report": report, "validation": validation,
                    "repair": investigate.REPAIR_FOR_CAUSE[report["cause"]],
                    "seconds": seconds, "usage": inv.usage,
                    "cost_usd": round(inv.cost_usd(), 4)})
    except Exception as err:   # noqa: BLE001 - every failure becomes an SSE error event
        events.put({"type": "error", "message": f"{type(err).__name__}: {err}"})


def _run_followup(events: queue.Queue, inv, question: str) -> None:
    try:
        inv.on_check = lambda e: events.put({"type": "check", **e})
        started = time.monotonic()
        text = inv.follow_up(question)
        seconds = round(time.monotonic() - started, 1)
        events.put({"type": "answer", "text": text, "seconds": seconds,
                    "usage": inv.usage, "cost_usd": round(inv.cost_usd(), 4)})
    except Exception as err:   # noqa: BLE001 - every failure becomes an SSE error event
        events.put({"type": "error", "message": f"{type(err).__name__}: {err}"})


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.post("/api/investigate")
async def investigate_endpoint(request: Request):
    return _investigate_stream(await request.json())


def _investigate_stream(body) -> StreamingResponse:
    symptom = (body.get("symptom") or "").strip() if isinstance(body, dict) else ""
    if not symptom:
        raise HTTPException(400, "the symptom box is empty")
    if not _try_start():
        raise HTTPException(409, "another request is still running")
    print(f"investigating: {symptom}", flush=True)
    events: queue.Queue = queue.Queue()
    worker = threading.Thread(target=_run_investigation, args=(events, symptom),
                              daemon=True)
    worker.start()
    return StreamingResponse(_stream(events), media_type="text/event-stream")


@app.post("/api/followup")
async def followup_endpoint(request: Request):
    return _followup_stream(await request.json())


def _followup_stream(body) -> StreamingResponse:
    question = (body.get("question") or "").strip() if isinstance(body, dict) else ""
    if not question:
        raise HTTPException(400, "the question box is empty")
    with _state_lock:
        inv = _state["inv"]
        if _state["busy"] or inv is None:
            raise HTTPException(409 if inv is not None else 400,
                                "another request is still running"
                                if inv is not None else "investigate first")
        _state["busy"] = True
    print(f"follow-up: {question}", flush=True)
    events: queue.Queue = queue.Queue()
    worker = threading.Thread(target=_run_followup, args=(events, inv, question),
                              daemon=True)
    worker.start()
    return StreamingResponse(_stream(events), media_type="text/event-stream")


@app.post("/api/repair")
def repair_endpoint():
    with _state_lock:
        report = _state["report"]
        if _state["busy"]:
            raise HTTPException(409, "another request is still running")
        if report is None:
            raise HTTPException(400, "investigate first")
        _state["busy"] = True
    try:
        name = investigate.REPAIR_FOR_CAUSE[report["cause"]]
        if name is None:
            raise HTTPException(400, "this diagnosis has no repair")
        print(f"approved repair: {name}", flush=True)
        if name not in repair_module.REPAIRS:
            return {"repair": name,
                    "repair_result": {"repair": name, "applied": False, "ok": False,
                                      "detail": "repair not implemented yet; nothing changed"},
                    "verification": None}
        repair_result = repair_module.REPAIRS[name]()
        verification = repair_module.verify_recovery()
        print(f"repair {name}: {'OK' if repair_result.get('ok') else 'FAILED'}; "
              f"verification: {'OK' if verification.get('ok') else 'FAILED'}", flush=True)
        return {"repair": name, "repair_result": repair_result,
                "verification": verification}
    finally:
        _finish()


def main() -> None:
    import uvicorn

    print(f"IncidentLab demo: open http://{HOST}:{PORT} in a browser", flush=True)
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
