"""Standalone preflight rejects changes to already approved fast-plan inputs."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import image_request_guard as image_guard
import workflow_guard as workflow


class BoundPlanPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.materials = self.root / "materials"
        self.materials.mkdir()
        self.refs = [self.materials / "ref-a.png", self.materials / "ref-b.png"]
        self.product = self.materials / "product.png"
        for path in self.refs + [self.product]:
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + path.name.encode())
        self.facts = {"schema_version": 1, "product_scope": "Synthetic product",
                      "sources": [{"id": "pack", "kind": "file", "path": str(self.product)}],
                      "facts": {key: {"status": "confirmed", "required_for_plan": True,
                                      "value": "synthetic", "candidates": []}
                                for key in workflow.CRITICAL_FACTS}}
        observation = {key: "Synthetic observation" for key in
                       ("summary", "visible_text", "composition", "shot", "lighting", "style", "imperfections")}
        self.manifest = {"schema_version": 1, "images": [
            {"id": name, "path": str(path), "sha256": workflow.sha256_file(path),
             "semantic_status": "verified", "inspection": {"method": "human_confirmed", "inspected_at": "test"},
             "observation": observation}
            for name, path in zip(("ref-a", "ref-b"), self.refs)]}
        self.paths = {name: self.root / (name + ".json") for name in
                      ("inventory", "facts", "facts_report", "manifest", "reference_report", "plan", "approval")}
        self.html = self.root / "plan.html"
        self.html.write_text("<!doctype html><title>synthetic approval</title>", encoding="utf-8")
        self.write("inventory", workflow.inventory(self.materials))
        self.write("facts", self.facts)
        self.write("facts_report", workflow.validate_facts(self.facts))
        self.write("manifest", self.manifest)
        self.write("reference_report", workflow.validate_references(self.manifest))
        self.plan = {"schema_version": 1, "status": "awaiting_user_approval", "task_id": "task-bound",
                     "strategy": "synthetic", "title_candidates": ["Title"], "body_copy": "Body",
                     "reference_contract": {key: "strict" for key in
                                            ("narrative", "composition", "image_style", "copy_tone")},
                     "n": 2, "model": "approved-model", "response_format": "b64_json", "pages": [
                         {"id": ident, "purpose": "synthetic", "visual_blueprint": "synthetic",
                          "prompt": "【参考强度】严格参考。\n  Keep exact approved wording.", "size": "1248x1664",
                          "reference_image_ids": refs, "product_image_paths": [str(self.product)],
                          "product_required": True, "ugc_required": True}
                         for ident, refs in (("p02", ["ref-b", "ref-a"]), ("p01", ["ref-a"]))],
                     "input_binding": {"schema_version": 1, "artifacts": [
                         {"kind": kind, "path": str(self.paths[key]), "sha256": workflow.sha256_file(self.paths[key])}
                         for kind, key in (("material_inventory", "inventory"), ("product_facts", "facts"),
                                           ("reference_manifest", "manifest"))]}}
        self.refresh_approval()
        self.request = self.make_request()

    def write(self, key, value):
        self.paths[key].write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def refresh_approval(self):
        self.write("plan", self.plan)
        self.write("approval", {"schema_version": 1, "status": "approved", "approved_by": "user",
                                "plan_json": str(self.paths["plan"]), "plan_html": str(self.html),
                                "plan_sha256": workflow.sha256_file(self.paths["plan"]),
                                "plan_html_sha256": workflow.sha256_file(self.html),
                                "approval_evidence": "synthetic user approval", "approved_at": "test"})

    def make_request(self):
        lookup = {entry["id"]: entry["path"] for entry in self.manifest["images"]}
        tasks, sources, ids = [], {}, {}
        for page in self.plan["pages"]:
            sources[page["id"]] = [lookup[key] for key in page["reference_image_ids"]]
            ids[page["id"]] = page["reference_image_ids"][:]
            tasks.append({"id": page["id"], "prompt": page["prompt"], "size": page["size"],
                          "image_paths": sources[page["id"]] + page["product_image_paths"],
                          "n": page.get("n", self.plan.get("n", 1))})
            for key in ("model", "response_format"):
                value = page.get(key, self.plan.get(key))
                if value is not None:
                    tasks[-1][key] = value
        return {"schema_version": 2, "run_id": "bound-run", "tool": "edit_batch_images",
                "arguments": {"tasks": tasks, "output_subdir": "runs/bound-run", "concurrency": 3, "retries": 0},
                "reference_context": {"provided": True, "visual_strength_by_task": {p["id"]: "strict" for p in tasks},
                                      "source_images_by_task": sources, "source_image_ids_by_task": ids},
                "planning_context": {"facts_input": str(self.paths["facts"]), "facts_report": str(self.paths["facts_report"]),
                                     "reference_manifest": str(self.paths["manifest"]),
                                     "reference_report": str(self.paths["reference_report"]),
                                     "plan_approval": str(self.paths["approval"])}}

    def test_standalone_preflight_passes_exact_request_and_subset_without_mutation(self):
        before = copy.deepcopy(self.request)
        self.assertTrue(image_guard.preflight(self.request)["ok"])
        self.assertEqual(self.request, before)
        subset = copy.deepcopy(self.request)
        subset["arguments"]["tasks"] = subset["arguments"]["tasks"][1:]
        for key in ("visual_strength_by_task", "source_images_by_task", "source_image_ids_by_task"):
            subset["reference_context"][key].pop("p02")
        self.assertTrue(image_guard.preflight(subset)["ok"])

    def test_post_approval_argument_changes_fail_without_needing_compiler(self):
        for field, value in (("prompt", "【参考强度】严格参考。Changed wording"), ("size", "1440x1920"),
                             ("n", 1), ("model", "other-model"), ("response_format", "url")):
            request = copy.deepcopy(self.request)
            request["arguments"]["tasks"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "偏离批准策划"):
                image_guard.preflight(request)

    def test_approved_model_and_count_cannot_disappear(self):
        for field in ("model", "n", "response_format"):
            request = copy.deepcopy(self.request)
            request["arguments"]["tasks"][0].pop(field)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "偏离批准策划"):
                image_guard.preflight(request)

    def test_absent_options_cannot_be_added_after_approval(self):
        self.plan.pop("model")
        self.plan.pop("response_format")
        self.refresh_approval()
        request = self.make_request()
        self.assertTrue(image_guard.preflight(request)["ok"])
        for field, value in (("model", "new-model"), ("response_format", "url")):
            changed = copy.deepcopy(request)
            changed["arguments"]["tasks"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "偏离批准策划"):
                image_guard.preflight(changed)

    def test_page_and_image_order_cannot_change(self):
        request = copy.deepcopy(self.request)
        request["arguments"]["tasks"].reverse()
        with self.assertRaisesRegex(ValueError, "页面顺序"):
            image_guard.preflight(request)
        request = copy.deepcopy(self.request)
        request["arguments"]["tasks"][0]["image_paths"].reverse()
        with self.assertRaisesRegex(ValueError, "image_paths 偏离"):
            image_guard.preflight(request)

    def test_reference_ids_cannot_change_even_when_paths_are_self_consistent(self):
        request = copy.deepcopy(self.request)
        request["reference_context"]["source_image_ids_by_task"]["p02"].reverse()
        request["reference_context"]["source_images_by_task"]["p02"].reverse()
        with self.assertRaisesRegex(ValueError, "参考图 ID 或顺序偏离"):
            image_guard.preflight(request)

    def test_unknown_page_id_is_not_authorized(self):
        request = copy.deepcopy(self.request)
        request["arguments"]["tasks"][0]["id"] = "new-page"
        for key in ("visual_strength_by_task", "source_image_ids_by_task", "source_images_by_task"):
            request["reference_context"][key]["new-page"] = request["reference_context"][key].pop("p02")
        with self.assertRaisesRegex(ValueError, "批准策划之外"):
            image_guard.preflight(request)

    def test_same_content_facts_or_manifest_copy_cannot_borrow_approval(self):
        for context_key, source_key in (("facts_input", "facts"), ("reference_manifest", "manifest")):
            copied = self.root / (source_key + "-copy.json")
            copied.write_bytes(self.paths[source_key].read_bytes())
            request = copy.deepcopy(self.request)
            request["planning_context"][context_key] = str(copied)
            with self.subTest(field=context_key), self.assertRaisesRegex(ValueError, "不是批准策划绑定"):
                image_guard.preflight(request)

    def test_changed_facts_with_refreshed_report_still_invalidates_approval(self):
        self.facts["facts"]["product_name"]["value"] = "changed product"
        self.write("facts", self.facts)
        self.write("facts_report", workflow.validate_facts(self.facts))
        with self.assertRaisesRegex(ValueError, "输入文件已变化|输入哈希"):
            image_guard.preflight(self.request)

    def test_new_material_inside_approved_scope_invalidates_approval(self):
        (self.materials / "new-brief.txt").write_text("new information", encoding="utf-8")
        with self.assertRaises(ValueError):
            image_guard.preflight(self.request)

    def test_new_bound_plan_cannot_switch_to_single_image_tool(self):
        request = copy.deepcopy(self.request)
        page = request["arguments"]["tasks"][1]
        request["id"] = page["id"]
        request["tool"] = "edit_image"
        request["arguments"] = {key: value for key, value in page.items() if key != "id"}
        for key in ("visual_strength_by_task", "source_image_ids_by_task", "source_images_by_task"):
            request["reference_context"][key].pop("p02")
        with self.assertRaisesRegex(ValueError, "只支持"):
            image_guard.preflight(request)


if __name__ == "__main__":
    unittest.main()
