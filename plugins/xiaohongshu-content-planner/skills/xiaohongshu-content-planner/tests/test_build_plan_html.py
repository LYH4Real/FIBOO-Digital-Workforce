"""Tests for the concise read-only planning HTML renderer."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_plan_html.py"


class PlanHtmlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "plan.json"
        self.output = self.root / "plan.html"
        self.plan = {
            "schema_version": 1, "status": "awaiting_user_approval", "task_id": "task-1",
            "title": "<script>alert(1)</script>", "strategy": "phone snapshot",
            "reference_contract": {"narrative": "borrow", "composition": "strict",
                                   "image_style": "strict", "copy_tone": "borrow"},
            "title_candidates": ["Title 1"], "body_copy": "Line one\nLine two",
            "pages": [{"id": "task-1-p01", "purpose": "cover", "visual_blueprint": "off-center",
                       "on_image_text": ["Text"], "reference_image_ids": ["source-p01"],
                       "product_image_paths": [], "prompt": "prompt", "size": "1248x1664",
                       "product_required": False, "ugc_required": True}],
            "questions": ["Approve this plan?"],
        }

    def test_renders_read_only_html_and_escapes_content(self):
        self.source.write_text(json.dumps(self.plan), encoding="utf-8")
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", str(SCRIPT),
                                 "--input", str(self.source), "--output", str(self.output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        html = self.output.read_text(encoding="utf-8")
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("contenteditable", html.lower())
        self.assertNotIn("<form", html.lower())
        self.assertIn("等待用户确认", html)

    def test_rejects_overwrite(self):
        self.source.write_text(json.dumps(self.plan), encoding="utf-8")
        self.output.write_text("existing", encoding="utf-8")
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--input", str(self.source),
                                 "--output", str(self.output)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "existing")


if __name__ == "__main__":
    unittest.main()
