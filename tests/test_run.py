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
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

try:
    import openai  # noqa: F401
except ImportError:
    fake = types.ModuleType("openai")
    fake.OpenAI = object   # only referenced at runtime, never in these tests
    sys.modules["openai"] = fake

import run as run_module
from lab import faults as faults_module


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


class TestRunModelEnv(unittest.TestCase):
    def _model_json(self, tmpdir, unavailable):
        path = Path(tmpdir) / "model.json"
        path.write_text(json.dumps({
            "report": {"cause": "healthy"}, "validation": {"supported": 1, "total": 1, "warnings": []},
            "evidence": {"E1": {}}, "seconds": 1.0,
            "usage": {"requests": 1, "input": 1, "cached": 0, "output": 1}, "cost_usd": 0.001,
            "unavailable_checks": unavailable,
        }))
        return path

    def test_passes_unavailable_checks_and_verifies_json(self):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen.update(kwargs.get("env", {}))
            return SimpleNamespace(returncode=0)

        case = {"unavailable_checks": ["disk_usage", "container_logs"]}
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._model_json(tmpdir, ["container_logs", "disk_usage"])
            with patch("subprocess.run", side_effect=fake_run):
                model = run_module._run_model(case, path, 60)
        self.assertEqual(seen["INCIDENTLAB_UNAVAILABLE_CHECKS"], "disk_usage,container_logs")
        self.assertEqual(model["cause"], "healthy")

    def test_unavailable_mismatch_is_harness_error(self):
        def fake_run(cmd, **kwargs):
            return SimpleNamespace(returncode=0)

        case = {"unavailable_checks": ["disk_usage"]}
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._model_json(tmpdir, [])
            with patch("subprocess.run", side_effect=fake_run):
                with self.assertRaisesRegex(run_module.HarnessError, "mismatch"):
                    run_module._run_model(case, path, 60)


class TestProbeUpload(unittest.TestCase):
    def test_failed_probe_cleans_residue(self):
        with (patch.object(run_module, "post_upload", side_effect=Exception("boom")),
              patch.object(run_module, "delete_file") as delete,
              patch("os.listdir", side_effect=[["old.part"], ["old.part", "new.part"], ["old.part"]]),
              patch.object(faults_module, "_docker") as helper,
              patch("builtins.print")):
            run_module._probe_upload_must_fail("P3")
        helper.assert_called_once_with("rm", "-f", "/d/tmp/new.part")
        delete.assert_not_called()

    def test_no_residue_no_cleanup(self):
        with (patch.object(run_module, "post_upload", side_effect=Exception("boom")),
              patch.object(run_module, "delete_file") as delete,
              patch("os.listdir", side_effect=[[], []]),
              patch.object(faults_module, "_docker") as helper,
              patch("builtins.print")):
            run_module._probe_upload_must_fail("D1")
        helper.assert_not_called()
        delete.assert_not_called()

    def test_succeeded_probe_is_harness_error(self):
        with (patch.object(run_module, "post_upload", return_value={"id": "abc"}),
              patch.object(run_module, "delete_file") as delete,
              patch("os.listdir", side_effect=[[], []]),
              patch.object(faults_module, "_docker"),
              patch("builtins.print")):
            with self.assertRaisesRegex(run_module.HarnessError, "succeeded"):
                run_module._probe_upload_must_fail("P3")
        delete.assert_called_once_with("abc")

    def test_remaining_residue_is_harness_error(self):
        with (patch.object(run_module, "post_upload", side_effect=Exception("boom")),
              patch("os.listdir", side_effect=[[], ["stuck.part"], ["stuck.part"]]),
              patch.object(faults_module, "_docker"),
              patch("builtins.print")):
            with self.assertRaisesRegex(run_module.HarnessError, "residue remains"):
                run_module._probe_upload_must_fail("V3")


if __name__ == "__main__":
    unittest.main()
