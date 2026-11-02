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
import queue
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

ANSWER = {"answer": "the disk filled up",
          "citations": [{"evidence_id": "E1", "field": "status_code", "value": "200"},
                        {"evidence_id": "E9", "field": "x", "value": "y"}]}


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
        self.assertEqual(check["summary"], "200")
        done = events[1]
        self.assertEqual(done["report"]["cause"], "volume_full")
        self.assertEqual(done["repair"], "clear_stale_temp_files")
        self.assertEqual(done["validation"]["supported"], 1)
        self.assertEqual(done["validation"]["total"], 1)
        row = done["validation"]["citations"][0]
        self.assertEqual(row["citation"], REPORT["findings"][0]["citation"])

    def test_followup_streams_validated_citations(self):
        script = (self._investigate_script()
                  + [_resp("r4", [FakeItem(MESSAGE)]),
                     _resp("r5", [], text=json.dumps(ANSWER))])
        key, client, checks, quiet = self._fake_model(script)
        with key, client, checks, quiet:
            self._events_check(self.client.post(
                "/api/investigate", json={"symptom": "uploads stuck"}))
            resp = self.client.post("/api/followup", json={"question": "why?"})
        events = _events(resp)
        self.assertEqual([e["type"] for e in events], ["answer"])
        self.assertEqual(events[0]["text"], "the disk filled up")
        cites = events[0]["citations"]
        self.assertEqual(len(cites), 2)
        self.assertTrue(cites[0]["supported"])
        self.assertEqual(cites[0]["citation"]["evidence_id"], "E1")
        self.assertFalse(cites[1]["supported"])
        self.assertIn("E9", cites[1]["detail"])

    def test_emit_check_falls_back_when_summary_fails(self):
        events = queue.Queue()
        with patch.object(server, "summarize_check", side_effect=ValueError("x")):
            server._emit_check(events, {"evidence_id": "E1", "check": "app_health",
                                        "arguments": "{}", "ok": True, "data": {}})
        self.assertEqual(events.get()["summary"], "result recorded")

    def test_page_has_polish_wiring(self):
        html = (Path(server.__file__).parent / "index.html").read_text()
        for needle in ("trail-sum", "cite-link", "gotoEvidence", "mark.hit",
                       'id="status"', '"ev-" +',
                       "JSON.stringify(raw)"):
            self.assertIn(needle, html)

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

    def test_ruled_out_rows_carry_their_citations(self):
        report = dict(REPORT, ruled_out=[
            {"cause": "database_unavailable", "reason": "db is up",
             "citation": {"evidence_id": "E1", "field": "status_code", "value": "200"}}])
        done = _events(self._investigate(self._investigate_script(report)))[-1]
        rows = done["validation"]["citations"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["citation"], report["ruled_out"][0]["citation"])

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


@unittest.skipUnless(HAS_WEB, "needs fastapi and httpx: run with .venv/bin/python")
class TestSummarizeCheck(unittest.TestCase):
    def test_each_check_has_a_plain_line(self):
        cases = [
            ("list_containers",
             {"containers": [{"name": "incidentlab-app-1", "state": "running"},
                             {"name": "incidentlab-db-1", "state": "exited"}]},
             True, "app: running, db: exited"),
            ("list_containers", {"containers": []}, True, "no containers"),
            ("inspect_container",
             {"name": "incidentlab-db-1", "status": "running", "health": "healthy"},
             True, "db: running, healthy"),
            ("container_logs", {"lines": ["a", "b"], "older_lines_omitted": 5},
             True, "2 lines, 5 older omitted"),
            ("container_logs", {"lines": [], "older_lines_omitted": 0},
             True, "0 lines"),
            ("disk_usage",
             {"use_percent": 9.1, "available_mb": 410.2, "inode_use_percent": 3.0},
             True, "9.1% used, 410.2 MB free"),
            ("disk_usage",
             {"use_percent": 9.1, "available_mb": 410.2, "inode_use_percent": 87.0},
             True, "9.1% used, 410.2 MB free, inodes 87.0% used"),
            ("path_access",
             {"path": "/srv/lab-data/uploads", "mode": "0755",
              "owner_uid": 0, "owner_gid": 0, "app_user_can_write": False},
             True, "uploads: app user cannot write (0755 0:0)"),
            ("recent_events", {"minutes": 15, "events": []},
             True, "no events in last 15 min"),
            ("recent_events",
             {"minutes": 15, "events": [
                 {"container": "incidentlab-app-1", "action": "start"},
                 {"container": "incidentlab-db-1", "action": "die"}]},
             True, "2 events in last 15 min, latest db die"),
            ("app_health", {"status_code": 200, "body": "ok"}, True, "200 ok"),
            ("app_health", {"status_code": 503, "body": "unhealthy"},
             True, "503 unhealthy"),
            ("app_health", {"status_code": None, "error": "refused"},
             True, "unreachable (refused)"),
            ("disk_usage", {"error": "boom"}, False, "failed: boom"),
        ]
        for name, data, ok, want in cases:
            with self.subTest(name=name, data=data):
                self.assertEqual(server.summarize_check(name, data, ok), want)

    def test_unknown_check_falls_back_to_json(self):
        self.assertEqual(server.summarize_check("new_check", {"a": 1}, True),
                         '{"a": 1}')


if __name__ == "__main__":
    unittest.main()
