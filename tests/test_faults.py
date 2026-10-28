#!/usr/bin/env python3
"""Unit tests for the inject health gate. No model, docker or lab needed.

Run from the repo root: python3 -m unittest tests.test_faults -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import faults


def _uploads_ok(writable=True):
    return {"path": "/srv/lab-data/uploads", "owner_uid": 0 if not writable else 1000,
            "owner_gid": 1000, "mode": "0755", "app_user_can_write": writable}


def _volume_ok(percent=3.8, avail=434.0):
    return {"path": "/srv/lab-data", "size_mb": 487.2, "used_mb": 17.3,
            "available_mb": avail, "use_percent": percent,
            "tmp_part_files": 0, "tmp_part_mb": 0.0, "tmp_part_stale_files": 0}


def _db_ok(running=True, paused=False, health="healthy", status="running"):
    return {"name": faults.DB, "status": status, "running": running, "paused": paused,
            "health": health, "started_at": "x", "networks": ["incidentlab_default"]}


class TestInjectGate(unittest.TestCase):
    def test_refuses_when_uploads_not_writable(self):
        with (patch.object(faults, "status_uploads", return_value=_uploads_ok(False)),
              patch.object(faults, "status_volume", return_value=_volume_ok()),
              patch.object(faults, "status_database", return_value=_db_ok()),
              patch.object(faults, "_run_verifier") as verifier,
              patch.object(faults, "_docker") as helper):
            with self.assertRaisesRegex(RuntimeError, "refusing to inject"):
                faults.inject("upload_dir_not_writable")
            verifier.assert_not_called()
            helper.assert_not_called()

    def test_refuses_when_volume_full(self):
        with (patch.object(faults, "status_uploads", return_value=_uploads_ok()),
              patch.object(faults, "status_volume",
                           return_value=_volume_ok(percent=100.0, avail=0.2)),
              patch.object(faults, "status_database", return_value=_db_ok()),
              patch.object(faults, "_run_verifier") as verifier,
              patch.object(faults, "_docker") as helper):
            with self.assertRaisesRegex(RuntimeError, "100.0% full"):
                faults.inject("volume_full")
            verifier.assert_not_called()
            helper.assert_not_called()

    def test_refuses_when_db_down(self):
        with (patch.object(faults, "status_uploads", return_value=_uploads_ok()),
              patch.object(faults, "status_volume", return_value=_volume_ok()),
              patch.object(faults, "status_database",
                           return_value=_db_ok(running=False, status="exited")),
              patch.object(faults, "_run_verifier") as verifier,
              patch.object(faults, "_docker_cmd") as cmd):
            with self.assertRaisesRegex(RuntimeError, "not running"):
                faults.inject("database_unavailable")
            verifier.assert_not_called()
            cmd.assert_not_called()

    def test_refuses_when_verify_fails(self):
        with (patch.object(faults, "status_uploads", return_value=_uploads_ok()),
              patch.object(faults, "status_volume", return_value=_volume_ok()),
              patch.object(faults, "status_database", return_value=_db_ok()),
              patch.object(faults, "_run_verifier",
                           return_value=(False, "UPLOAD FAILED: boom")),
              patch.object(faults, "_docker_cmd") as cmd):
            with self.assertRaisesRegex(RuntimeError, "pre-inject verify failed"):
                faults.inject("database_unavailable")
            cmd.assert_not_called()

    def test_proceeds_when_healthy(self):
        with (patch.object(faults, "status_uploads", return_value=_uploads_ok()),
              patch.object(faults, "status_volume", return_value=_volume_ok()),
              patch.object(faults, "status_database", return_value=_db_ok()),
              patch.object(faults, "_run_verifier", return_value=(True, "OK")),
              patch.object(faults, "_docker") as helper):
            with patch("builtins.print"):
                faults.inject("upload_dir_not_writable")
            self.assertTrue(helper.called)

    def test_proceeds_database_when_healthy(self):
        with (patch.object(faults, "status_uploads", return_value=_uploads_ok()),
              patch.object(faults, "status_volume", return_value=_volume_ok()),
              patch.object(faults, "status_database", return_value=_db_ok()),
              patch.object(faults, "_run_verifier", return_value=(True, "OK")),
              patch.object(faults, "_docker_cmd") as cmd):
            with patch("builtins.print"):
                faults.inject("database_unavailable")
            cmd.assert_called_once_with("stop", faults.DB)

    def test_unknown_fault_still_value_error(self):
        with patch.object(faults, "status_uploads") as uploads:
            with self.assertRaises(ValueError):
                faults.inject("nope")
            uploads.assert_not_called()


class TestRunVerifier(unittest.TestCase):
    def test_pass_reports_last_line(self):
        class Proc:
            returncode = 0
            stdout = "Verification: OK: hashes match\n"

        with patch("lab.faults.subprocess.run", return_value=Proc()):
            self.assertEqual(faults._run_verifier(), (True, "Verification: OK: hashes match"))

    def test_fail_reports_last_line(self):
        class Proc:
            returncode = 1
            stdout = "Verification: FAILED: UPLOAD FAILED: boom\n"

        with patch("lab.faults.subprocess.run", return_value=Proc()):
            ok, detail = faults._run_verifier()
        self.assertFalse(ok)
        self.assertIn("UPLOAD FAILED", detail)


if __name__ == "__main__":
    unittest.main()
