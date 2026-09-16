"""Tests for material, fact, semantic-reference, plan, and approval gates."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "workflow_guard.py"
SPEC = importlib.util.spec_from_file_location("workflow_guard", SCRIPT)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class WorkflowGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.brief = self.root / "brief.txt"
        self.brief.write_text("source", encoding="utf-8")
        self.reference = self.root / "reference.png"
        self.reference.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test-image")

    def facts(self):
        return {
            "schema_version": 1,
            "product_scope": "example product / current pack",
            "sources": [{"id": "brief", "kind": "file", "path": str(self.brief)}],
            "facts": {
                "product_name": {"status": "confirmed", "required_for_plan": True,
                                 "value": "Example", "candidates": [{"value": "Example", "source_id": "brief"}]},
                "core_claims": {"status": "confirmed", "required_for_plan": True,
                                "value": ["Claim"], "candidates": []},
                "dosage": {"status": "not_applicable", "required_for_plan": False,
                            "reason": "Not used", "candidates": []},
                "packaging": {"status": "confirmed", "required_for_plan": True,
                              "value": {"net": "400g"}, "candidates": []},
            },
        }

    def reference_manifest(self):
        return {
            "schema_version": 1,
            "images": [{
                "id": "source-p01", "path": str(self.reference),
                "sha256": hashlib.sha256(self.reference.read_bytes()).hexdigest(),
                "page_index": 1, "semantic_status": "verified",
                "inspection": {"method": "multimodal_model", "inspected_at": "2026-09-08T00:00:00Z"},
                "observation": {
                    "summary": "handheld product", "visible_text": ["title"],
                    "composition": "left photo, right product", "shot": "phone close-up",
                    "lighting": "mixed room light", "style": "casual snapshot",
                    "imperfections": "slightly tilted", "product_or_brand": "competitor pack",
                },
            }],
        }

    def plan(self):
        return {
            "schema_version": 1, "status": "awaiting_user_approval", "task_id": "task-01",
            "strategy": "one problem, one useful answer",
            "reference_contract": {"narrative": "borrow", "composition": "strict",
                                   "image_style": "strict", "copy_tone": "borrow"},
            "title_candidates": ["Title"], "body_copy": "Body",
            "pages": [{"id": "task-01-p01", "purpose": "cover", "visual_blueprint": "phone shot",
                       "prompt": "prompt", "size": "1248x1664", "reference_image_ids": ["source-p01"],
                       "product_image_paths": [], "product_required": False, "ugc_required": True}],
            "questions": [],
        }

    def test_inventory_recurses_and_hashes(self):
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "image.jpg").write_bytes(b"abc")
        report = guard.inventory(self.root)
        self.assertEqual(report["file_count"], 3)
        self.assertEqual({item["relative_path"] for item in report["files"]},
                         {"brief.txt", "reference.png", "nested/image.jpg"})

    def test_inventory_cli_emits_one_success_report_and_exits_zero(self):
        command = [sys.executable, "-B", "-X", "utf8", str(SCRIPT),
                   "inventory", "--root", str(self.root)]
        result = subprocess.run(command, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])
        output = self.root / "inventory.json"
        result = subprocess.run(command + ["--output", str(output)], capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertTrue(json.loads(output.read_text(encoding="utf-8"))["ok"])

    def test_inventory_scope_prunes_outputs_and_deduplicates_inputs(self):
        task = self.root / "task"
        refs, generated, history = task / "refs", task / "outputs", task / "history"
        for directory in (refs, generated, history):
            directory.mkdir(parents=True)
            (directory / "image.png").write_bytes(b"image")
        selected = refs / "image.png"
        with patch.object(guard, "sha256_file", wraps=guard.sha256_file) as hasher:
            report = guard.inventory(self.root, include=["brief.txt", "task", str(selected)],
                                     exclude=["task/outputs", "task/history"])
        self.assertEqual({item["relative_path"] for item in report["files"]},
                         {"brief.txt", "task/refs/image.png"})
        self.assertEqual(hasher.call_count, 2)
        self.assertEqual(report["scope"]["exclude"], ["task/outputs", "task/history"])

    def test_inventory_selected_sources_are_rehashed_even_with_same_size_and_mtime(self):
        before = guard.inventory(self.root, include=["brief.txt"])
        stamp = self.brief.stat()
        self.brief.write_text("edited", encoding="utf-8")
        os.utime(self.brief, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        after = guard.inventory(self.root, include=["brief.txt"])
        self.assertEqual(before["files"][0]["bytes"], after["files"][0]["bytes"])
        self.assertNotEqual(before["files"][0]["sha256"], after["files"][0]["sha256"])

    def test_inventory_missing_or_outside_include_fails_without_silent_omission(self):
        with self.assertRaisesRegex(ValueError, "不存在"):
            guard.inventory(self.root, include=["missing-brief.pdf"])
        with self.assertRaisesRegex(ValueError, "任务目录"):
            guard.inventory(self.root, include=[self.root.parent])

    def test_inventory_cli_accepts_repeated_scope_paths(self):
        command = [sys.executable, "-B", "-X", "utf8", str(SCRIPT), "inventory",
                   "--root", str(self.root), "--include", "brief.txt", "--include", "reference.png",
                   "--exclude", "reference.png"]
        result = subprocess.run(command, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([item["relative_path"] for item in json.loads(result.stdout)["files"]], ["brief.txt"])

    def test_critical_conflict_blocks_even_if_mislabeled_confirmed(self):
        doc = self.facts()
        doc["facts"]["product_name"]["candidates"].append({"value": "Other", "source_id": "brief"})
        report = guard.validate_facts(doc)
        self.assertFalse(report["ok"])
        self.assertEqual(report["next_action"], "ask_user_and_stop_before_plan_html")

    def test_only_user_confirmation_resolves_conflict(self):
        doc = self.facts()
        entry = doc["facts"]["dosage"] = {"status": "conflict", "required_for_plan": True,
                                           "candidates": [{"value": "10g", "source_id": "brief"},
                                                          {"value": "15g", "source_id": "brief"}]}
        self.assertFalse(guard.validate_facts(doc)["ok"])
        entry["resolution"] = {"value": "15g", "source": "agent_choice", "evidence": "newer file"}
        with self.assertRaisesRegex(ValueError, "用户确认"):
            guard.validate_facts(doc)
        entry["resolution"]["source"] = "user_confirmation"
        self.assertTrue(guard.validate_facts(doc)["ok"])

    def test_noncritical_conflict_blocks_when_current_plan_needs_it(self):
        doc = self.facts()
        doc["facts"]["nutrition"] = {"status": "conflict", "required_for_plan": False,
                                     "candidates": [{"value": "a", "source_id": "brief"},
                                                    {"value": "b", "source_id": "brief"}]}
        self.assertTrue(guard.validate_facts(doc)["ok"])
        doc["facts"]["nutrition"]["required_for_plan"] = True
        self.assertFalse(guard.validate_facts(doc)["ok"])

    def test_reference_manifest_requires_real_vision_semantics_and_hash(self):
        doc = self.reference_manifest()
        self.assertTrue(guard.validate_references(doc)["ok"])
        doc["images"][0]["inspection"]["method"] = "filename_only"
        with self.assertRaisesRegex(ValueError, "实际看图"):
            guard.validate_references(doc)
        doc = self.reference_manifest()
        doc["images"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "哈希"):
            guard.validate_references(doc)

    def test_plan_requires_passed_fact_and_reference_gates(self):
        refs = guard.validate_references(self.reference_manifest())
        facts = guard.validate_facts(self.facts())
        self.assertTrue(guard.validate_plan(self.plan(), facts, refs)["ok"])
        refs["ok"] = False
        with self.assertRaisesRegex(ValueError, "参考图语义"):
            guard.validate_plan(self.plan(), facts, refs)

    def test_approval_is_invalid_after_plan_or_html_changes(self):
        plan, html = self.root / "plan.json", self.root / "plan.html"
        plan.write_text("{}", encoding="utf-8")
        html.write_text("<html></html>", encoding="utf-8")
        approval = {
            "schema_version": 1, "status": "approved", "approved_by": "user",
            "plan_json": str(plan), "plan_html": str(html),
            "plan_sha256": guard.sha256_file(plan), "plan_html_sha256": guard.sha256_file(html),
            "approval_evidence": "按这个版本作图", "approved_at": "2026-09-08T00:00:00Z",
        }
        self.assertTrue(guard.validate_approval(approval)["ok"])
        html.write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "HTML 已变化"):
            guard.validate_approval(approval)

    def test_cli_refuses_to_overwrite_report(self):
        facts_path, output = self.root / "facts.json", self.root / "report.json"
        facts_path.write_text(json.dumps(self.facts()), encoding="utf-8")
        command = [sys.executable, "-B", "-X", "utf8", str(SCRIPT), "facts", "--input", str(facts_path), "--output", str(output)]
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
