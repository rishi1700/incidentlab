#!/usr/bin/env python3
"""Unit tests for the repairs. No model, docker or lab needed.

Run from the repo root: python3 -m unittest tests.test_repair -v
"""
from __future__ import annotations

import ast
import json
import sys
import tempfile
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
    def test_touches_only_upload_and_tmp_dirs(self):
        cmds = []

        class Proc:
            returncode = 0
            stdout, stderr = "", ""

        with patch("repair.subprocess.run", side_effect=lambda c, **k: cmds.append(c) or Proc()):
            result = repair.restore_upload_dir_access()
        self.assertTrue(result["ok"])
        self.assertEqual(len(cmds), 4)
        self.assertEqual([cmd[-1] for cmd in cmds],
                         ["/d/uploads", "/d/uploads", "/d/tmp", "/d/tmp"])
        for cmd in cmds:
            self.assertEqual(cmd[:4], ["docker", "run", "--rm", "-v"])
            self.assertNotIn("--name", cmd)
            self.assertFalse(any(a in ("-e", "--env", "--label", "-l") for a in cmd))
        self.assertIn("chown", cmds[0])
        self.assertIn("1000:1000", cmds[0])
        self.assertIn("chmod", cmds[1])
        self.assertIn("755", cmds[1])
        self.assertIn("chown", cmds[2])
        self.assertIn("chmod", cmds[3])


class TestClearStaleTempFiles(unittest.TestCase):
    def _run(self, stdout="", returncode=0, stderr=""):
        cmds = []

        class Proc:
            pass

        proc = Proc()
        proc.returncode, proc.stdout, proc.stderr = returncode, stdout, stderr
        with patch("repair.subprocess.run", side_effect=lambda c, **k: cmds.append(c) or proc):
            result = repair.clear_stale_temp_files()
        return result, cmds

    def test_registered_under_investigate_name(self):
        self.assertIs(repair.REPAIRS["clear_stale_temp_files"], repair.clear_stale_temp_files)

    def test_cli_accepts_new_repair(self):
        with patch.dict(repair.REPAIRS, {"clear_stale_temp_files": lambda: {"ok": True, "detail": "mocked"}}):
            self.assertEqual(repair.main(["clear_stale_temp_files", "--yes", "--no-verify"]), 0)

    def test_deletes_via_one_find_helper(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "tmp").mkdir()
            with patch.object(repair, "DATA_MOUNT", tmpdir):
                result, cmds = self._run("/d/tmp/a.part\n/d/tmp/b.part\n")
        self.assertTrue(result["ok"], result)
        self.assertIn("deleted 2", result["detail"])
        self.assertEqual(len(cmds), 1)
        cmd = cmds[0]
        self.assertEqual(cmd[:4], ["docker", "run", "--rm", "-v"])
        self.assertNotIn("--name", cmd)
        self.assertFalse(any(a in ("-e", "--env", "--label", "-l") for a in cmd))
        self.assertEqual(cmd[5:], ["alpine", "find", "/d/tmp", "-maxdepth", "1",
                                  "-name", "*.part", "-type", "f", "-mmin", "+60",
                                  "-delete", "-print"])

    def test_noop_when_nothing_stale(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "tmp").mkdir()
            with patch.object(repair, "DATA_MOUNT", tmpdir):
                result, cmds = self._run("")
        self.assertTrue(result["ok"], result)
        self.assertIn("nothing to delete", result["detail"])
        self.assertEqual(len(cmds), 1)

    def test_missing_tmp_skips_helper(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(repair, "DATA_MOUNT", tmpdir):
                result, cmds = self._run()
        self.assertTrue(result["ok"], result)
        self.assertEqual(cmds, [])

    def test_find_failure_is_not_ok(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "tmp").mkdir()
            with patch.object(repair, "DATA_MOUNT", tmpdir):
                result, _ = self._run(returncode=1, stderr="boom")
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["detail"])


