#!/usr/bin/env python3
"""Unit tests for the eval runner's guard rails. No model, docker or lab needed.

Run from the repo root: python3 -m unittest tests.test_run -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

try:
    import openai  # noqa: F401
except ImportError:
    fake = types.ModuleType("openai")
    fake.OpenAI = object   # only referenced at runtime, never in these tests
    sys.modules["openai"] = fake

import run as run_module


class TestApiKey(unittest.TestCase):
    def _without_key(self):
        env = dict(os.environ)
        env.pop("MODEL_API_KEY", None)
        return patch.dict(os.environ, env, clear=True)

    def test_missing_key(self):
        with self._without_key():
            msg = run_module._check_api_key()
        self.assertIn("not set", msg)

    def test_empty_key(self):
        with patch.dict(os.environ, {"MODEL_API_KEY": ""}):
            self.assertIn("not set", run_module._check_api_key())

    def test_clean_key(self):
        with patch.dict(os.environ, {"MODEL_API_KEY": "sk-valid-key-123"}):
            self.assertIsNone(run_module._check_api_key())

    def test_bad_key_rejected_without_printing_it(self):
        bad_bits = (" ", "\t", "\n", "\r\n", " a", "a ", "a\x1bb", "a\x7fb")
        for bit in bad_bits:
            with self.subTest(bit=repr(bit)):
                with patch.dict(os.environ, {"MODEL_API_KEY": f"CANARY{bit}CANARY"}):
                    msg = run_module._check_api_key()
            self.assertIn("whitespace or control", msg)
            self.assertNotIn("CANARY", msg)

    def test_null_byte_rejected(self):
        # os.environ cannot hold \x00, so stub .get instead of patch.dict.
        with patch.object(os.environ, "get", return_value="CANARY\x00CANARY"):
            msg = run_module._check_api_key()
        self.assertIn("whitespace or control", msg)
        self.assertNotIn("CANARY", msg)


class TestModelErrorStop(unittest.TestCase):
    def test_two_in_a_row_stops(self):
        stop = run_module._ModelErrorStop()
        self.assertFalse(stop.note({"stage": "model", "harness_error": "x"}))
        self.assertTrue(stop.note({"stage": "model", "harness_error": "y"}))

    def test_completed_run_resets(self):
        stop = run_module._ModelErrorStop()
        stop.note({"stage": "model", "harness_error": "x"})
        self.assertFalse(stop.note({"case": "V1", "run": 1}))
        self.assertFalse(stop.note({"stage": "model", "harness_error": "y"}))

    def test_other_stage_resets(self):
        stop = run_module._ModelErrorStop()
        stop.note({"stage": "model", "harness_error": "x"})
        self.assertFalse(stop.note({"stage": "setup", "harness_error": "x"}))
        self.assertFalse(stop.note({"stage": "model", "harness_error": "y"}))


class TestLoadRecorded(unittest.TestCase):
    def test_retries_harness_errors(self):
        lines = [
            {"case": "V1", "run": 1},
            {"case": "V2", "run": 1, "harness_error": "boom", "stage": "setup"},
            {"case": "P1", "run": 1, "harness_error": "boom", "stage": "model"},
            {"case": "P1", "run": 1},
            {"case": "D1", "run": 1, "no_model": True},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "runs.jsonl"
            with open(path, "w") as f:
                for record in lines:
                    f.write(json.dumps(record) + "\n")
            done = run_module._load_recorded(path)
        self.assertEqual(done, {("V1", 1), ("P1", 1)})


if __name__ == "__main__":
    unittest.main()
