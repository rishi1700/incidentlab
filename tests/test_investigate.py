#!/usr/bin/env python3
"""Unit tests for stateless history replay. No model, docker or lab needed.

Run from the repo root: python3 -m unittest tests.test_investigate -v
"""
from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import openai  # noqa: F401
except ImportError:
    fake = types.ModuleType("openai")
    fake.OpenAI = object   # only referenced at runtime, never in these tests
    sys.modules["openai"] = fake

import investigate


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
        self.calls = []

    def create(self, **kwargs):
        # Snapshot: the caller keeps extending the history list afterwards.
        snapshot = dict(kwargs)
        snapshot["input"] = [dict(item) for item in kwargs["input"]]
        self.calls.append(snapshot)
        return self.script.pop(0)


class FakeClient:
    def __init__(self, script):
        self.responses = FakeResponses(script)


def _resp(resp_id, output, text=""):
    return SimpleNamespace(id=resp_id, output=output, output_text=text, usage=None)


REASONING = {"id": "rs-1", "type": "reasoning", "summary": [{"type": "summary_text", "text": "hmm"}],
             "encrypted_content": "enc-1"}
CALL = {"id": "fc-1", "type": "function_call", "call_id": "call-1",
        "name": "app_health", "arguments": "{}"}
MESSAGE = {"id": "msg-1", "type": "message", "status": "completed", "role": "assistant",
           "phase": "commentary", "content": [{"type": "output_text", "text": "checking"}]}


class TestStatelessReplay(unittest.TestCase):
    def _investigate(self, script):
        client = FakeClient(script)
        inv = investigate.Investigation(client)
        with (patch.object(investigate, "run_check", return_value={"status_code": 200}),
              patch("builtins.print")):
            inv.investigate("symptom")
        return inv, client

    def test_second_request_contains_first_response_items(self):
        report = _resp("r3", [], text='{"cause": "healthy"}')
        _, client = self._investigate([
            _resp("r1", [FakeItem(REASONING), FakeItem(CALL)]),
            _resp("r2", [FakeItem(MESSAGE)]),
            report,
        ])
        first, second, third = (call["input"] for call in client.responses.calls)
        self.assertEqual(first, [{"role": "user", "content": "symptom"}])
        # Reasoning verbatim (encrypted_content and summary), then the call,
        # then our function_call_output for it.
        self.assertEqual(second[1], REASONING)
        self.assertEqual(second[2], CALL)
        self.assertEqual(second[3]["type"], "function_call_output")
        self.assertEqual(second[3]["call_id"], "call-1")
        # Report request sees the whole history plus the report prompt.
        self.assertEqual(third[-1]["content"], investigate.REPORT_REQUEST)
        self.assertIn(REASONING, third)
        # Assistant message replayed verbatim, phase included.
        self.assertIn(MESSAGE, third)

    def test_requests_are_stateless(self):
        _, client = self._investigate([
            _resp("r1", [FakeItem(CALL)]),
            _resp("r2", [FakeItem(MESSAGE)]),
            _resp("r3", [], text='{"cause": "healthy"}'),
        ])
        for call in client.responses.calls:
            self.assertIs(call["store"], False)
            self.assertEqual(call["include"], ["reasoning.encrypted_content"])
            self.assertNotIn("previous_response_id", call)
            self.assertEqual(call["model"], investigate.MODEL)
            self.assertEqual(call["instructions"], investigate.INSTRUCTIONS)
        self.assertIn("tools", client.responses.calls[0])
        self.assertNotIn("tools", client.responses.calls[2])
        self.assertIn("text", client.responses.calls[2])

    def test_reasoning_only_turn_gets_filler_message(self):
        # No function calls, so the tool loop ends at once and the report
        # request is the second create.
        _, client = self._investigate([
            _resp("r1", [FakeItem(REASONING)]),
            _resp("r2", [], text='{"cause": "healthy"}'),
        ])
        second = client.responses.calls[1]["input"]
        self.assertEqual(second[1], REASONING)
        self.assertEqual(second[2], {"role": "assistant", "content": "Understood."})
        self.assertEqual(second[-1]["content"], investigate.REPORT_REQUEST)

    def test_follow_up_uses_same_history(self):
        client = FakeClient([
            _resp("r1", [FakeItem(CALL)]),
            _resp("r2", [FakeItem(MESSAGE)]),
            _resp("r3", [], text='{"cause": "healthy"}'),
            _resp("r4", [FakeItem(MESSAGE)]),
        ])
        inv = investigate.Investigation(client)
        with (patch.object(investigate, "run_check", return_value={"status_code": 200}),
              patch("builtins.print")):
            inv.investigate("symptom")
            inv.follow_up("why?")
        followup = client.responses.calls[3]["input"]
        self.assertEqual(followup[-1], {"role": "user", "content": "why?"})
        self.assertIn(CALL, followup)
        self.assertNotIn("previous_response_id", client.responses.calls[3])