class TestRestoreDatabase(unittest.TestCase):
    NET = "incidentlab_default"

    def _db(self, status="running", running=True, paused=False,
            health="healthy", networks=None):
        if networks is None:
            networks = (self.NET,)
        return {"State": {"Status": status, "Running": running, "Paused": paused,
                          "Health": {"Status": health}},
                "NetworkSettings": {"Networks": {n: {} for n in networks}}}

    def _app(self, networks=None):
        if networks is None:
            networks = (self.NET,)
        return {"State": {"Status": "running", "Running": True},
                "NetworkSettings": {"Networks": {n: {} for n in networks}}}

    def _run(self, db_queue, app=None, fail_prefixes=(), timeout=90):
        """Run the repair against scripted inspect outputs. Last item repeats."""
        cmds = []
        queues = {repair.DB: list(db_queue), repair.APP: [app or self._app()]}

        class Proc:
            def __init__(self, stdout="", returncode=0, stderr=""):
                self.stdout, self.returncode, self.stderr = stdout, returncode, stderr

        def fake_run(cmd, **kwargs):
            if cmd[:3] == ["docker", "inspect", "--type"]:
                queue = queues[cmd[-1]]
                payload = queue.pop(0) if len(queue) > 1 else queue[0]
                return Proc(stdout=json.dumps([payload]))
            cmds.append(cmd)
            if any(list(cmd[: len(p)]) == list(p) for p in fail_prefixes):
                return Proc(returncode=1, stderr="boom")
            return Proc()

        with (
            patch("repair.subprocess.run", side_effect=fake_run),
            patch.object(repair, "DB_POLL_SECONDS", 0),
            patch.object(repair, "DB_READY_TIMEOUT", timeout),
        ):
            result = repair.restore_database()
        return result, cmds

    def test_registered_under_investigate_name(self):
        self.assertIs(repair.REPAIRS["restore_database"], repair.restore_database)

    def test_cli_accepts_new_repair(self):
        with patch.dict(repair.REPAIRS, {"restore_database": lambda: {"ok": True, "detail": "mocked"}}):
            self.assertEqual(repair.main(["restore_database", "--yes", "--no-verify"]), 0)

    def test_stopped_db_is_started(self):
        stopped = self._db(status="exited", running=False)
        starting = self._db(health="starting")
        result, cmds = self._run([stopped, starting, starting, self._db()])
        self.assertTrue(result["ok"], result)
        self.assertIn("started", result["detail"])
        self.assertIn(["docker", "start", repair.DB], cmds)
        self.assertFalse(any(c[1] == "unpause" for c in cmds))
        self.assertFalse(any(c[1] == "network" for c in cmds))

    def test_paused_db_is_unpaused_not_started(self):
        paused = self._db(status="paused", paused=True)
        result, cmds = self._run([paused, self._db()])
        self.assertTrue(result["ok"], result)
        self.assertIn("unpaused", result["detail"])
        self.assertIn(["docker", "unpause", repair.DB], cmds)
        self.assertFalse(any(c[1] == "start" for c in cmds))

    def test_missing_network_is_reconnected(self):
        lonely = self._db(networks=())
        result, cmds = self._run([lonely, lonely])
        self.assertTrue(result["ok"], result)
        self.assertIn("connected to", result["detail"])
        self.assertIn(["docker", "network", "connect", "--alias", repair.DB_ALIAS,
                       self.NET, repair.DB], cmds)

    def test_already_healthy_is_noop(self):
        result, cmds = self._run([self._db()])
        self.assertTrue(result["ok"], result)
        self.assertIn("already running and healthy", result["detail"])
        self.assertEqual(cmds, [])

    def test_unhealthy_timeout_is_not_ok(self):
        result, _ = self._run([self._db(health="starting")], timeout=0)
        self.assertFalse(result["ok"])
        self.assertIn("did not become healthy", result["detail"])

    def test_start_failure_is_not_ok(self):
        stopped = self._db(status="exited", running=False)
        result, cmds = self._run([stopped], fail_prefixes=(["docker", "start"],))
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["detail"])


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
            patch.object(v, "delete_file", return_value={"deleted": "ab" * 16}) as delete,
        ):
            result = repair.verify_recovery()
        self.assertTrue(result["ok"], result)
        delete.assert_called_once_with("ab" * 16)

    def test_cleanup_failure_keeps_ok(self):
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
            patch.object(v, "delete_file", side_effect=ConnectionError("gone")),
        ):
            result = repair.verify_recovery()
        self.assertTrue(result["ok"], result)
        self.assertIn("cleanup delete failed", result["detail"])

    def test_upload_failure_is_not_recovery(self):
        import lab.verify as v

        with patch.object(v, "post_upload", side_effect=ConnectionError("down")):
            result = repair.verify_recovery()
        self.assertFalse(result["ok"])
        self.assertIn("UPLOAD FAILED", result["detail"])


if __name__ == "__main__":
    unittest.main()
