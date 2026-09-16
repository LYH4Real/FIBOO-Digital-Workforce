"""Compile an approved reference-based plan into an offline MCP request.

This does not approve content, change prompts, call the MCP, or infer facts.
Only edit_batch_images is supported; every selected page needs source references.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import image_request_guard as image_guard
import workflow_guard as workflow


def option_value(plan, page, field, override):
    """CLI options must not silently replace an approved value."""
    inherited = page.get(field, plan.get(field))
    if inherited is not None and override is not None:
        workflow.require(inherited == override, f"{page['id']}.{field} 与显式参数冲突；请先修改并确认策划")
    return inherited if inherited is not None else override


def new_run_id():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:12]


def prepare(*, plan, facts, facts_report, reference_manifest, reference_report,
            approval, output_root, pages=None, model=None, response_format=None,
            concurrency=3, retries=0):
    image_guard.integer(concurrency, 1, 3, "concurrency")
    image_guard.integer(retries, 0, 5, "retries")
    if model is not None:
        workflow.nonempty(model, "model")
    workflow.require(response_format is None or response_format in {"url", "b64_json"},
                     "response_format 必须为 url 或 b64_json")
    inputs = {key: str(Path(value).resolve()) for key, value in {
        "plan": plan, "facts_input": facts, "facts_report": facts_report,
        "reference_manifest": reference_manifest, "reference_report": reference_report,
        "plan_approval": approval,
    }.items()}
    docs = {key: workflow.load(workflow.absolute_file(path, key)) for key, path in inputs.items()}
    approved = workflow.validate_approval(docs["plan_approval"])
    workflow.require(Path(approved["plan_json"]).resolve() == Path(inputs["plan"]),
                     "approval.plan_json 与本次输入策划不同，禁止借用其他策划的确认")
    current_facts = workflow.validate_facts(docs["facts_input"])
    current_refs = workflow.validate_references(docs["reference_manifest"])
    for key, expected_stage, source in (
        ("facts_report", "product_facts", "facts_input"),
        ("reference_report", "reference_semantics", "reference_manifest"),
    ):
        report = docs[key]
        workflow.require(report.get("stage") == expected_stage and report.get("ok") is True,
                         f"{key} 未通过")
        workflow.require(report.get("input_sha256") == workflow.json_sha256(docs[source]),
                         f"{key} 与当前输入哈希不一致，必须重新检查")
    plan_doc = docs["plan"]
    workflow.validate_plan(plan_doc, current_facts, current_refs)
    workflow.require(plan_doc.get("tool", "edit_batch_images") == "edit_batch_images",
                     "策划指定特殊工具；此命令只编译 edit_batch_images")
    all_pages = plan_doc["pages"]
    all_ids = [page["id"] for page in all_pages]
    if pages is not None:
        workflow.require(isinstance(pages, list) and pages, "--pages 必须至少指定一个页面ID")
        workflow.require(len(pages) == len(set(pages)), "--pages 含重复页面ID")
        workflow.require(set(pages).issubset(all_ids), "--pages 含策划中不存在的页面ID")
    selected = [page for page in all_pages if pages is None or page["id"] in pages]
    manifest_images = {item["id"]: item for item in current_refs["images"]}
    tasks, strengths, source_paths, source_ids, expectations = [], {}, {}, {}, {}
    for page in selected:
        page_id = image_guard.ident(page["id"], "page.id")
        workflow.require(page.get("tool", "edit_batch_images") == "edit_batch_images",
                         f"{page_id} 使用特殊工具；此命令只编译 edit_batch_images")
        ids = page["reference_image_ids"]
        workflow.require(ids and len(ids) == len(set(ids)), f"{page_id} 原帖参考ID为空或重复")
        paths = [manifest_images[image_id]["path"] for image_id in ids]
        products = list(page.get("product_image_paths", []))
        combined = paths + products
        workflow.require(len(combined) == len({image_guard.normalized_path(path) for path in combined}),
                         f"{page_id} 图片路径重复；请先在策划中明确图序，不自动去重")
        strength = image_guard.parse_reference_strength(page["prompt"])
        for field in ("reference_strength", "visual_strength"):
            if field in page:
                workflow.require(page[field] == strength, f"{page_id}.{field} 与prompt参考强度冲突")
        task = {"id": page_id, "prompt": page["prompt"], "size": page["size"],
                "image_paths": combined, "n": page.get("n", plan_doc.get("n", 1))}
        for field, override in (("model", model), ("response_format", response_format)):
            value = option_value(plan_doc, page, field, override)
            if value is not None:
                task[field] = value
        # Explicit image_paths can define an order not representable by this
        # minimal compiler. Refuse it rather than silently reorder numbered refs.
        if "image_paths" in page:
            workflow.require(page["image_paths"] == combined,
                             f"{page_id}.image_paths 与原帖图在前/产品图在后的顺序冲突")
        tasks.append(task)
        strengths[page_id], source_paths[page_id], source_ids[page_id] = strength, paths, list(ids)
        expectations[page_id] = {"size": page["size"]}
    run_id = new_run_id()
    request = {
        "schema_version": 2, "run_id": run_id, "tool": "edit_batch_images",
        "arguments": {"tasks": tasks, "concurrency": concurrency, "retries": retries,
                      "output_subdir": f"runs/{run_id}"},
        "expectations": expectations,
        "reference_context": {"provided": True, "visual_strength_by_task": strengths,
                              "source_images_by_task": source_paths, "source_image_ids_by_task": source_ids},
        "planning_context": {key: value for key, value in inputs.items() if key != "plan"},
    }
    report = image_guard.preflight(request)
    destination = Path(output_root).resolve() / run_id
    destination.mkdir(parents=True, exist_ok=False)
    for filename, data in (("request.json", request), ("preflight.json", report)):
        with (destination / filename).open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    return {"ok": True, "network_called": False, "run_id": run_id, "page_ids": [p["id"] for p in selected],
            "page_count": len(selected), "concurrency": concurrency, "retries": retries,
            "request": str(destination / "request.json"), "preflight": str(destination / "preflight.json"),
            "note": "仅编译已确认策划；不证明事实真实性，不执行生图或视觉验收。显式重试参数须在用户既有授权内。"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "facts", "facts-report", "reference-manifest", "reference-report", "approval", "output-root"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--pages", nargs="+", help="只编译指定页面；保持策划原页序，未知或重复ID报错")
    parser.add_argument("--model", help="只补未指定值；与策划已有值冲突时报错")
    parser.add_argument("--response-format", choices=["url", "b64_json"], help="未指定时继承策划，否则保留MCP默认")
    parser.add_argument("--concurrency", type=int, default=3, help="同批并发数，默认3，范围1–3")
    parser.add_argument("--retries", type=int, default=0, help="传输重试，默认0；显式设置须已获授权，范围0–5")
    args = parser.parse_args(argv)
    try:
        summary = prepare(**vars(args))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
