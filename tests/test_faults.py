#!/usr/bin/env python3
"""Unit tests for the inject health gate. No model, docker or lab needed.

Run from the repo root: python3 -m unittest tests.test_faults -v
"""
from __future__ import annotations

import io
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import faults


def _uploads_ok(writable=True, tmp_writable=True):
    return {"path": "/srv/lab-data/uploads", "owner_uid": 0 if not writable else 1000,
            "owner_gid": 1000, "mode": "0755", "app_user_can_write": writable,
            "tmp_path": "/srv/lab-data/tmp", "tmp_owner_uid": 0 if not tmp_writable else 1000,
            "tmp_owner_gid": 1000, "tmp_mode": "0755", "tmp_app_user_can_write": tmp_writable}


def _app_nets(*networks):
    return {"NetworkSettings": {"Networks": {n: {} for n in networks}}}


def _volume_ok(percent=3.8, avail=434.0):
    return {"path": "/srv/lab-data", "size_mb": 487.2, "used_mb": 17.3,
            "available_mb": avail, "use_percent": percent,
            "tmp_part_files": 0, "tmp_part_mb": 0.0, "tmp_part_stale_files": 0}


def _db_ok(running=True, paused=False, health="healthy", status="running",
           networks=("incidentlab_default",)):
    return {"name": faults.DB, "status": status, "running": running, "paused": paused,
            "health": health, "started_at": "x", "networks": list(networks)}


class TestInjectGate(unittest.TestCase):
    def test_refuses_when_uploads_not_writable(self):
        with (patch.object(faults, "status_uploads", return_value=_uploads_ok(False)),
              patch.object(faults, "status_volume", return_value=_volume_ok()),
              patch.object(faults, "status_database", return_value=_db_ok()),
              patch.object(faults, "_inspect_container",
                           return_value=_app_nets("incidentlab_default")),
              patch.object(faults, "_run_verifier") as verifier,
              patch.object(faults, "_docker") as helper):
            with self.assertRaisesRegex(RuntimeError, "refusing to inject"):
                faults.inject("upload_dir_not_writable")
            verifier.assert_not_called()
            helper.assert_not_called()

    def test_refuses_when_tmp_not_writable(self):
        with (patch.object(faults, "status_uploads", return_value=_uploads_ok(True, False)),
              patch.object(faults, "status_volume", return_value=_volume_ok()),
              patch.object(faults, "status_database", return_value=_db_ok()),
              patch.object(faults, "_inspect_container",
                           return_value=_app_nets("incidentlab_default")),
              patch.object(faults, "_run_verifier") as verifier,
              patch.object(faults, "_docker") as helper):
            with self.assertRaisesRegex(RuntimeError, "tmp/ not writable"):
                faults.inject("P3")
            verifier.assert_not_called()
            helper.assert_not_called()

    def test_refuses_when_volume_full(self):
        with (patch.object(faults, "status_uploads", return_value=_uploads_ok()),
              patch.object(faults, "status_volume",
                           return_value=_volume_ok(percent=100.0, avail=0.2)),
              patch.object(faults, "status_database", return_value=_db_ok()),
              patch.object(faults, "_inspect_container",
                           return_value=_app_nets("incidentlab_default")),
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
              patch.object(faults, "_inspect_container",
                           return_value=_app_nets("incidentlab_default")),
              patch.object(faults, "_run_verifier") as verifier,
              patch.object(faults, "_docker_cmd") as cmd):
            with self.assertRaisesRegex(RuntimeError, "not running"):
                faults.inject("database_unavailable")
            verifier.assert_not_called()
            cmd.assert_not_called()

    def test_refuses_when_db_shares_no_network(self):
        with (patch.object(faults, "status_uploads", return_value=_uploads_ok()),
              patch.object(faults, "status_volume", return_value=_volume_ok()),
              patch.object(faults, "status_database", return_value=_db_ok(networks=())),
              patch.object(faults, "_inspect_container",
                           return_value=_app_nets("incidentlab_default")),
              patch.object(faults, "_run_verifier") as verifier,
              patch.object(faults, "_docker_cmd") as cmd):
            with self.assertRaisesRegex(RuntimeError, "shares no network"):
                faults.inject("D3")
            verifier.assert_not_called()
            cmd.assert_not_called()

    def test_refuses_when_verify_fails(self):
        with (patch.object(faults, "status_uploads", return_value=_uploads_ok()),
              patch.object(faults, "status_volume", return_value=_volume_ok()),
              patch.object(faults, "status_database", return_value=_db_ok()),
              patch.object(faults, "_inspect_container",
                           return_value=_app_nets("incidentlab_default")),
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
              patch.object(faults, "_inspect_container",
                           return_value=_app_nets("incidentlab_default")),
              patch.object(faults, "_run_verifier", return_value=(True, "OK")),
              patch.object(faults, "_docker") as helper):
            with patch("builtins.print"):
                faults.inject("upload_dir_not_writable")
            self.assertTrue(helper.called)

    def test_proceeds_database_when_healthy(self):
        with (patch.object(faults, "status_uploads", return_value=_uploads_ok()),
              patch.object(faults, "status_volume", return_value=_volume_ok()),
              patch.object(faults, "status_database", return_value=_db_ok()),
              patch.object(faults, "_inspect_container",
                           return_value=_app_nets("incidentlab_default")),
              patch.object(faults, "_run_verifier", return_value=(True, "OK")),
              patch.object(faults, "_docker_cmd") as cmd):
            with patch("builtins.print"):
                faults.inject("database_unavailable")
            cmd.assert_called_once_with("stop", faults.DB)

    def test_unknown_variant_still_value_error(self):
        with patch.object(faults, "status_uploads") as uploads:
            with self.assertRaises(ValueError):
                faults.inject("nope")
            uploads.assert_not_called()

    def test_listed_variant_without_branch_raises(self):
        with (patch.object(faults, "VARIANTS", faults.VARIANTS + ["future_variant"]),
              patch.object(faults, "status_uploads", return_value=_uploads_ok()),
              patch.object(faults, "status_volume", return_value=_volume_ok()),
              patch.object(faults, "status_database", return_value=_db_ok()),
              patch.object(faults, "_inspect_container",
                           return_value=_app_nets("incidentlab_default")),
              patch.object(faults, "_run_verifier", return_value=(True, "OK")),
              patch.object(faults, "_docker_cmd") as cmd,
              patch.object(faults, "_docker") as helper):
            with self.assertRaisesRegex(RuntimeError, "no inject branch"):
                faults.inject("future_variant")
            cmd.assert_not_called()
            helper.assert_not_called()


