#!/usr/bin/env python3
"""Unit tests for milestone 1 step 3. No model, docker or lab needed.

Run from the repo root: python3 -m unittest tests.test_repair -v
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import repair


class TestNoFaultKnowledge(unittest.TestCase):
    def test_no_faults_import(self):
        tree = ast.parse(Path(repair.__file__).read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module)
        self.assertTrue(
            all("faults" not in name for name in imported),
            f"repair.py must not import faults, got {imported}",
        )

    def test_reuses_lab_verify(self):
        source = Path(repair.__file__).read_text()
        self.assertIn("from lab.verify import", source)


class TestApproval(unittest.TestCase):
    def test_accepts_y_and_yes(self):
        for answer in ("y", "Y", "yes", "YES", "  y  "):
            with patch("builtins.input", return_value=answer):
                self.assertTrue(repair.ask_approval("r", "c"), answer)

    def test_declines_anything_else(self):
        for answer in ("", "n", "N", "no", "maybe", "yep"):
            with patch("builtins.input", return_value=answer):
                self.assertFalse(repair.ask_approval("r", "c"), repr(answer))

    def test_no_input_declines(self):
        with patch("builtins.input", side_effect=EOFError):
            self.assertFalse(repair.ask_approval("r", "c"))


class TestRepairCommand(unittest.TestCase):
    def test_touches_only_uploads_dir(self):
        cmds = []

        class Proc:
            returncode = 0
            stdout, stderr = "", ""

        with patch("repair.subprocess.run", side_effect=lambda c, **k: cmds.append(c) or Proc()):
            result = repair.restore_upload_dir_access()
        self.assertTrue(result["ok"])
        self.assertEqual(len(cmds), 2)
        for cmd in cmds:
            self.assertEqual(cmd[:4], ["docker", "run", "--rm", "-v"])
            self.assertNotIn("--name", cmd)
            self.assertFalse(any(a in ("-e", "--env", "--label", "-l") for a in cmd))
            self.assertTrue(cmd[-1].startswith("/d/uploads"), cmd)
        self.assertIn("chown", cmds[0])
        self.assertIn("1000:1000", cmds[0])
        self.assertIn("chmod", cmds[1])
        self.assertIn("755", cmds[1])


class TestVerifier(unittest.TestCase):
    def test_round_trip_ok(self):
        import lab.verify as v

        payload_hash = {}

        def fake_post(payload: bytes):
            import hashlib

            h = hashlib.sha256(payload).hexdigest()
            payload_hash["sent"] = payload
            return {"id": "ab" * 16, "sha256": h}

        with (
            patch.object(v, "post_upload", side_effect=fake_post),
            patch.object(v, "get_file", side_effect=lambda _: payload_hash["sent"]),
        ):
            result = repair.verify_recovery()
        self.assertTrue(result["ok"], result)

    def test_upload_failure_is_not_recovery(self):
        import lab.verify as v

        with patch.object(v, "post_upload", side_effect=ConnectionError("down")):
            result = repair.verify_recovery()
        self.assertFalse(result["ok"])
        self.assertIn("UPLOAD FAILED", result["detail"])


if __name__ == "__main__":
    unittest.main()
