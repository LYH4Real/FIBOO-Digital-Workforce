"""Synthetic offline integration tests; fixtures are not real product or approval evidence."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import workflow_guard as guard
import product_cache
import prepare_task
import prepare_generation


class FastTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.product_root = self.root / "product"
        self.inputs = self.root / "task-input"
        for path in (self.product_root, self.inputs):
            path.mkdir()
        self.source = self.product_root / "label.txt"
        self.source.write_text("Synthetic label", encoding="utf-8")
        self.product = self.product_root / "product.png"
        self.reference = self.inputs / "reference.png"
        for path in (self.product, self.reference):
            path.write_bytes(b"\x89PNG\r\n\x1a\nfixture" + path.name.encode())
        self.brief = self.inputs / "brief.txt"
        self.brief.write_text("Synthetic post brief", encoding="utf-8")
        self.facts = {"schema_version": 1, "product_scope": "synthetic one variant",
                      "sources": [{"id": "label", "kind": "file", "path": str(self.source), "locator": "line 1"}],
                      "facts": {name: {"status": "confirmed", "required_for_plan": name != "dosage",
                                        "value": "synthetic", "source_id": "label", "candidates": []}
                                for name in guard.CRITICAL_FACTS}}
        self.assets = {"schema_version": 1, "product_scope": self.facts["product_scope"],
                       "images": [self.image_item("product", self.product)]}
        self.assets["images"][0].update(role="front", product_scope=self.facts["product_scope"])
        facts_path = self.write("facts.json", self.facts)
        assets_path = self.write("assets.json", self.assets)
        inv_path = self.write("product-inventory.json", guard.inventory(self.product_root))
        result = product_cache.build_cache(facts_path, assets_path, inv_path, self.root / "caches")
        self.cache = result["cache_path"]
        self.context = prepare_task.make_context(cache=self.cache, output_root=self.root / "contexts")["context"]
        self.inventory = self.write("task-inventory.json", guard.inventory(self.inputs))
        self.manifest = self.write("reference-manifest.json", {"schema_version": 1, "images": [self.image_item("ref", self.reference)]})
        self.plan = {"schema_version": 1, "status": "awaiting_user_approval", "task_id": "synthetic",
                     "strategy": "useful content", "title_candidates": ["title"], "body_copy": "body",
                     "fact_keys_used": ["product_name"],
                     "reference_contract": {key: "strict" for key in ("narrative", "composition", "image_style", "copy_tone")},
                     "pages": [{"id": "p01", "purpose": "one useful fact", "visual_blueprint": "phone framing",
                                "prompt": "【参考强度】\n严格参考。Synthetic prompt preserved.", "size": "1248x1664",
                                "reference_image_ids": ["ref"], "product_image_paths": [str(self.product)],
                                "product_required": True, "ugc_required": True}], "questions": []}
        self.plan_path = self.write("draft.json", self.plan)

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def image_item(self, ident, path):
        return {"id": ident, "path": str(path), "sha256": guard.sha256_file(path), "page_index": 1,
                "semantic_status": "verified", "inspection": {"method": "human_confirmed", "inspected_at": "2026-09-08T00:00:00Z"},
                "observation": {key: "synthetic observation" for key in
                                ("summary", "visible_text", "composition", "shot", "lighting", "style", "imperfections", "product_or_brand")}}

    def package(self, **options):
        args = dict(context=self.context, inventory=self.inventory, reference_manifest=self.manifest,
                    plan=self.plan_path, output_root=self.root / "plans")
        args.update(options)
        return prepare_task.package_plan(**args)

    def test_context_then_plan_preserves_creative_content_and_stops(self):
        result = self.package()
        self.assertFalse(result["network_called"])
        paths = result["paths"]
        doc = guard.load(paths["plan"])
        self.assertEqual(doc["pages"], self.plan["pages"])
        self.assertEqual(doc["body_copy"], self.plan["body_copy"])
        self.assertIn("input_binding", doc)
        self.assertIn("尚未开始生图", Path(paths["plan_html"]).read_text(encoding="utf-8"))
        self.assertFalse(any("approval" in path.name for path in Path(paths["plan"]).parent.iterdir()))
        self.assertTrue(guard.load(paths["facts_report"])["ok"])

    def test_product_file_change_invalidates_context(self):
        self.source.write_text("Changed same product name", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.package()
        self.assertFalse((self.root / "plans").exists())

    def test_new_brief_before_render_blocks(self):
        (self.inputs / "new-brief.txt").write_text("new", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.package()

    def test_context_cannot_override_facts(self):
        doc = guard.load(self.context)
        doc["facts"]["facts"]["product_name"]["value"] = "invented"
        Path(self.context).write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.package()

    def test_unknown_fact_or_product_image_blocks(self):
        self.plan["fact_keys_used"] = ["invented_fact"]
        self.write("draft.json", self.plan)
        with self.assertRaises(ValueError):
            self.package()
        self.plan["fact_keys_used"] = ["product_name"]
        self.plan["pages"][0]["product_image_paths"] = [str(self.reference)]
        self.write("draft.json", self.plan)
        with self.assertRaises(ValueError):
            self.package()

    def test_output_may_not_contaminate_input_scope(self):
        with self.assertRaisesRegex(ValueError, "扫描范围"):
            self.package(output_root=self.inputs / "output")

    def test_invalid_generation_parameters_block_before_html(self):
        original = copy.deepcopy(self.plan)
        for changes in ({"size": "1x1"}, {"n": 5}, {"tool": "generate_image"},
                        {"reference_image_ids": ["ref", "ref"]}, {"response_format": "png"}):
            self.plan = copy.deepcopy(original)
            self.plan["pages"][0].update(changes)
            self.write("draft.json", self.plan)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.package()
        self.assertFalse((self.root / "plans").exists())

    def approval(self, result):
        paths = result["paths"]
        return self.write("synthetic-approval.json", {
            "schema_version": 1, "status": "approved", "approved_by": "user",
            "plan_json": paths["plan"], "plan_html": paths["plan_html"],
            "plan_sha256": guard.sha256_file(paths["plan"]), "plan_html_sha256": guard.sha256_file(paths["plan_html"]),
            "approval_evidence": "Synthetic fixture only", "approved_at": "2026-09-08T00:00:00Z"})

    def compile(self, result, approval):
        keys = ("plan", "facts", "facts_report", "reference_manifest", "reference_report")
        return prepare_generation.prepare(**{key: result["paths"][key] for key in keys},
                                          approval=approval, output_root=self.root / "requests")

    def test_full_offline_chain_preserves_reference_and_product_images(self):
        result = self.package()
        compiled = self.compile(result, self.approval(result))
        task = guard.load(compiled["request"])["arguments"]["tasks"][0]
        self.assertEqual(task["image_paths"], [str(self.reference), str(self.product)])
        self.assertEqual(task["prompt"], self.plan["pages"][0]["prompt"])

    def test_changed_task_brief_after_approval_blocks_generation(self):
        result = self.package()
        approval = self.approval(result)
        self.brief.write_text("new brief after approval", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.compile(result, approval)
        self.assertFalse((self.root / "requests").exists())

    def test_independent_plan_bundles_do_not_overwrite(self):
        first = self.package()["paths"]["plan"]
        before = Path(first).read_bytes()
        second = self.package()["paths"]["plan"]
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
