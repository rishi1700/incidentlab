#!/usr/bin/env python3
"""Unit tests for the chained-request retry. No model, docker or lab needed.

Run from the repo root: python3 -m unittest tests.test_investigate -v
"""
from __future__ import annotations

import io
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


class FakeChainError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class FakeResponses:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, script):
        self.responses = FakeResponses(script)


def _ok_resp(resp_id="resp-2"):
    return SimpleNamespace(id=resp_id, usage=None)


CHAIN_MSG = "referenced response not found or expired"


class TestChainRetry(unittest.TestCase):
    def _run(self, script, last_response_id="resp-1"):
        client = FakeClient(script)
        inv = investigate.Investigation(client)
        inv.last_response_id = last_response_id
        stderr, sleeps = io.StringIO(), []
        with (patch.object(sys, "stderr", stderr),
              patch("investigate.time.sleep", side_effect=sleeps.append)):
            try:
                result = inv._create([{"role": "user", "content": "hi"}])
            except Exception as err:   # noqa: BLE001 - asserted by callers
                return inv, client, stderr, sleeps, err
        return inv, client, stderr, sleeps, result

    def test_retries_then_succeeds(self):
        inv, client, stderr, sleeps, result = self._run(
            [FakeChainError(CHAIN_MSG), FakeChainError(CHAIN_MSG), _ok_resp()])
        self.assertEqual(result.id, "resp-2")
        self.assertEqual(inv.chain_retries, 2)
        self.assertEqual(inv.last_response_id, "resp-2")
        self.assertEqual(sleeps, [1, 2])
        self.assertEqual(len(stderr.getvalue().strip().splitlines()), 2)
        for kwargs in client.responses.calls:
            self.assertEqual(kwargs["previous_response_id"], "resp-1")
        self.assertEqual(len(client.responses.calls), 3)

    def test_gives_up_after_four_retries(self):
        inv, client, stderr, sleeps, err = self._run([FakeChainError(CHAIN_MSG)] * 5)
        self.assertIsInstance(err, FakeChainError)
        self.assertEqual(inv.chain_retries, 4)
        self.assertEqual(inv.last_response_id, "resp-1")
        self.assertEqual(sleeps, [1, 2, 4, 8])
        self.assertEqual(len(stderr.getvalue().strip().splitlines()), 4)
        self.assertEqual(len(client.responses.calls), 5)

    def test_first_request_never_retries(self):
        inv, client, stderr, sleeps, err = self._run(
            [FakeChainError(CHAIN_MSG)], last_response_id=None)
        self.assertIsInstance(err, FakeChainError)
        self.assertEqual(inv.chain_retries, 0)
        self.assertEqual(sleeps, [])
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn("previous_response_id", client.responses.calls[0])

    def test_other_400_never_retries(self):
        inv, client, stderr, sleeps, err = self._run(
            [FakeChainError("invalid JSON in input")])
        self.assertIsInstance(err, FakeChainError)
        self.assertEqual(inv.chain_retries, 0)
        self.assertEqual(sleeps, [])
        self.assertEqual(len(client.responses.calls), 1)

    def test_non_400_never_retries(self):
        inv, client, stderr, sleeps, err = self._run(
            [FakeChainError(CHAIN_MSG, status_code=500)])
        self.assertIsInstance(err, FakeChainError)
        self.assertEqual(inv.chain_retries, 0)
        self.assertEqual(sleeps, [])


if __name__ == "__main__":
    unittest.main()
