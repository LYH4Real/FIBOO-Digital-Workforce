"""Offline compiler tests: inputs and approvals are synthetic temporary fixtures."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_generation.py"
SPEC = importlib.util.spec_from_file_location("prepare_generation", SCRIPT)
compiler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compiler)
guard = compiler.workflow


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ref_a, self.ref_b, self.product = [self.root / name for name in ("a.png", "b.png", "product.png")]
        for path in (self.ref_a, self.ref_b, self.product):
            path.write_bytes(b"\x89PNG\r\n\x1a\nfixture" + path.name.encode())
        self.facts = {"schema_version": 1, "product_scope": "synthetic product",
                      "sources": [{"id": "product", "kind": "file", "path": str(self.product)}],
                      "facts": {name: {"status": "confirmed", "required_for_plan": True,
                                        "value": "synthetic", "candidates": []}
                                for name in ("product_name", "core_claims", "dosage", "packaging")}}
        observation = {key: "synthetic observation" for key in
                       ("summary", "visible_text", "composition", "shot", "lighting", "style", "imperfections")}
        self.manifest = {"schema_version": 1, "images": [
            {"id": ident, "path": str(path), "sha256": guard.sha256_file(path), "page_index": number,
             "semantic_status": "verified", "inspection": {"method": "human_confirmed", "inspected_at": "test"},
             "observation": observation} for number, (ident, path) in enumerate(
                 (("source-a", self.ref_a), ("source-b", self.ref_b)), 1)]}
        self.plan = {"schema_version": 1, "status": "awaiting_user_approval", "task_id": "task-test",
                     "strategy": "synthetic", "title_candidates": ["title"], "body_copy": "body",
                     "reference_contract": {key: "strict" for key in
                                            ("narrative", "composition", "image_style", "copy_tone")},
                     "questions": [], "pages": [
                         {"id": ident, "purpose": "synthetic", "visual_blueprint": "synthetic",
                          "prompt": "【参考强度】\n  严格参考。\nPreserve prompt exactly.", "size": "1248x1664",
                          "reference_image_ids": ids, "product_image_paths": [str(self.product)],
                          "product_required": True, "ugc_required": True}
                         for ident, ids in (("p02", ["source-b", "source-a"]), ("p01", ["source-a"]))]}
        self.paths = {name: self.root / (name + ".json") for name in
                      ("plan", "facts", "facts_report", "reference_manifest", "reference_report", "approval")}
        self.html = self.root / "plan.html"
        self.html.write_text("<title>Synthetic approved plan</title>", encoding="utf-8")
        self.refresh()

    def write(self, name, value):
        self.paths[name].write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def refresh(self):
        self.write("facts", self.facts)
        self.write("facts_report", guard.validate_facts(self.facts))
        self.write("reference_manifest", self.manifest)
        self.write("reference_report", guard.validate_references(self.manifest))
        self.write("plan", self.plan)
        self.approval = {"schema_version": 1, "status": "approved", "approved_by": "user",
                         "plan_json": str(self.paths["plan"]), "plan_html": str(self.html),
                         "plan_sha256": guard.sha256_file(self.paths["plan"]),
                         "plan_html_sha256": guard.sha256_file(self.html),
                         "approval_evidence": "synthetic user approval", "approved_at": "test"}
        self.write("approval", self.approval)

    def run_compile(self, **options):
        return compiler.prepare(**self.paths, output_root=self.root / "runs", **options)

    def test_compiles_in_approved_order_and_preserves_prompt_and_reference_order(self):
        before = {path: path.read_bytes() for path in self.paths.values()}
        summary = self.run_compile()
        request = guard.load(summary["request"])
        tasks = request["arguments"]["tasks"]
        self.assertEqual([t["id"] for t in tasks], ["p02", "p01"])
        self.assertEqual(tasks[0]["image_paths"], [str(self.ref_b), str(self.ref_a), str(self.product)])
        self.assertEqual(tasks[0]["prompt"], self.plan["pages"][0]["prompt"])
        self.assertEqual(request["arguments"]["retries"], 0)
        self.assertEqual(request["arguments"]["concurrency"], 3)
        self.assertNotIn("model", tasks[0])
        self.assertNotIn("response_format", tasks[0])
        self.assertFalse(summary["network_called"])
        self.assertEqual(before, {path: path.read_bytes() for path in self.paths.values()})
        self.assertTrue(guard.load(summary["preflight"])["ok"])

    def test_page_filter_keeps_plan_order_and_only_selected_pages(self):
        summary = self.run_compile(pages=["p01", "p02"])
        self.assertEqual(summary["page_ids"], ["p02", "p01"])
        single = self.run_compile(pages=["p01"])
        request = guard.load(single["request"])
        self.assertEqual([t["id"] for t in request["arguments"]["tasks"]], ["p01"])
        self.assertEqual(set(request["reference_context"]["source_images_by_task"]), {"p01"})
        for ids in (["unknown"], ["p01", "p01"], []):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.run_compile(pages=ids)

    def test_model_and_format_inherited_and_explicit_conflicts_rejected(self):
        self.plan["model"] = "approved-model"
        self.plan["pages"][0]["response_format"] = "b64_json"
        self.refresh()
        request = guard.load(self.run_compile(model="approved-model")["request"])
        self.assertEqual(request["arguments"]["tasks"][0]["response_format"], "b64_json")
        self.assertEqual(request["arguments"]["tasks"][1]["model"], "approved-model")
        for options in ({"model": "other"}, {"response_format": "url"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.run_compile(**options)

    def test_approved_count_inherits_plan_with_page_override_and_special_root_tool_blocks(self):
        self.plan["n"] = 3
        self.plan["pages"][0]["n"] = 2
        self.refresh()
        request = guard.load(self.run_compile()["request"])
        self.assertEqual([t["n"] for t in request["arguments"]["tasks"]], [2, 3])
        self.plan["tool"] = "generate_image"
        self.refresh()
        with self.assertRaisesRegex(ValueError, "特殊工具"):
            self.run_compile()

    def test_approval_must_match_this_plan_even_identical_copy(self):
        other = self.root / "other-plan.json"
        other.write_bytes(self.paths["plan"].read_bytes())
        self.approval["plan_json"] = str(other)
        self.write("approval", self.approval)
        with self.assertRaisesRegex(ValueError, "不同"):
            self.run_compile()

    def test_missing_approval_and_mutation_block_without_output(self):
        self.approval["approved_by"] = "agent"
        self.write("approval", self.approval)
        with self.assertRaises(ValueError):
            self.run_compile()
        self.refresh()
        self.plan["body_copy"] = "unapproved change"
        self.write("plan", self.plan)
        with self.assertRaisesRegex(ValueError, "已变化"):
            self.run_compile()
        self.assertFalse((self.root / "runs").exists())

    def test_facts_conflict_and_stale_reports_block(self):
        self.facts["facts"]["dosage"]["status"] = "conflict"
        self.refresh()
        with self.assertRaises(ValueError):
            self.run_compile()
        self.facts["facts"]["dosage"]["status"] = "confirmed"
        self.refresh()
        self.facts["product_scope"] = "changed"
        self.write("facts", self.facts)
        with self.assertRaisesRegex(ValueError, "哈希"):
            self.run_compile()

    def test_reference_bytes_and_report_changes_block(self):
        report = guard.load(self.paths["reference_report"])
        report["input_sha256"] = "0" * 64
        self.write("reference_report", report)
        with self.assertRaises(ValueError):
            self.run_compile()
        self.refresh()
        self.ref_a.write_bytes(b"\x89PNG\r\n\x1a\nchanged")
        with self.assertRaisesRegex(ValueError, "哈希"):
            self.run_compile()

    def test_strength_conflict_duplicate_and_invalid_markers_block(self):
        original = copy.deepcopy(self.plan)
        for prompt, field in (("【参考强度】严格参考", "light"),
                              ("【参考强度】严格参考 【参考强度】轻度参考", None),
                              ("【参考强度】unknown", None)):
            self.plan = copy.deepcopy(original)
            self.plan["pages"][0]["prompt"] = prompt
            if field:
                self.plan["pages"][0]["reference_strength"] = field
            self.refresh()
            with self.subTest(prompt=prompt), self.assertRaises(ValueError):
                self.run_compile()

    def test_unknown_or_duplicate_references_and_custom_tool_block(self):
        original = copy.deepcopy(self.plan)
        for changes in ({"reference_image_ids": ["missing"]}, {"reference_image_ids": []},
                        {"reference_image_ids": ["source-a", "source-a"]}, {"tool": "generate_image"},
                        {"image_paths": [str(self.product), str(self.ref_a)]}):
            self.plan = copy.deepcopy(original)
            self.plan["pages"][0].update(changes)
            self.refresh()
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_compile()

    def test_limits_and_invalid_size_inherit_request_guard(self):
        for kwargs in ({"concurrency": 4}, {"concurrency": 0}, {"concurrency": True},
                       {"retries": -1}, {"retries": 6}, {"response_format": "png"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.run_compile(**kwargs)
        self.plan["pages"][0]["size"] = "1:1"
        self.refresh()
        with self.assertRaises(ValueError):
            self.run_compile()

    def test_new_unique_directories_and_refuse_overwrite(self):
        first, second = self.run_compile(), self.run_compile()
        self.assertNotEqual(first["run_id"], second["run_id"])
        before = Path(first["request"]).read_bytes()
        with patch.object(compiler, "new_run_id", return_value=first["run_id"]):
            with self.assertRaises(FileExistsError):
                self.run_compile()
        self.assertEqual(Path(first["request"]).read_bytes(), before)

    def test_cli_summary_does_not_print_prompt(self):
        args = [sys.executable, "-B", "-X", "utf8", str(SCRIPT)]
        for key, path in self.paths.items():
            args.extend(["--" + key.replace("_", "-"), str(path)])
        result = subprocess.run(args + ["--output-root", str(self.root / "cli-runs")],
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Preserve prompt exactly", result.stdout)
        self.assertTrue(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
