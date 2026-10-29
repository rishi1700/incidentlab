#!/usr/bin/env python3
"""Tests for the demo page server with a fake model client. No lab needed.

Needs the web deps, so run with the venv python:
    .venv/bin/python -m unittest tests.test_web -v
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HAS_WEB = (importlib.util.find_spec("fastapi") is not None
           and importlib.util.find_spec("httpx") is not None)

if HAS_WEB:
    from fastapi.testclient import TestClient

    import investigate
    from web import server


class FakeItem:
    """An SDK response output item: attribute access plus model_dump()."""

    def __init__(self, payload):
        self._payload = dict(payload)

    def __getattr__(self, name):
        return self._payload[name]

    def model_dump(self):
        return dict(self._payload)


class FakeResponses:
    def __init__(self, script):
        self.script = list(script)

    def create(self, **kwargs):
        return self.script.pop(0)


class FakeClient:
    def __init__(self, script):
        self.responses = FakeResponses(script)


def _resp(resp_id, output, text=""):
    return SimpleNamespace(id=resp_id, output=output, output_text=text, usage=None)


CALL = {"id": "fc-1", "type": "function_call", "call_id": "call-1",
        "name": "app_health", "arguments": "{}"}
MESSAGE = {"id": "msg-1", "type": "message", "status": "completed", "role": "assistant",
           "content": [{"type": "output_text", "text": "checking"}]}

REPORT = {"cause": "volume_full", "summary": "The data disk is full.",
          "findings": [{"claim": "health check failed",
                        "citation": {"evidence_id": "E1", "field": "status_code",
                                      "value": "200"}}],
          "ruled_out": [], "open_questions": ["who filled the disk?"]}


def _events(resp):
    """Parse the SSE stream of a completed TestClient response."""
    out = []
    for chunk in resp.text.split("\n\n"):
        for line in chunk.splitlines():
            if line.startswith("data:"):
                out.append(json.loads(line[5:]))
    return out


@unittest.skipUnless(HAS_WEB, "needs fastapi and httpx: run with .venv/bin/python")
class TestDemoServer(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self._reset_state()

    def tearDown(self):
        self._reset_state()

    def _reset_state(self):
        server._state.update(busy=False, inv=None, report=None,
                             validation=None, seconds=None)

    def _fake_model(self, script):
        return (patch.dict(os.environ, {"MODEL_API_KEY": "test-key"}),
                patch.object(server, "OpenAI", return_value=FakeClient(script)),
                patch.object(investigate, "run_check",
                             return_value={"status_code": 200}),
                patch("builtins.print"))

    def _investigate_script(self, report=None):
        return [_resp("r1", [FakeItem(CALL)]),
                _resp("r2", [FakeItem(MESSAGE)]),
                _resp("r3", [], text=json.dumps(report or REPORT))]

    def _investigate(self, script):
        key, client, checks, quiet = self._fake_model(script)
        with key, client, checks, quiet:
            return self.client.post("/api/investigate",
                                    json={"symptom": "uploads stuck"})

    def test_index_serves_the_page(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("IncidentLab demo", resp.text)
        self.assertIn('id="symptom"', resp.text)
        self.assertIn("Investigate", resp.text)

    def test_investigate_streams_checks_then_report(self):
        events = _events(self._investigate(self._investigate_script()))
        self.assertEqual([e["type"] for e in events], ["check", "done"])
        check = events[0]
        self.assertEqual(check["evidence_id"], "E1")
        self.assertEqual(check["check"], "app_health")
        self.assertEqual(check["arguments"], "{}")
        self.assertTrue(check["ok"])
        self.assertEqual(check["data"], {"status_code": 200})
        done = events[1]
        self.assertEqual(done["report"]["cause"], "volume_full")
        self.assertEqual(done["repair"], "clear_stale_temp_files")
        self.assertEqual(done["validation"]["supported"], 1)
        self.assertEqual(done["validation"]["total"], 1)

    def test_followup_streams_answer(self):
        script = self._investigate_script() + [
            _resp("r4", [FakeItem(MESSAGE)], text="the disk filled up")]
        key, client, checks, quiet = self._fake_model(script)
        with key, client, checks, quiet:
            self._events_check(self.client.post(
                "/api/investigate", json={"symptom": "uploads stuck"}))
            resp = self.client.post("/api/followup", json={"question": "why?"})
        events = _events(resp)
        self.assertEqual([e["type"] for e in events], ["answer"])
        self.assertEqual(events[0]["text"], "the disk filled up")

    def _events_check(self, resp):
        self.assertEqual(_events(resp)[-1]["type"], "done")

    def test_repair_applies_and_verifies(self):
        self._events_check(self._investigate(self._investigate_script()))
        with (patch.dict(server.repair_module.REPAIRS,
                         {"clear_stale_temp_files":
                          lambda: {"repair": "clear_stale_temp_files",
                                   "applied": True, "ok": True,
                                   "detail": "deleted 3 stale files"}}),
              patch.object(server.repair_module, "verify_recovery",
                           return_value={"ok": True,
                                         "detail": "hashes match"}),
              patch("builtins.print")):
            resp = self.client.post("/api/repair")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["repair"], "clear_stale_temp_files")
        self.assertTrue(body["repair_result"]["ok"])
        self.assertTrue(body["verification"]["ok"])

    def test_healthy_diagnosis_has_no_repair(self):
        healthy = dict(REPORT, cause="healthy", findings=[])
        done = _events(self._investigate(self._investigate_script(healthy)))[-1]
        self.assertIsNone(done["repair"])
        resp = self.client.post("/api/repair")
        self.assertEqual(resp.status_code, 400)

    def test_empty_symptom_rejected(self):
        resp = self.client.post("/api/investigate", json={"symptom": "  "})
        self.assertEqual(resp.status_code, 400)

    def test_followup_and_repair_need_an_investigation_first(self):
        self.assertEqual(
            self.client.post("/api/followup", json={"question": "why?"}).status_code,
            400)
        self.assertEqual(self.client.post("/api/repair").status_code, 400)

    def test_busy_requests_are_rejected(self):
        server._state["busy"] = True
        try:
            resp = self.client.post("/api/investigate", json={"symptom": "x"})
            self.assertEqual(resp.status_code, 409)
        finally:
            server._state["busy"] = False

    def test_missing_api_key_is_an_error_event(self):
        env = dict(os.environ)
        env.pop("MODEL_API_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            resp = self.client.post("/api/investigate", json={"symptom": "x"})
        events = _events(resp)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "error")
        self.assertIn("MODEL_API_KEY", events[0]["message"])

    def test_server_never_touches_fault_ground_truth(self):
        source = Path(server.__file__).read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("faults", alias.name)
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn("faults", node.module or "")
                for alias in node.names:
                    self.assertNotIn("faults", alias.name)
        html = (Path(server.__file__).parent / "index.html").read_text()
        self.assertNotIn("faults", html)


if __name__ == "__main__":
    unittest.main()
