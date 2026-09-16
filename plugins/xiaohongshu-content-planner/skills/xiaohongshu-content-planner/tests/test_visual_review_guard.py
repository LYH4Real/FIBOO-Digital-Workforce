"""Tests for independent visual evaluation and bounded retry decisions."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "visual_review_guard.py"
SPEC = importlib.util.spec_from_file_location("visual_review_guard", SCRIPT)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class VisualReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reference = self.root / "reference.jpg"
        self.generated = self.root / "generated.png"
        self.reference.write_bytes(b"reference")
        self.generated.write_bytes(b"generated")
        self.raw_path = self.root / "raw.json"
        self.raw = {
            "schema_version": 1, "page_id": "task-p01",
            "evaluator": {"method": "independent_vision_model", "name": "vision-eval",
                          "evaluated_at": "2026-09-08T00:00:00Z"},
            "generated_image": str(self.generated), "generated_sha256": guard.sha256_file(self.generated),
            "reference_images": [str(self.reference)],
            "reference_sha256": {str(self.reference): guard.sha256_file(self.reference)},
            "product_reference_images": [], "product_reference_sha256": {},
            "plan_blueprint": "off-center phone snapshot", "allowed_text": ["title"],
            "confirmed_fact_constraints": ["no unsupported claims"],
            "scores": {"composition": 90, "style": 85, "product_accuracy": 100,
                       "text_accuracy": 100, "factual_safety": 100,
                       "phone_capture_feel": 85, "commercial_ad_risk": 15},
            "findings": [],
        }

    def document(self, attempt=1):
        self.raw_path.write_text(json.dumps(self.raw), encoding="utf-8")
        return {"schema_version": 1, "run_id": "run-1", "max_auto_retries": 2,
                "pages": [{"id": "task-p01", "reference_strength": "strict", "ugc_required": True,
                           "product_required": False,
                           "attempt": attempt, "raw_evaluation": str(self.raw_path)}]}

    def test_strict_ugc_thresholds_pass(self):
        report = guard.review(self.document())
        self.assertTrue(report["ok"])
        self.assertEqual(report["pages"][0]["action"], "accept")

    def test_commercial_ad_feel_causes_retry_then_escalation(self):
        self.raw["scores"]["commercial_ad_risk"] = 70
        self.raw["findings"] = [{"dimension": "ugc", "evidence": "perfect studio light",
                                  "retry_instruction": "use uneven room light and off-center phone framing"}]
        first = guard.review(self.document(attempt=1))["pages"][0]
        self.assertEqual(first["action"], "retry")
        final = guard.review(self.document(attempt=3))["pages"][0]
        self.assertEqual(final["action"], "escalate_to_user_or_manual_edit")

    def test_product_text_and_fact_scores_are_hard_gates(self):
        for key in ("product_accuracy", "text_accuracy", "factual_safety"):
            self.raw["scores"][key] = 99
            with self.subTest(key=key):
                self.assertFalse(guard.review(self.document())["ok"])
            self.raw["scores"][key] = 100

    def test_content_agent_self_review_is_rejected(self):
        self.raw["evaluator"]["method"] = "content_agent_self_review"
        with self.assertRaisesRegex(ValueError, "自述目检"):
            guard.review(self.document())

    def test_changed_image_hash_invalidates_review(self):
        doc = self.document()
        self.generated.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "成图在评估后发生变化"):
            guard.review(doc)


if __name__ == "__main__":
    unittest.main()
