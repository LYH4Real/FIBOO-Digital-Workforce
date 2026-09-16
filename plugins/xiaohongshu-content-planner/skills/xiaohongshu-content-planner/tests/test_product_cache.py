"""Offline tests: product-cache provenance, image records and byte freshness."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import product_cache as cache
import workflow_guard as guard


class ProductCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.materials = self.root / "materials"
        self.materials.mkdir()
        self.source = self.materials / "label.txt"
        self.source.write_text("Example product / current label", encoding="utf-8")
        self.evidence = self.materials / "user-confirmation.txt"
        self.evidence.write_text("User supplied confirmation record", encoding="utf-8")
        self.image = self.materials / "product.png"
        # Signature fixture only: tests validate schema/hash, never infer vision.
        self.image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fixture-image-data")
        self.facts = {
            "schema_version": 1, "product_scope": "example / current pack",
            "sources": [{"id": "label", "kind": "file", "path": str(self.source), "locator": "line 1"}],
            "facts": {
                "product_name": {"status": "confirmed", "required_for_plan": True,
                                 "value": "Example", "source_id": "label"},
                "core_claims": {"status": "confirmed", "required_for_plan": True,
                                "value": ["Example claim"], "source_id": "label"},
                "dosage": {"status": "missing", "required_for_plan": False},
                "packaging": {"status": "confirmed", "required_for_plan": True,
                              "value": "Example pack", "source_id": "label"},
            },
        }
        self.assets = {
            "schema_version": 1, "product_scope": self.facts["product_scope"],
            "images": [{"id": "product-front", "path": str(self.image),
                        "sha256": guard.sha256_file(self.image), "role": "pack_front",
                        "product_scope": self.facts["product_scope"], "semantic_status": "verified",
                        "inspection": {"method": "human_confirmed", "inspected_at": "2026-09-08T00:00:00Z"},
                        "observation": {"summary": "An example front-facing pack",
                                        "visible_text": "Example", "composition": "centered",
                                        "shot": "front", "lighting": "room light", "style": "phone photo",
                                        "imperfections": "slight tilt"}}],
        }
        self.inventory = guard.inventory(self.materials)
        self.facts_path = self.root / "facts.json"
        self.assets_path = self.root / "assets.json"
        self.inventory_path = self.root / "inventory.json"
        self.output = self.root / "caches"

    def write_json(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_inputs(self):
        for path, value in ((self.facts_path, self.facts), (self.assets_path, self.assets),
                            (self.inventory_path, self.inventory)):
            self.write_json(path, value)

    def build(self, output=None):
        self.write_inputs()
        return cache.build_cache(self.facts_path, self.assets_path, self.inventory_path, output or self.output)

    def read_cache(self, result):
        return cache.load_valid_cache(result["cache_path"])

    def resolve_name(self):
        self.facts["sources"].append({"id": "user", "kind": "user_confirmation",
                                      "path": str(self.evidence), "locator": "message 1"})
        self.facts["facts"]["product_name"] = {
            "status": "conflict", "required_for_plan": True,
            "candidates": [{"value": "Old", "source_id": "label"},
                           {"value": "New", "source_id": "user"}],
            "resolution": {"value": "New", "source": "user_confirmation", "source_id": "user",
                           "evidence": "User supplied choice is recorded in message 1"},
        }

    def test_round_trip_preserves_full_inputs_and_records_original_hashes(self):
        self.facts["extra_metadata"] = {"product_version": "provided-v1", "do_not_infer": True}
        result = self.build()
        document = self.read_cache(result)
        self.assertEqual(document["facts"], self.facts)
        self.assertEqual(document["assets"], self.assets)
        self.assertEqual(document["inventory"], self.inventory)
        self.assertEqual(document["kind"], "product_cache")
        self.assertEqual(len(document["input_artifacts"]), 3)
        for item in document["input_artifacts"]:
            self.assertTrue(Path(item["path"]).is_absolute())
            self.assertEqual(item["sha256"], guard.sha256_file(item["path"]))
        self.assertEqual(result["image_count"], 1)
        self.assertTrue(Path(result["context_path"]).is_file())

    def test_each_build_gets_new_directory_without_overwriting_old_cache(self):
        first = self.build()
        before = Path(first["cache_path"]).read_bytes()
        second = self.build()
        self.assertNotEqual(first["cache_path"], second["cache_path"])
        self.assertEqual(Path(first["cache_path"]).read_bytes(), before)

    def test_source_same_path_size_and_mtime_content_change_invalidates(self):
        result = self.build()
        stamp = self.source.stat()
        self.source.write_bytes(b"X" * stamp.st_size)
        os.utime(self.source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "内容变化"):
            self.read_cache(result)

    def test_image_changed_same_path_invalidates(self):
        result = self.build()
        stamp = self.image.stat()
        self.image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"other-image-data!!")
        os.utime(self.image, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "内容变化"):
            self.read_cache(result)

    def test_added_and_deleted_material_invalidate(self):
        result = self.build()
        added = self.materials / "new-version.txt"
        added.write_text("new", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "新增"):
            self.read_cache(result)
        added.unlink()
        self.source.unlink()
        with self.assertRaisesRegex(ValueError, "删除"):
            self.read_cache(result)

    def test_mtime_only_change_does_not_invalidate(self):
        result = self.build()
        stamp = self.source.stat()
        os.utime(self.source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1000000000))
        self.assertEqual(self.read_cache(result)["facts"], self.facts)

    def test_explicit_excluded_output_is_not_scanned(self):
        excluded = self.materials / "outputs"
        self.inventory = guard.inventory(self.materials, exclude=["outputs"])
        result = self.build(output=excluded)
        self.assertTrue(self.read_cache(result))
        (excluded / "unrelated.txt").write_text("changed result", encoding="utf-8")
        self.assertTrue(self.read_cache(result))

    def test_output_inside_selected_materials_refused_before_writing(self):
        destination = self.materials / "caches"
        with self.assertRaisesRegex(ValueError, "输出目录"):
            self.build(output=destination)
        self.assertFalse(destination.exists())

    def test_old_inventory_without_explicit_scope_rejected(self):
        del self.inventory["scope"]
        with self.assertRaisesRegex(ValueError, "include/exclude"):
            self.build()

    def test_stale_inventory_rejected_at_build(self):
        self.source.write_text("changed before build", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "内容变化"):
            self.build()
        self.assertFalse(self.output.exists())

    def test_sources_and_assets_must_be_selected_not_just_under_root(self):
        for excluded in ("label.txt", "product.png"):
            with self.subTest(excluded=excluded):
                self.inventory = guard.inventory(self.materials, exclude=[excluded])
                with self.assertRaisesRegex(ValueError, "未纳入"):
                    self.build()

    def test_source_locator_and_usable_source_id_required(self):
        original = copy.deepcopy(self.facts)
        del self.facts["sources"][0]["locator"]
        with self.assertRaisesRegex(ValueError, "locator"):
            self.build()
        self.facts = copy.deepcopy(original)
        del self.facts["facts"]["core_claims"]["source_id"]
        with self.assertRaisesRegex(ValueError, "source_id"):
            self.build()
        self.facts["facts"]["core_claims"]["source_id"] = "unknown"
        with self.assertRaisesRegex(ValueError, "source_id"):
            self.build()

    def test_critical_conflict_blocks_even_when_marked_confirmed(self):
        self.facts["facts"]["product_name"]["candidates"] = [
            {"value": "Old", "source_id": "label"}, {"value": "New", "source_id": "label"}]
        with self.assertRaisesRegex(ValueError, "HITL"):
            self.build()

    def test_resolution_references_user_evidence_file_and_locator(self):
        self.resolve_name()
        result = self.build()
        self.assertEqual(self.read_cache(result)["facts"], self.facts)
        self.assertIn("不证明", result["validation_scope"])
        self.evidence.write_text("another confirmation", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "内容变化"):
            self.read_cache(result)

    def test_resolution_free_text_is_not_enough(self):
        self.resolve_name()
        del self.facts["facts"]["product_name"]["resolution"]["source_id"]
        with self.assertRaisesRegex(ValueError, "用户确认证据"):
            self.build()

    def test_resolution_source_must_be_user_kind_and_real_file(self):
        self.resolve_name()
        self.facts["facts"]["product_name"]["resolution"]["source_id"] = "label"
        with self.assertRaisesRegex(ValueError, "用户确认证据"):
            self.build()
        self.facts["facts"]["product_name"]["resolution"]["source_id"] = "user"
        del self.facts["sources"][1]["path"]
        with self.assertRaisesRegex(ValueError, "path"):
            self.build()

    def test_conflicting_value_and_resolution_not_silently_chosen(self):
        self.resolve_name()
        self.facts["facts"]["product_name"]["value"] = "Old"
        with self.assertRaisesRegex(ValueError, "不一致"):
            self.build()

    def test_unknown_optional_fact_preserved_but_never_usable(self):
        self.facts["facts"]["optional_taste"] = {
            "status": "conflict", "required_for_plan": False,
            "candidates": [{"value": "A", "source_id": "label"}, {"value": "B", "source_id": "label"}]}
        result = self.build()
        self.assertEqual(result["usable_fact_count"], 3)
        self.assertEqual(result["pending_fact_count"], 2)
        self.facts["facts"]["optional_taste"]["required_for_plan"] = True
        with self.assertRaisesRegex(ValueError, "HITL"):
            self.build()

    def test_required_missing_and_empty_confirmed_fact_fail(self):
        self.facts["facts"]["dosage"]["required_for_plan"] = True
        with self.assertRaisesRegex(ValueError, "尚未确认"):
            self.build()
        self.facts["facts"]["dosage"]["required_for_plan"] = False
        self.facts["facts"]["core_claims"]["value"] = "  "
        with self.assertRaisesRegex(ValueError, "不得为空"):
            self.build()

    def test_zero_images_allowed_with_explicit_follow_up_warning(self):
        self.assets["images"] = []
        result = self.build()
        self.assertEqual(self.read_cache(result)["assets"]["images"], [])
        self.assertEqual(result["image_count"], 0)
        self.assertIn("补图", " ".join(result["warnings"]))

    def test_visual_schema_requires_actual_view_method_role_and_scope(self):
        original = copy.deepcopy(self.assets)
        for field, value, message in (("role", "", "role"), ("product_scope", "other pack", "product_scope"),
                                      ("sha256", "0" * 64, "哈希")):
            with self.subTest(field=field):
                self.assets = copy.deepcopy(original)
                self.assets["images"][0][field] = value
                with self.assertRaisesRegex(ValueError, message):
                    self.build()
        self.assets = copy.deepcopy(original)
        for method in ("filename_only", "ocr_only"):
            self.assets["images"][0]["inspection"]["method"] = method
            with self.assertRaisesRegex(ValueError, "实际看图"):
                self.build()

    def test_incomplete_observation_and_cross_scope_assets_fail(self):
        del self.assets["images"][0]["observation"]["composition"]
        with self.assertRaisesRegex(ValueError, "composition"):
            self.build()
        self.assets["images"] = []
        self.assets["product_scope"] = "other"
        with self.assertRaisesRegex(ValueError, "product_scope"):
            self.build()

    def test_nested_cache_binding_refused_even_if_null(self):
        self.facts["cache_binding"] = None
        with self.assertRaisesRegex(ValueError, "递归缓存"):
            self.build()

    def test_original_artifact_edits_invalidate_even_if_semantically_same(self):
        result = self.build()
        with self.facts_path.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        with self.assertRaisesRegex(ValueError, "原始 product_facts"):
            self.read_cache(result)

    def test_cached_payload_edit_cannot_bypass_original_binding(self):
        result = self.build()
        document = self.read_cache(result)
        document["facts"]["facts"]["product_name"]["value"] = "Tampered"
        self.write_json(Path(result["cache_path"]), document)
        with self.assertRaisesRegex(ValueError, "原始记录不一致"):
            self.read_cache(result)

    def test_duplicate_or_missing_artifact_binding_rejected(self):
        result = self.build()
        original = self.read_cache(result)
        document = copy.deepcopy(original)
        document["input_artifacts"][1] = copy.deepcopy(document["input_artifacts"][0])
        self.write_json(Path(result["cache_path"]), document)
        with self.assertRaisesRegex(ValueError, "重复"):
            self.read_cache(result)
        document = copy.deepcopy(original)
        document["input_artifacts"].pop()
        self.write_json(Path(result["cache_path"]), document)
        with self.assertRaisesRegex(ValueError, "三个"):
            self.read_cache(result)

    def test_loader_rescans_scope_only_once(self):
        result = self.build()
        with patch.object(cache, "validate_inventory_snapshot", wraps=guard.validate_inventory_snapshot) as scan:
            self.read_cache(result)
        self.assertEqual(scan.call_count, 1)

    def test_cli_is_offline_concise_and_returns_nonzero_for_stale_cache(self):
        self.write_inputs()
        command = [sys.executable, "-B", "-X", "utf8", str(SCRIPTS / "product_cache.py")]
        built = subprocess.run(command + ["build", "--facts", str(self.facts_path), "--assets", str(self.assets_path),
                                          "--inventory", str(self.inventory_path), "--output-root", str(self.output)],
                               capture_output=True, encoding="utf-8")
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        result = json.loads(built.stdout)
        self.assertNotIn("Example claim", built.stdout)
        check = command + ["check", "--cache", result["cache_path"]]
        valid = subprocess.run(check, capture_output=True, encoding="utf-8")
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        self.assertNotIn("observation", valid.stdout)
        self.source.write_text("changed", encoding="utf-8")
        stale = subprocess.run(check, capture_output=True, encoding="utf-8")
        self.assertEqual(stale.returncode, 2)
        self.assertFalse(json.loads(stale.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