class TestVariantDispatch(unittest.TestCase):
    INJECT_TARGETS = {
        "V1": "_inject_v1", "V2": "_inject_v2", "V3": "_inject_v3",
        "P1": "_docker", "P2": "_docker", "P3": "_docker",
        "D1": "_docker_cmd", "D2": "_docker_cmd", "D3": "_inject_d3",
        "H2": "_setup_h2", "H3": "_setup_h3",
        "volume_full": "_inject_v1",
        "upload_dir_not_writable": "_docker",
        "database_unavailable": "_docker_cmd",
    }

    def test_inject_dispatch_matrix(self):
        mocks = {}
        for name in set(self.INJECT_TARGETS.values()):
            patcher = patch.object(faults, name)
            mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        with (patch.object(faults, "status_uploads", return_value=_uploads_ok()),
              patch.object(faults, "status_volume", return_value=_volume_ok()),
              patch.object(faults, "status_database", return_value=_db_ok()),
              patch.object(faults, "_inspect_container",
                           return_value=_app_nets("incidentlab_default")),
              patch.object(faults, "_run_verifier", return_value=(True, "OK")),
              patch("builtins.print")):
            for variant, target in self.INJECT_TARGETS.items():
                with self.subTest(variant=variant):
                    for mock in mocks.values():
                        mock.reset_mock()
                    faults.inject(variant)
                    mocks[target].assert_called()
                    for name, mock in mocks.items():
                        if name != target:
                            mock.assert_not_called()

    def test_inject_h1_confirms_clean(self):
        with (patch.object(faults, "status_uploads", return_value=_uploads_ok()),
              patch.object(faults, "status_volume", return_value=_volume_ok()),
              patch.object(faults, "status_database", return_value=_db_ok()),
              patch.object(faults, "_inspect_container",
                           return_value=_app_nets("incidentlab_default")),
              patch.object(faults, "_run_verifier", return_value=(True, "OK")),
              patch.object(faults, "_docker") as helper,
              patch.object(faults, "_docker_cmd") as cmd,
              patch("builtins.print") as echoed):
            faults.inject("H1")
            helper.assert_not_called()
            cmd.assert_not_called()
            echoed.assert_called_once()
            self.assertIn("confirmed clean", echoed.call_args[0][0])

    def test_reset_dispatch_matrix(self):
        targets = {**dict.fromkeys(
            ("V1", "V2", "V3", "volume_full"), "_reset_volume_full"),
            **dict.fromkeys(
                ("P1", "P2", "P3", "upload_dir_not_writable"), "_docker"),
            **dict.fromkeys(
                ("D1", "D2", "D3", "database_unavailable"), "_reset_database")}
        mocks = {}
        for name in ("_reset_volume_full", "_docker", "_reset_database"):
            patcher = patch.object(faults, name)
            mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        with (patch.object(faults, "_wait_app_healthy"),
              patch("builtins.print")):
            for variant, target in targets.items():
                with self.subTest(variant=variant):
                    for mock in mocks.values():
                        mock.reset_mock()
                    faults.reset(variant)
                    mocks[target].assert_called()
                    for name, mock in mocks.items():
                        if name != target:
                            mock.assert_not_called()
            for variant in ("H1", "H2", "H3"):
                with self.subTest(variant=variant):
                    for mock in mocks.values():
                        mock.reset_mock()
                    faults.reset(variant)
                    for mock in mocks.values():
                        mock.assert_not_called()

    def test_reset_all_runs_every_family_then_waits(self):
        with (patch.object(faults, "_reset_volume_full") as volume,
              patch.object(faults, "_reset_upload_dirs") as perms,
              patch.object(faults, "_reset_database") as db,
              patch.object(faults, "_wait_app_healthy") as wait,
              patch("builtins.print")):
            faults.reset("all")
        volume.assert_called_once_with()
        perms.assert_called_once_with()
        db.assert_called_once_with()
        wait.assert_called_once_with()

    def test_reset_all_collects_failures(self):
        with (patch.object(faults, "_reset_volume_full",
                           side_effect=RuntimeError("boom")),
              patch.object(faults, "_reset_upload_dirs") as perms,
              patch.object(faults, "_reset_database") as db,
              patch.object(faults, "_wait_app_healthy") as wait,
              patch("builtins.print")):
            with self.assertRaisesRegex(RuntimeError, "reset incomplete"):
                faults.reset("all")
        perms.assert_called_once_with()
        db.assert_called_once_with()
        wait.assert_not_called()

    def test_reset_unknown_variant_raises(self):
        with self.assertRaises(ValueError):
            faults.reset("nope")

    def test_status_healthy_setup_raises(self):
        with self.assertRaisesRegex(ValueError, "run the verifier"):
            faults.status("H2")

    def test_status_unknown_variant_raises(self):
        with self.assertRaises(ValueError):
            faults.status("nope")


