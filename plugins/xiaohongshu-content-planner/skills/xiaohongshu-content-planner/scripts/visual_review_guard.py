"""Validate independent, tool-backed visual comparisons and choose retry actions.

The independent vision evaluator writes one raw JSON file per generated page.
This guard verifies the compared file hashes and computes pass/retry/escalate from
fixed thresholds. It never accepts an Agent-authored "目检通过" statement.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from workflow_guard import absolute_file, emit, load, nonempty, require, sha256_file


STRENGTH_THRESHOLDS = {
    "none": {"composition": 60, "style": 60},
    "strict": {"composition": 85, "style": 80},
    "moderate": {"composition": 70, "style": 70},
    "light": {"composition": 50, "style": 60},
}
INDEPENDENT_METHODS = {"independent_vision_model", "vision_evaluation_tool", "human_review"}


def bounded_score(value, label):
    require(type(value) in (int, float) and 0 <= value <= 100, f"{label} 必须是 0–100 数值")
    return float(value)


def validate_raw_review(raw, page, check_paths=True):
    require(isinstance(raw, dict) and raw.get("schema_version") == 1,
            "视觉评估原始记录 schema_version 必须为 1")
    require(raw.get("page_id") == page.get("id"), "视觉评估 page_id 与页面不一致")
    evaluator = raw.get("evaluator")
    require(isinstance(evaluator, dict), "视觉评估必须包含 evaluator")
    require(evaluator.get("method") in INDEPENDENT_METHODS,
            "必须使用独立视觉模型/视觉评估工具或人工复核；内容 Agent 自述目检不合格")
    nonempty(evaluator.get("name"), "evaluator.name")
    nonempty(evaluator.get("evaluated_at"), "evaluator.evaluated_at")
    generated = absolute_file(raw.get("generated_image"), "generated_image", check_paths)
    references = raw.get("reference_images")
    require(isinstance(references, list), "reference_images 必须是数组")
    if page.get("reference_strength") != "none":
        require(references, "有参考任务的视觉评估必须实际传入至少一张原帖参考图")
    reference_paths = [absolute_file(value, "reference_images", check_paths) for value in references]
    nonempty(raw.get("plan_blueprint"), "plan_blueprint")
    require(isinstance(raw.get("allowed_text"), list), "allowed_text 必须是数组")
    require(isinstance(raw.get("confirmed_fact_constraints"), list), "confirmed_fact_constraints 必须是数组")
    product_required = page.get("product_required")
    require(type(product_required) is bool, "product_required 必须为布尔值")
    product_images = raw.get("product_reference_images", [])
    require(isinstance(product_images, list), "product_reference_images 必须是数组")
    if product_required:
        require(product_images, "产品出镜页必须把真实产品参考图交给独立视觉评估")
    product_paths = [absolute_file(value, "product_reference_images", check_paths) for value in product_images]
    if check_paths:
        require(raw.get("generated_sha256") == sha256_file(generated), "成图在评估后发生变化，必须重新评估")
        expected_hashes = raw.get("reference_sha256")
        require(isinstance(expected_hashes, dict), "reference_sha256 必须记录实际对比参考图哈希")
        for path in reference_paths:
            require(expected_hashes.get(str(path)) == sha256_file(path), f"参考图在评估后变化：{path}")
        product_hashes = raw.get("product_reference_sha256", {})
        require(isinstance(product_hashes, dict), "product_reference_sha256 必须是对象")
        for path in product_paths:
            require(product_hashes.get(str(path)) == sha256_file(path), f"产品参考图在评估后变化：{path}")
    scores = raw.get("scores")
    require(isinstance(scores, dict), "scores 必须是对象")
    required = ("composition", "style", "product_accuracy", "text_accuracy", "factual_safety",
                "phone_capture_feel", "commercial_ad_risk")
    parsed = {name: bounded_score(scores.get(name), f"scores.{name}") for name in required}
    findings = raw.get("findings")
    require(isinstance(findings, list), "findings 必须是数组；通过时可为空")
    for index, finding in enumerate(findings, 1):
        require(isinstance(finding, dict), f"findings[{index}] 必须是对象")
        nonempty(finding.get("dimension"), f"findings[{index}].dimension")
        nonempty(finding.get("evidence"), f"findings[{index}].evidence")
        nonempty(finding.get("retry_instruction"), f"findings[{index}].retry_instruction")
    return parsed, findings


def review(document, check_paths=True):
    require(isinstance(document, dict) and document.get("schema_version") == 1,
            "视觉复核批次 schema_version 必须为 1")
    nonempty(document.get("run_id"), "run_id")
    maximum = document.get("max_auto_retries", 2)
    require(type(maximum) is int and 0 <= maximum <= 3, "max_auto_retries 必须是 0–3 的整数")
    pages = document.get("pages")
    require(isinstance(pages, list) and pages, "pages 必须包含待复核页面")
    results = []
    for index, page in enumerate(pages, 1):
        require(isinstance(page, dict), f"pages[{index}] 必须是对象")
        page_id = nonempty(page.get("id"), f"pages[{index}].id")
        strength = page.get("reference_strength")
        require(strength in STRENGTH_THRESHOLDS, f"{page_id}.reference_strength 无效")
        ugc_required = page.get("ugc_required")
        require(type(ugc_required) is bool, f"{page_id}.ugc_required 必须为布尔值")
        require(type(page.get("product_required")) is bool, f"{page_id}.product_required 必须为布尔值")
        attempt = page.get("attempt")
        require(type(attempt) is int and 1 <= attempt <= maximum + 1, f"{page_id}.attempt 超出允许范围")
        raw_path = absolute_file(page.get("raw_evaluation"), f"{page_id}.raw_evaluation", check_paths)
        raw = load(raw_path)
        scores, findings = validate_raw_review(raw, page, check_paths)
        threshold = STRENGTH_THRESHOLDS[strength]
        failures = []
        if scores["composition"] < threshold["composition"]:
            failures.append(f"构图 {scores['composition']:.0f} < {threshold['composition']}")
        if scores["style"] < threshold["style"]:
            failures.append(f"风格 {scores['style']:.0f} < {threshold['style']}")
        for name, label in (("product_accuracy", "产品准确"), ("text_accuracy", "文字准确"),
                            ("factual_safety", "事实安全")):
            if scores[name] < 100:
                failures.append(f"{label}必须为 100，实际 {scores[name]:.0f}")
        if ugc_required and scores["phone_capture_feel"] < 80:
            failures.append(f"手机随拍质感 {scores['phone_capture_feel']:.0f} < 80")
        if ugc_required and scores["commercial_ad_risk"] > 20:
            failures.append(f"商业广告感风险 {scores['commercial_ad_risk']:.0f} > 20")
        passed = not failures
        if passed:
            action = "accept"
        elif attempt <= maximum:
            action = "retry"
        else:
            action = "escalate_to_user_or_manual_edit"
        results.append({"id": page_id, "ok": passed, "attempt": attempt, "action": action,
                        "scores": scores, "failures": failures,
                        "retry_instructions": [item["retry_instruction"] for item in findings]})
    return {"schema_version": 1, "stage": "visual_review", "run_id": document["run_id"],
            "ok": all(item["ok"] for item in results), "max_auto_retries": maximum,
            "pages": results,
            "next_action": "deliver" if all(item["ok"] for item in results) else "follow_page_actions"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", help="新建 JSON 报告；已有文件拒绝覆盖")
    args = parser.parse_args(argv)
    try:
        value = review(load(args.input))
        emit(value, args.output)
        return 0 if value["ok"] else 1
    except (ValueError, TypeError, OSError, KeyError, json.JSONDecodeError) as exc:
        emit({"ok": False, "stage": "visual_review", "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
