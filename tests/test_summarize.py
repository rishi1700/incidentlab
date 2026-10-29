#!/usr/bin/env python3
"""Unit tests for eval/summarize.py's per-case table. No lab needed.

Run from the repo root: python3 -m unittest tests.test_summarize -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

import summarize as summarize_module


def record(case, run, **overrides):
    rec = {"case": case, "run": run, "expected_cause": "volume_full",
           "model_cause": "volume_full", "baseline_cause": "volume_full",
           "citations_supported": 8, "citations_total": 8,
           "recovery_verified": True}
    rec.update(overrides)
    return rec


class PerCaseTableTest(unittest.TestCase):
    def test_full_case_row(self):
        rows = summarize_module.per_case_table(
            [record("V1", 1), record("V1", 2), record("V1", 3)])
        self.assertEqual(rows[0],
                         "| Case | Type | Expected | Model run 1 | Model run 2"
                         " | Model run 3 | Baseline | Citations | Recovery |")
        self.assertEqual(rows[2],
                         "| V1 | fault | volume_full | volume_full"
                         " | volume_full | volume_full | volume_full"
                         " | 24/24 | 3/3 |")

    def test_case_order_and_mixed_cells(self):
        recs = [record("D1", 1, model_cause="database_unavailable",
                       expected_cause="database_unavailable",
                       baseline_cause="database_unavailable"),
                record("V1", 2),
                {"case": "V1", "run": 1, "harness_error": "boom", "stage": "inject",
                 "expected_cause": "volume_full"},
                {"case": "P1", "run": 3, "no_model": True,
                 "expected_cause": "upload_dir_not_writable",
                 "baseline_cause": "healthy"}]
        rows = summarize_module.per_case_table(recs)
        self.assertEqual(rows[2].split(" | ")[0], "| V1")
        self.assertEqual(rows[3].split(" | ")[0], "| P1")
        self.assertEqual(rows[4].split(" | ")[0], "| D1")
        # V1: run 1 error, run 2 answer, run 3 missing; citations/recovery only
        # from the settled run.
        self.assertIn("| error | volume_full | missing |", rows[2])
        self.assertTrue(rows[2].endswith("| volume_full | 8/8 | 1/1 |"))
        # P1: --no-model run shown as skipped with no citations or recovery.
        self.assertIn("| skipped |", rows[3])
        self.assertTrue(rows[3].endswith("| n/a | n/a | n/a |"))


if __name__ == "__main__":
    unittest.main()
