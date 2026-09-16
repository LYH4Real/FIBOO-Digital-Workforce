"""Offline request/metadata regression tests; never generate paid images."""
from __future__ import annotations

import copy
import atexit
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
import hashlib
import shutil


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "image_request_guard.py"
SPEC = importlib.util.spec_from_file_location("image_request_guard", SCRIPT)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

FIXTURE_ROOT = Path(tempfile.mkdtemp())
atexit.register(shutil.rmtree, FIXTURE_ROOT, ignore_errors=True)
PLAN = FIXTURE_ROOT / "plan.json"
PLAN_HTML = FIXTURE_ROOT / "plan.html"
APPROVAL = FIXTURE_ROOT / "approval.json"
FACTS_REPORT = FIXTURE_ROOT / "facts-report.json"
FACTS_INPUT = FIXTURE_ROOT / "facts-input.json"
REFERENCE_REPORT = FIXTURE_ROOT / "reference-report.json"
REFERENCE_MANIFEST = FIXTURE_ROOT / "reference-manifest.json"
SOURCE_A = FIXTURE_ROOT / "source-a.png"
SOURCE_B = FIXTURE_ROOT / "source-b.jpg"
SOURCE_A.write_bytes(b"\x89PNG\r\n\x1a\n" + b"source-a")
SOURCE_B.write_bytes(b"\xff\xd8" + b"source-b")
PLAN.write_text(json.dumps({"schema_version": 1, "status": "awaiting_user_approval"}), encoding="utf-8")
PLAN_HTML.write_text("<!doctype html><title>test plan</title>", encoding="utf-8")
FACTS_INPUT.write_text(json.dumps({
    "schema_version": 1, "product_scope": "test product",
    "sources": [{"id": "user", "kind": "user_confirmation"}],
    "facts": {
        "product_name": {"status": "confirmed", "required_for_plan": True, "value": "test", "candidates": []},
        "core_claims": {"status": "confirmed", "required_for_plan": True, "value": [], "candidates": []},
        "dosage": {"status": "not_applicable", "required_for_plan": False, "reason": "test", "candidates": []},
        "packaging": {"status": "not_applicable", "required_for_plan": False, "reason": "test", "candidates": []},
    },
}), encoding="utf-8")
facts_doc = json.loads(FACTS_INPUT.read_text(encoding="utf-8"))
facts_hash = hashlib.sha256(json.dumps(facts_doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
FACTS_REPORT.write_text(json.dumps({"schema_version": 1, "stage": "product_facts", "ok": True,
                                   "input_sha256": facts_hash}), encoding="utf-8")
observation = {"summary": "test source", "visible_text": ["none"], "composition": "test layout",
               "shot": "test shot", "lighting": "test light", "style": "test style",
               "imperfections": "none", "product_or_brand": "none"}
REFERENCE_MANIFEST.write_text(json.dumps({"schema_version": 1, "images": [
    {"id": "source-script", "path": str(SOURCE_A.resolve()), "sha256": hashlib.sha256(SOURCE_A.read_bytes()).hexdigest(),
     "page_index": 1, "semantic_status": "verified",
     "inspection": {"method": "human_confirmed", "inspected_at": "2026-09-08T00:00:00Z"}, "observation": observation},
    {"id": "source-test", "path": str(SOURCE_B.resolve()), "sha256": hashlib.sha256(SOURCE_B.read_bytes()).hexdigest(),
     "page_index": 2, "semantic_status": "verified",
     "inspection": {"method": "human_confirmed", "inspected_at": "2026-09-08T00:00:00Z"}, "observation": observation},
]}), encoding="utf-8")
manifest_doc = json.loads(REFERENCE_MANIFEST.read_text(encoding="utf-8"))
manifest_hash = hashlib.sha256(json.dumps(manifest_doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
REFERENCE_REPORT.write_text(json.dumps({
    "schema_version": 1, "stage": "reference_semantics", "ok": True, "input_sha256": manifest_hash,
    "images": [{"id": item["id"], "path": item["path"], "sha256": item["sha256"]} for item in manifest_doc["images"]],
}), encoding="utf-8")
APPROVAL.write_text(json.dumps({
    "schema_version": 1, "status": "approved", "approved_by": "user",
    "plan_json": str(PLAN.resolve()), "plan_html": str(PLAN_HTML.resolve()),
    "plan_sha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(),
    "plan_html_sha256": hashlib.sha256(PLAN_HTML.read_bytes()).hexdigest(),
    "approval_evidence": "test user approved this exact plan", "approved_at": "2026-09-08T00:00:00Z",
}), encoding="utf-8")


def request(tool="generate_batch_images"):
    task = {"id": "task-01-p01", "prompt": "示例生活构图，不包含业务资料", "size": "1248x1664"}
    if "batch" in tool:
        arguments = {"tasks": [task], "output_subdir": "runs/test-run-001", "retries": 0, "concurrency": 1}
        result = {"schema_version": 2, "run_id": "test-run-001", "tool": tool, "arguments": arguments}
    else:
        arguments = {key: value for key, value in task.items() if key != "id"}
        result = {"schema_version": 2, "run_id": "test-run-001", "id": task["id"],
                  "tool": tool, "arguments": arguments}
    result["expectations"] = {task["id"]: {"ratio": "3:4", "size": "1248x1664"}}
    result["reference_context"] = {"provided": False}
    result["planning_context"] = {"facts_input": str(FACTS_INPUT.resolve()),
                                  "facts_report": str(FACTS_REPORT.resolve()),
                                  "plan_approval": str(APPROVAL.resolve())}
    return result


def chunk(kind, payload):
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def png(width, height):
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    pixels = (b"\x00" + b"\x7f" * width) * height
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def jpeg_header_fixture(width, height):
    # Structural metadata fixture, deliberately not a complete pixel encoder.
    sof = bytes([8]) + struct.pack(">HH", height, width) + bytes([1, 1, 0x11, 0])
    sos = bytes([1, 1, 0, 0, 63, 0])
    return b"\xff\xd8\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof + b"\xff\xda" + struct.pack(">H", len(sos) + 2) + sos + b"\x00\xff\xd9"


def webp_header_fixture(width, height, extended=False, lossless=True):
    # Metadata-only fixture; tests must not imply successful full decoding.
    def web_chunk(kind, payload):
        return kind + struct.pack("<I", len(payload)) + payload + b"\0" * (len(payload) % 2)
    payload = b""
    if extended:
        payload += web_chunk(b"VP8X", b"\0" * 4 + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little"))
    if lossless:
        payload += web_chunk(b"VP8L", b"\x2f" + ((width - 1) | ((height - 1) << 14)).to_bytes(4, "little"))
    else:
        payload += web_chunk(b"VP8 ", b"\0" * 3 + b"\x9d\x01\x2a" + struct.pack("<HH", width, height))
    return b"RIFF" + struct.pack("<I", len(payload) + 4) + b"WEBP" + payload


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.doc = request()
        self.task = self.doc["arguments"]["tasks"][0]

    def test_preflight_preserves_exact_arguments_and_no_mutation(self):
        before = copy.deepcopy(self.doc)
        result = guard.preflight(self.doc, "3:4")
        self.assertTrue(result["ok"])
        self.assertEqual(result["mcp_call"], {"name": self.doc["tool"], "arguments": self.doc["arguments"]})
        self.assertEqual(self.doc, before)
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["page_request_records"][0]["exact_task_arguments"], self.task)

    def test_strength_marker_whitespace_does_not_require_prompt_rewrite(self):
        for whitespace in ["", " ", "\n", "\r\n  ", "\u3000"]:
            prompt = "【参考强度】" + whitespace + "严格参考。保持生活构图"
            self.assertEqual(guard.parse_reference_strength(prompt), "strict")
        for prompt in ["无标记", "【参考强度】未知", "【参考强度】严格参考【参考强度】轻度参考",
                       "【参考强度】严格参考【参考强度】严格参考", "【参考强度】轻度参考【参考强度】未知",
                       "【参考强度】严格参考或轻度参考", "【参考强度】严格参考/轻度参考",
                       "【参考强度】严格参考度为零", "【参考强度】严格参考，或轻度参考",
                       "【参考强度】严格参考（或轻度参考）"]:
            with self.subTest(prompt=prompt), self.assertRaises(ValueError):
                guard.parse_reference_strength(prompt)

    def test_old_request_schema_cannot_bypass_planning_gate(self):
        self.doc["schema_version"] = 1
        with self.assertRaisesRegex(ValueError, "旧请求不含策划确认门"):
            guard.preflight(self.doc)

    def test_missing_or_stale_plan_approval_blocks_generation(self):
        self.doc.pop("planning_context")
        with self.assertRaisesRegex(ValueError, "planning_context"):
            guard.preflight(self.doc)
        stale = FIXTURE_ROOT / "stale-approval.json"
        stale.write_text(APPROVAL.read_text(encoding="utf-8").replace(
            hashlib.sha256(PLAN.read_bytes()).hexdigest(), "0" * 64), encoding="utf-8")
        self.doc["planning_context"] = {"facts_input": str(FACTS_INPUT.resolve()),
                                        "facts_report": str(FACTS_REPORT.resolve()),
                                        "plan_approval": str(stale.resolve())}
        with self.assertRaisesRegex(ValueError, "策划 JSON 已变化"):
            guard.preflight(self.doc)

    def test_failed_product_facts_gate_blocks_generation(self):
        blocked = FIXTURE_ROOT / "blocked-facts.json"
        blocked.write_text(json.dumps({"stage": "product_facts", "ok": False}), encoding="utf-8")
        self.doc["planning_context"]["facts_report"] = str(blocked.resolve())
        with self.assertRaisesRegex(ValueError, "产品事实冲突门"):
            guard.preflight(self.doc)

    def test_reference_context_must_be_explicit(self):
        self.doc.pop("reference_context")
        with self.assertRaisesRegex(ValueError, "reference_context"):
            guard.preflight(self.doc)

    def test_source_reference_forces_edit_tool_per_page(self):
        self.doc["reference_context"] = {
            "provided": True,
            "visual_strength_by_task": {"task-01-p01": "strict"},
            "source_image_ids_by_task": {"task-01-p01": ["source-script"]},
            "source_images_by_task": {"task-01-p01": [str(SOURCE_A.resolve())]},
        }
        self.doc["planning_context"].update({"reference_manifest": str(REFERENCE_MANIFEST.resolve()),
                                              "reference_report": str(REFERENCE_REPORT.resolve())})
        self.task["prompt"] = "【参考强度】严格参考。示例"
        with self.assertRaisesRegex(ValueError, "edit_image"):
            guard.preflight(self.doc)

    def test_source_reference_is_in_actual_edit_request_with_strength_marker(self):
        for strength, marker in guard.REFERENCE_STRENGTHS.items():
            doc = request("edit_batch_images")
            task = doc["arguments"]["tasks"][0]
            task["prompt"] = marker.replace("】", "】\n  ") + "。只继承示例构图，不继承产品与原文。"
            task["image_paths"] = [str(SOURCE_A.resolve())]
            doc["reference_context"] = {
                "provided": True,
                "visual_strength_by_task": {task["id"]: strength},
                "source_image_ids_by_task": {task["id"]: ["source-script"]},
                "source_images_by_task": {task["id"]: [str(SOURCE_A.resolve())]},
            }
            doc["planning_context"].update({"reference_manifest": str(REFERENCE_MANIFEST.resolve()),
                                             "reference_report": str(REFERENCE_REPORT.resolve())})
            with self.subTest(strength=strength):
                before = copy.deepcopy(doc)
                self.assertTrue(guard.preflight(doc)["ok"])
                self.assertEqual(doc, before)
                doc["reference_context"]["visual_strength_by_task"][task["id"]] = (
                    "light" if strength != "light" else "strict")
                with self.assertRaisesRegex(ValueError, "prompt 必须显式写"):
                    guard.preflight(doc)

    def test_semantic_reference_id_must_match_the_real_path(self):
        doc = request("edit_image")
        doc["arguments"]["prompt"] = "【参考强度】严格参考。示例"
        doc["arguments"]["image_paths"] = [str(SOURCE_A.resolve())]
        doc["reference_context"] = {
            "provided": True,
            "visual_strength_by_task": {doc["id"]: "strict"},
            "source_image_ids_by_task": {doc["id"]: ["source-test"]},
            "source_images_by_task": {doc["id"]: [str(SOURCE_A.resolve())]},
        }
        doc["planning_context"].update({"reference_manifest": str(REFERENCE_MANIFEST.resolve()),
                                         "reference_report": str(REFERENCE_REPORT.resolve())})
        with self.assertRaisesRegex(ValueError, "ID 与真实路径错配"):
            guard.preflight(doc)

    def test_declared_source_cannot_be_omitted_or_only_left_in_prompt(self):
        doc = request("edit_image")
        doc["arguments"]["prompt"] = "【参考强度】轻度参考。只学习生活语境。"
        doc["arguments"]["image_paths"] = [str(SOURCE_B.resolve())]
        doc["reference_context"] = {
            "provided": True,
            "visual_strength_by_task": {doc["id"]: "light"},
            "source_image_ids_by_task": {doc["id"]: ["source-script"]},
            "source_images_by_task": {doc["id"]: [str(SOURCE_A.resolve())]},
        }
        doc["planning_context"].update({"reference_manifest": str(REFERENCE_MANIFEST.resolve()),
                                         "reference_report": str(REFERENCE_REPORT.resolve())})
        with self.assertRaisesRegex(ValueError, "未进入实际 image_paths"):
            guard.preflight(doc)
        doc["arguments"]["image_paths"].append(str(SOURCE_A.resolve()))
        doc["arguments"]["prompt"] = "只学习生活语境。"
        with self.assertRaisesRegex(ValueError, "轻度参考"):
            guard.preflight(doc)

    def test_reference_maps_must_cover_every_page(self):
        doc = request("edit_batch_images")
        first = doc["arguments"]["tasks"][0]
        first["prompt"] = "【参考强度】严格参考。示例"
        first["image_paths"] = [str(SOURCE_A.resolve())]
        second = copy.deepcopy(first)
        second["id"] = "task-01-p02"
        doc["arguments"]["tasks"].append(second)
        doc["reference_context"] = {
            "provided": True,
            "visual_strength_by_task": {first["id"]: "strict"},
            "source_image_ids_by_task": {first["id"]: ["source-script"]},
            "source_images_by_task": {first["id"]: [str(SOURCE_A.resolve())]},
        }
        doc["planning_context"].update({"reference_manifest": str(REFERENCE_MANIFEST.resolve()),
                                         "reference_report": str(REFERENCE_REPORT.resolve())})
        with self.assertRaisesRegex(ValueError, "逐项覆盖"):
            guard.preflight(doc)

    def test_missing_size_rejected_even_when_prompt_says_vertical(self):
        self.task.pop("size")
        self.task["prompt"] = "请务必生成 3:4 竖图"
        with self.assertRaisesRegex(ValueError, "size"):
            guard.preflight(self.doc)

    def test_null_size_rejected(self):
        self.task["size"] = None
        with self.assertRaises(ValueError):
            guard.preflight(self.doc)

    def test_arbitrary_sizes_allowed_when_meeting_real_constraints(self):
        for size in ["1440x1920", "640x1024", "2880x2880", "2304x768"]:
            self.assertEqual(len(guard.size_pixels(size)), 2)

    def test_invalid_size_boundaries(self):
        for size in ["auto", "3:4", "0x1024", "3840x1024", "1254x1254", "512x512", "3008x3008", "3072x768", True]:
            with self.subTest(size=size), self.assertRaises(ValueError):
                guard.size_pixels(size)

    def test_user_ratio_conflict_rejected_without_silent_rewrite(self):
        self.task["size"] = "1024x1024"
        self.doc["expectations"][self.task["id"]].pop("size")
        with self.assertRaisesRegex(ValueError, "不要自动替换"):
            guard.preflight(self.doc)
        self.assertEqual(self.task["size"], "1024x1024")

    def test_strict_ratio_optional_and_does_not_force_portrait(self):
        self.doc.pop("expectations")
        self.task["size"] = "1664x1248"
        self.assertTrue(guard.preflight(self.doc)["ok"])
        self.assertTrue(guard.preflight(self.doc, "4:3")["ok"])
        with self.assertRaises(ValueError):
            guard.preflight(self.doc, "3:4")

    def test_same_ratio_different_user_size_rejected(self):
        self.task["size"] = "1440x1920"
        with self.assertRaises(ValueError):
            guard.preflight(self.doc)

    def test_ids_required_unique_and_stable(self):
        self.doc["arguments"]["tasks"].append(copy.deepcopy(self.task))
        with self.assertRaisesRegex(ValueError, "重复"):
            guard.preflight(self.doc)
        self.doc["arguments"]["tasks"].pop()
        self.task.pop("id")
        with self.assertRaises(ValueError):
            guard.preflight(self.doc)

    def test_blank_prompt_and_unknown_argument_rejected(self):
        self.task["prompt"] = "  "
        with self.assertRaises(ValueError):
            guard.preflight(self.doc)
        self.task["prompt"] = "valid"
        self.doc["arguments"]["size"] = "1248x1664"
        with self.assertRaisesRegex(ValueError, "未知"):
            guard.preflight(self.doc)

    def test_unrelated_expectation_rejected(self):
        self.doc["expectations"]["other"] = {"ratio": "3:4"}
        with self.assertRaises(ValueError):
            guard.preflight(self.doc)

    def test_explicit_null_ratio_rejected_not_treated_as_absent(self):
        self.doc["expectations"]["task-01-p01"]["ratio"] = None
        with self.assertRaisesRegex(ValueError, "比例"):
            guard.preflight(self.doc)

    def test_actual_output_name_collisions_rejected(self):
        for left, n, right in [("cover", 1, "cover_"), ("cover", 1, "cover."),
                               ("Cover", 1, "cover"), ("cover", 2, "cover-1"),
                               ("cover_", 2, "Cover-2")]:
            for tool in ("generate_batch_images", "edit_batch_images"):
                doc = request(tool)
                doc.pop("expectations")
                doc["arguments"]["tasks"] = [
                    {"id": left, "n": n, "prompt": "test", "size": "1024x1024"},
                    {"id": right, "prompt": "test", "size": "1024x1024"},
                ]
                if tool.startswith("edit"):
                    for item in doc["arguments"]["tasks"]:
                        item["image_paths"] = [str(SOURCE_A.resolve())]
                with self.subTest(left=left, n=n, right=right, tool=tool), self.assertRaisesRegex(ValueError, "碰撞"):
                    guard.preflight(doc)

    def test_noncolliding_multiimage_output_stems_are_reported(self):
        self.task["n"] = 2
        report = guard.preflight(self.doc)
        self.assertEqual(report["checks"][0]["predicted_output_stems"], ["task-01-p01-1", "task-01-p01-2"])

    def test_single_filename_uses_real_path_stem_and_sanitization(self):
        single = request("generate_image")
        for filename, expected in [("cover.png", "cover"), ("cover.jpg", "cover"),
                                   ("cover_.png", "cover"), ("cover.final.png", "cover.final")]:
            single["arguments"]["filename"] = filename
            report = guard.preflight(single)
            self.assertEqual(report["checks"][0]["predicted_output_stems"], [expected])
            self.assertEqual(report["mcp_call"]["arguments"]["filename"], filename)

    def test_windows_reserved_output_device_names_rejected(self):
        for name in ["NUL.png", "con", "aux.jpg", "LPT1.final.png", "COM9_.png"]:
            single = request("generate_image")
            single["arguments"]["filename"] = name
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "保留设备名"):
                guard.preflight(single)
        self.doc.pop("expectations")
        self.task["id"] = "NUL"
        with self.assertRaisesRegex(ValueError, "保留设备名"):
            guard.preflight(self.doc)

    def test_invalid_windows_directory_components_rejected(self):
        for component in ["folder\u0000bad", "CON", "NUL.data", "folder:", "bad?", "trailing.", "trailing "]:
            self.doc["arguments"]["output_subdir"] = f"runs/{component}/test-run-001"
            with self.subTest(component=component), self.assertRaises(ValueError):
                guard.preflight(self.doc)

    def test_single_filename_control_characters_rejected(self):
        single = request("generate_image")
        single["arguments"]["filename"] = "folder\u0000bad.png"
        with self.assertRaisesRegex(ValueError, "控制字符"):
            guard.preflight(single)

    def test_single_tool_does_not_require_batch_only_options(self):
        single = request("generate_image")
        self.assertTrue(guard.preflight(single)["ok"])
        self.assertTrue(any("单图未指定" in x for x in guard.preflight(single)["warnings"]))
        single["arguments"]["tasks"] = []
        with self.assertRaises(ValueError):
            guard.preflight(single)

    def test_batch_run_directory_and_traversal(self):
        for directory in [None, "fixed", "../runs/test-run-001", "C:/runs/test-run-001", "/runs/test-run-001", "C:relative"]:
            self.doc["arguments"]["output_subdir"] = directory
            with self.subTest(directory=directory), self.assertRaises(ValueError):
                guard.preflight(self.doc)

    def test_reference_paths_must_exist_and_be_absolute(self):
        for tool in ["edit_image", "edit_batch_images"]:
            with tempfile.TemporaryDirectory() as temp:
                ref = Path(temp) / "ref.png"
                ref.write_bytes(png(2, 2))
                doc = request(tool)
                item = doc["arguments"]["tasks"][0] if "batch" in tool else doc["arguments"]
                item["image_paths"] = [str(ref)]
                self.assertTrue(guard.preflight(doc)["ok"])
                for invalid in ["ref.png", "https://example.test/ref.png", str(ref.with_name("missing.png"))]:
                    item["image_paths"] = [invalid]
                    with self.assertRaises(ValueError):
                        guard.preflight(doc)

    def test_mcp_numeric_limits_and_format(self):
        for field, value in [("n", True), ("n", 5), ("n", 0), ("response_format", "png")]:
            doc = copy.deepcopy(self.doc)
            doc["arguments"]["tasks"][0][field] = value
            with self.assertRaises(ValueError):
                guard.preflight(doc)
        for field, value in [("concurrency", True), ("concurrency", 17), ("retries", -1), ("retries", 6)]:
            doc = copy.deepcopy(self.doc)
            doc["arguments"][field] = value
            with self.assertRaises(ValueError):
                guard.preflight(doc)


class ResultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "output.png"
        self.path.write_bytes(png(1248, 1664))
        self.doc = request()
        self.result = {"success": True, "results": [{"id": "task-01-p01", "success": True, "images": [str(self.path)]}]}

    def test_exact_dimensions_pass_without_claiming_visual_or_decode_check(self):
        report = guard.inspect_result(self.doc, self.result)
        self.assertTrue(report["ok"])
        self.assertFalse(report["full_decode_performed"])
        self.assertTrue(report["visual_review_required"])
        self.assertEqual(report["review_mode"], "human_on_delivery")
        self.assertFalse(report["automatic_visual_review"])
        self.assertEqual(report["visual_review_status"], "pending_human_review")
        self.assertFalse(report["publish_ready"])
        self.assertTrue(report["tasks"][0]["images"][0]["pixels_match"])

    def test_inspection_cannot_claim_visual_approval_from_mcp_result(self):
        original = self.path.read_bytes()
        self.result.update({"visual_review_required": False, "automatic_visual_review": True,
                            "visual_review_status": "passed", "publish_ready": True})
        for dimensions, expected_ok in [((1248, 1664), True), ((1254, 1254), False)]:
            with self.subTest(dimensions=dimensions):
                content = png(*dimensions)
                self.path.write_bytes(content)
                report = guard.inspect_result(self.doc, self.result)
                self.assertEqual(report["ok"], expected_ok)
                self.assertTrue(report["visual_review_required"])
                self.assertFalse(report["automatic_visual_review"])
                self.assertEqual(report["visual_review_status"], "pending_human_review")
                self.assertFalse(report["publish_ready"])
                self.assertEqual(self.path.read_bytes(), content)
        self.path.write_bytes(original)

    def test_square_actual_image_fails_despite_mcp_success(self):
        self.path.write_bytes(png(1254, 1254))
        report = guard.inspect_result(self.doc, self.result)
        self.assertFalse(report["ok"])
        image = report["tasks"][0]["images"][0]
        self.assertFalse(image["pixels_match"])
        self.assertFalse(image["ratio_match"])

    def test_correct_ratio_wrong_pixels_still_fails(self):
        self.path.write_bytes(png(1440, 1920))
        image = guard.inspect_result(self.doc, self.result)["tasks"][0]["images"][0]
        self.assertTrue(image["ratio_match"])
        self.assertFalse(image["pixels_match"])
        self.assertFalse(image["ok"])

    def test_bad_or_unknown_image_is_not_accepted(self):
        for content in [b"not an image", png(1248, 1664)[:33], b"RIFF\0\0\0\0WEBP"]:
            self.path.write_bytes(content)
            self.assertFalse(guard.inspect_result(self.doc, self.result)["ok"])

    def test_png_crc_corruption_rejected(self):
        content = bytearray(png(1248, 1664))
        content[29] ^= 1
        self.path.write_bytes(content)
        report = guard.inspect_result(self.doc, self.result)
        self.assertFalse(report["ok"])
        self.assertIn("CRC", report["tasks"][0]["images"][0]["error"])

    def test_jpeg_metadata_from_signature_not_extension(self):
        self.path.write_bytes(jpeg_header_fixture(1248, 1664))
        metadata = guard.image_metadata(self.path)
        self.assertEqual((metadata["format"], metadata["width"], metadata["height"]), ("JPEG", 1248, 1664))

    def test_webp_lossy_lossless_and_extended_metadata(self):
        for extended, lossless in [(False, True), (False, False), (True, True), (True, False)]:
            self.path.write_bytes(webp_header_fixture(1248, 1664, extended, lossless))
            metadata = guard.image_metadata(self.path)
            self.assertEqual((metadata["format"], metadata["width"], metadata["height"]), ("WebP", 1248, 1664))

    def test_webp_truncated_rejected(self):
        self.path.write_bytes(webp_header_fixture(1248, 1664)[:-1])
        with self.assertRaises(ValueError):
            guard.image_metadata(self.path)

    def test_missing_unknown_and_duplicate_results(self):
        self.result["results"] = []
        self.assertFalse(guard.inspect_result(self.doc, self.result)["ok"])
        self.result["results"] = [{"id": "unknown", "success": True, "images": [str(self.path)]}]
        with self.assertRaises(ValueError):
            guard.inspect_result(self.doc, self.result)
        self.result["results"][0]["id"] = "task-01-p01"
        self.result["results"].append(copy.deepcopy(self.result["results"][0]))
        with self.assertRaises(ValueError):
            guard.inspect_result(self.doc, self.result)

    def test_failed_task_missing_image_and_wrong_count_fail(self):
        self.result["results"][0]["success"] = False
        self.assertFalse(guard.inspect_result(self.doc, self.result)["ok"])
        self.result["results"][0]["success"] = True
        self.doc["arguments"]["tasks"][0]["n"] = 2
        self.assertFalse(guard.inspect_result(self.doc, self.result)["ok"])
        self.path.unlink()
        self.assertFalse(guard.inspect_result(self.doc, self.result)["ok"])

    def test_single_image_raw_and_mcp_wrapped_results(self):
        doc = request("generate_image")
        result = {"success": True, "images": [str(self.path)]}
        for wrapped in [result, {"structuredContent": result},
                        {"content": [{"type": "text", "text": json.dumps(result)}]},
                        {"jsonrpc": "2.0", "result": {"structuredContent": result}}]:
            self.assertTrue(guard.inspect_result(doc, wrapped)["ok"])

    def test_inspect_does_not_require_original_references_still_exist(self):
        doc = request("edit_image")
        doc["arguments"]["image_paths"] = [str(Path(self.temp.name) / "old-ref.png")]
        self.assertTrue(guard.inspect_result(doc, {"success": True, "images": [str(self.path)]})["ok"])

    def test_repeated_output_path_does_not_count_as_two_images(self):
        self.doc["arguments"]["tasks"][0]["n"] = 2
        self.result["results"][0]["images"] *= 2
        self.assertFalse(guard.inspect_result(self.doc, self.result)["ok"])

    def test_cli_exit_codes_and_refuses_overwrite(self):
        root = Path(self.temp.name)
        request_file, result_file, output = root / "request.json", root / "result.json", root / "report.json"
        request_file.write_text(json.dumps(self.doc), encoding="utf-8")
        result_file.write_text(json.dumps(self.result), encoding="utf-8")
        command = [sys.executable, "-B", "-X", "utf8", str(SCRIPT)]
        passed = subprocess.run(command + ["preflight", "--input", str(request_file), "--output", str(output)], capture_output=True, text=True)
        self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
        original = output.read_bytes()
        conflict = subprocess.run(command + ["preflight", "--input", str(request_file), "--output", str(output)], capture_output=True)
        self.assertEqual(conflict.returncode, 2)
        self.assertEqual(output.read_bytes(), original)
        self.path.write_bytes(png(1254, 1254))
        mismatch = subprocess.run(command + ["inspect", "--request", str(request_file), "--result", str(result_file)], capture_output=True)
        self.assertEqual(mismatch.returncode, 1)


if __name__ == "__main__":
    unittest.main()