class TestHealthySetupLogChecks(unittest.TestCase):
    def test_h2_reads_merged_logs(self):
        with (patch.object(faults, "_docker_cmd",
                           return_value="OperationalError: connection failed") as cmd,
              patch.object(faults, "_hit_health"),
              patch.object(faults, "_run_verifier", return_value=(False, "x")),
              patch.object(faults, "_wait_db_healthy"),
              patch.object(faults, "_wait_app_healthy")):
            faults._setup_h2()
        log_calls = [c for c in cmd.call_args_list if c.args[:1] == ("logs",)]
        self.assertEqual(len(log_calls), 1)
        self.assertTrue(log_calls[0].kwargs.get("merge_stderr"))

    def test_h3_reads_merged_logs(self):
        with (patch.object(faults, "_docker_cmd",
                           return_value="OSError: No space left on device") as cmd,
              patch.object(faults, "_inject_v1"),
              patch.object(faults, "_run_verifier", return_value=(False, "x")),
              patch.object(faults, "_run_child", return_value=(True, "OK")),
              patch.object(faults, "_wait_app_healthy")):
            faults._setup_h3()
        log_calls = [c for c in cmd.call_args_list if c.args[:1] == ("logs",)]
        self.assertEqual(len(log_calls), 1)
        self.assertTrue(log_calls[0].kwargs.get("merge_stderr"))


class TestResetWaitsForHealth(unittest.TestCase):
    def test_reset_waits_after_fix(self):
        with (patch.object(faults, "_docker"),
              patch.object(faults, "_wait_app_healthy") as wait,
              patch("builtins.print")):
            faults.reset("upload_dir_not_writable")
        wait.assert_called_once_with()

    def test_reset_fails_when_unhealthy(self):
        with (patch.object(faults, "_docker"),
              patch.object(faults, "_wait_app_healthy",
                           side_effect=RuntimeError("app still unhealthy after 60s")),
              patch("builtins.print") as echoed):
            with self.assertRaisesRegex(RuntimeError, "still unhealthy"):
                faults.reset("upload_dir_not_writable")
            echoed.assert_not_called()

    def test_wait_returns_on_200(self):
        ok = MagicMock()
        ok.__enter__.return_value.status = 200
        with patch("lab.faults.urllib.request.urlopen", return_value=ok):
            faults._wait_app_healthy()

    def test_wait_fails_after_timeout(self):
        err = urllib.error.HTTPError("http://x/health", 503, "x", {}, io.BytesIO(b""))
        self.addCleanup(err.close)
        with (patch("lab.faults.urllib.request.urlopen", side_effect=err),
              patch.object(faults, "HEALTH_TIMEOUT", 0)):
            with self.assertRaisesRegex(RuntimeError, "status 503"):
                faults._wait_app_healthy()


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