class TestOnCheck(unittest.TestCase):
    def _investigate(self, script, **kwargs):
        events = []
        client = FakeClient(script)
        inv = investigate.Investigation(client, on_check=events.append, **kwargs)
        with (patch.object(investigate, "run_check", return_value={"status_code": 200}),
              patch("builtins.print")):
            inv.investigate("symptom")
        return events

    def test_off_by_default(self):
        inv = investigate.Investigation(FakeClient([]))
        self.assertIsNone(inv.on_check)

    def test_called_once_per_check_with_evidence_id(self):
        events = self._investigate([
            _resp("r1", [FakeItem(CALL)]),
            _resp("r2", [FakeItem(MESSAGE)]),
            _resp("r3", [], text='{"cause": "healthy"}'),
        ])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["evidence_id"], "E1")
        self.assertEqual(events[0]["check"], "app_health")
        self.assertEqual(events[0]["arguments"], "{}")
        self.assertTrue(events[0]["ok"])
        self.assertEqual(events[0]["data"], {"status_code": 200})

    def test_reports_check_errors(self):
        events = []
        client = FakeClient([
            _resp("r1", [FakeItem(CALL)]),
            _resp("r2", [FakeItem(MESSAGE)]),
            _resp("r3", [], text='{"cause": "healthy"}'),
        ])
        inv = investigate.Investigation(client, on_check=events.append)
        with (patch.object(investigate, "run_check",
                           side_effect=investigate.CheckError("nope")),
              patch("builtins.print")):
            inv.investigate("symptom")
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["ok"])
        self.assertEqual(events[0]["data"], {"error": "nope"})

    def test_budget_reached_emits_nothing(self):
        events = []
        inv = investigate.Investigation(FakeClient([]), on_check=events.append)
        inv.checks_in_loop = investigate.MAX_CHECKS
        call = SimpleNamespace(name="app_health", arguments="{}", call_id="call-1")
        with patch("builtins.print"):
            inv._answer(call)
        self.assertEqual(events, [])
        self.assertEqual(inv.evidence, {})


class TestFollowupStructured(unittest.TestCase):
    def test_default_followup_still_returns_text(self):
        client = FakeClient([_resp("r1", [FakeItem(MESSAGE)], text="plain")])
        inv = investigate.Investigation(client)
        with patch("builtins.print"):
            self.assertEqual(inv.follow_up("why?"), "plain")
        self.assertNotIn("text", client.responses.calls[0])

    def test_structured_answer_uses_schema(self):
        answer = {"answer": "the disk is full",
                  "citations": [{"evidence_id": "E1", "field": "status_code",
                                 "value": "200"}]}
        client = FakeClient([
            _resp("r1", [FakeItem(CALL)]),
            _resp("r2", [FakeItem(MESSAGE)]),
            _resp("r3", [], text=json.dumps(answer)),
        ])
        inv = investigate.Investigation(client)
        with (patch.object(investigate, "run_check", return_value={"status_code": 200}),
              patch("builtins.print")):
            result = inv.follow_up("why?", structured=True)
        self.assertEqual(result, answer)
        schema_call = client.responses.calls[2]
        fmt = schema_call["text"]["format"]
        self.assertEqual(fmt["name"], "followup_answer")
        self.assertIs(fmt["schema"], investigate.FOLLOWUP_SCHEMA)
        self.assertEqual(schema_call["input"][-1]["content"],
                         investigate.FOLLOWUP_ANSWER_REQUEST)
        # Citations reuse the report's evidence_id/field/value shape.
        self.assertIs(investigate.FOLLOWUP_SCHEMA["properties"]["citations"]["items"],
                      investigate._CITATION)


if __name__ == "__main__":
    unittest.main()
