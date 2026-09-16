"""Functional local-only timing tests; fake clocks exist only in test fixtures."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "task_timing.py"
SPEC = importlib.util.spec_from_file_location("task_timing", SCRIPT)
timing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(timing)


def sample(seconds, *, process="process-a", boot="boot-a", host="host-a", wall=None):
    wall = seconds if wall is None else wall
    return {"utc": f"test-{wall}", "utc_ns": int(wall * 1e9), "monotonic_ns": int(seconds * 1e9), "pid": 10,
            "clock": {"host_id": host, "boot_id": boot, "process_id": process,
                      "implementation": "test-clock", "cross_process_comparable": bool(boot)}}


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def begin(self, **kwargs):
        return Path(timing.begin(self.root, **kwargs)["journal"])

    def cli(self, *args):
        return subprocess.run([sys.executable, "-B", "-X", "utf8", str(SCRIPT), *map(str, args)],
                              capture_output=True, encoding="utf-8")

    def test_stages_are_exclusive_and_waiting_is_only_active_deduction(self):
        with patch.object(timing, "sample_clock", side_effect=[sample(t) for t in (0, 10, 15, 45, 65, 85, 100)]):
            journal = self.begin()
            timing.mark(journal, "planning")
            timing.mark(journal, "awaiting_user")
            timing.mark(journal, "generation")
            timing.mark(journal, "blocked")
            timing.mark(journal, "rework")
            report = timing.finish(journal, "partial", 2)
        self.assertEqual(report["elapsed_seconds"], 100)
        self.assertEqual(report["awaiting_user_seconds"], 30)
        self.assertEqual(report["active_seconds"], 70)
        self.assertEqual(sum(report["stage_seconds"].values()), 100)
        self.assertEqual(report["stage_seconds"]["blocked"], 20)
        self.assertEqual(report["stage_seconds"]["rework"], 15)

    def test_unique_runs_append_only_and_finished_journal_is_sealed(self):
        journal, second = self.begin(), self.begin()
        self.assertNotEqual(journal, second)
        before = journal.read_bytes()
        timing.mark(journal, "generation")
        self.assertTrue(journal.read_bytes().startswith(before))
        timing.finish(journal, "complete", 3)
        completed = journal.read_bytes()
        for callback in (lambda: timing.mark(journal, "rework"), lambda: timing.finish(journal, "failed", 0)):
            with self.assertRaisesRegex(ValueError, "已完成"):
                callback()
        self.assertEqual(journal.read_bytes(), completed)
        self.assertEqual(timing.report(journal)["quality"]["status"], "unverified")

    def test_lock_blocks_another_process_without_overwriting_history(self):
        journal = self.begin()
        before = journal.read_bytes()
        with timing.journal_lock(journal):
            result = self.cli("mark", "--journal", journal, "--stage", "generation")
            self.assertEqual(result.returncode, 2)
            self.assertIn("锁", result.stderr)
        self.assertEqual(journal.read_bytes(), before)
        self.assertEqual(self.cli("mark", "--journal", journal, "--stage", "generation").returncode, 0)

    def test_cross_process_cli_reports_real_clock_metadata_and_no_quality_claim(self):
        result = self.cli("begin", "--output-root", self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        journal = Path(json.loads(result.stdout)["journal"])
        self.assertEqual(self.cli("mark", "--journal", journal, "--stage", "delivery").returncode, 0)
        done = self.cli("finish", "--journal", journal, "--outcome", "complete", "--pages", 4)
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout)
        self.assertGreater(report["elapsed_seconds"], 0)
        self.assertEqual(report["quality"], {"status": "unverified", "evidence": None})
        self.assertEqual(report["target"]["quality_and_time_acceptance"], "unverified")
        events = timing.read_events(journal)
        self.assertEqual(len({event["pid"] for event in events}), 3)
        self.assertTrue(all(event["utc"].endswith("+00:00") for event in events))
        self.assertEqual(self.cli("report", "--journal", journal).returncode, 0)

    def test_failed_runs_and_preparation_modes_remain_visible(self):
        for mode in timing.MODES:
            journal = self.begin(mode=mode)
            report = timing.finish(journal, "failed", 0)
            self.assertEqual(report["mode"], mode)
            self.assertEqual(report["outcome"], "failed")
            self.assertTrue(report["include_in_all_runs_denominator"])
            self.assertEqual(report["cold_start_cost_included"], mode == "cold")

    def test_same_boot_cross_process_monotonic_is_used_despite_wall_jump(self):
        with patch.object(timing, "sample_clock", side_effect=[sample(100), sample(110, process="other", wall=5000)]):
            journal = self.begin()
            report = timing.finish(journal, "complete", 1)
        self.assertEqual(report["elapsed_seconds"], 10)
        self.assertEqual(report["clock_basis"], ["monotonic"])
        self.assertTrue(report["warnings"])

    def test_changed_boot_or_unknown_cross_process_uses_labelled_utc_fallback(self):
        for boot in ("boot-b", None):
            with self.subTest(boot=boot), patch.object(timing, "sample_clock", side_effect=[
                    sample(100), sample(5, process="other", boot=boot, wall=120)]):
                journal = self.begin()
                report = timing.finish(journal, "partial", 0)
            self.assertEqual(report["elapsed_seconds"], 20)
            self.assertFalse(report["timing_reliable"])
            self.assertEqual(report["clock_basis"], ["utc_fallback"])

    def test_negative_uncomparable_clock_never_becomes_fast_success(self):
        with patch.object(timing, "sample_clock", side_effect=[sample(100), sample(1, process="b", boot="b", wall=90)]):
            journal = self.begin()
            report = timing.finish(journal, "complete", 1)
        self.assertIsNone(report["elapsed_seconds"])
        self.assertIsNone(report["target"]["elapsed_within_target"])
        self.assertFalse(report["timing_reliable"])

    def test_report_running_includes_open_interval_without_appending_event(self):
        with patch.object(timing, "sample_clock", side_effect=[sample(0), sample(7)]):
            journal = self.begin()
            before = journal.read_bytes()
            report = timing.report(journal)
        self.assertEqual(report["elapsed_seconds"], 7)
        self.assertFalse(report["finished"])
        self.assertEqual(report["outcome"], "running")
        self.assertEqual(journal.read_bytes(), before)

    def test_over_target_and_invalid_cli_options(self):
        with patch.object(timing, "sample_clock", side_effect=[sample(0), sample(601)]):
            journal = self.begin()
            report = timing.finish(journal, "complete", 6)
        self.assertFalse(report["target"]["elapsed_within_target"])
        for args in (("begin", "--output-root", self.root, "--timestamp", "2020-01-01"),
                     ("mark", "--journal", journal, "--stage", "fake"),
                     ("finish", "--journal", journal, "--outcome", "complete", "--pages", "-1")):
            self.assertEqual(self.cli(*args).returncode, 2)

    def test_truncated_journal_fails_without_repair(self):
        journal = self.begin()
        journal.write_bytes(journal.read_bytes()[:-1])
        before = journal.read_bytes()
        with self.assertRaisesRegex(ValueError, "不完整"):
            timing.mark(journal, "generation")
        self.assertEqual(journal.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
