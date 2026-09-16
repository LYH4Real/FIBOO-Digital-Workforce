"""Offline warm-path context and plan packaging; never approves or generates images."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import workflow_guard as guard
from build_plan_html import render
from product_cache import load_valid_cache


def new_directory(root):
    destination = Path(root).resolve() / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:10])
    destination.mkdir(parents=True, exist_ok=False)
    return destination


def write_json(directory, name, value):
    path = directory / name
    guard.emit(value, path)
    return str(path)


def require_output_outside_inventory(output_root, inventory):
    root = Path(inventory["root"]).resolve()
    destination = Path(output_root).resolve()
    def resolved(value):
        path = Path(value)
        return (path if path.is_absolute() else root / path).resolve()
    scope = inventory["scope"]
    included = any(destination == p or p in destination.parents for p in map(resolved, scope["include"]))
    excluded = any(destination == p or p in destination.parents for p in map(resolved, scope.get("exclude", [])))
    guard.require(not included or excluded, "输出目录须在材料扫描范围外或明确 exclude，避免产物成为新输入")


def make_context(*, cache, output_root, required_facts=None):
    cache_path = guard.absolute_file(str(Path(cache).resolve()), "cache")
    document = load_valid_cache(cache_path)
    require_output_outside_inventory(output_root, document["inventory"])
    facts = copy.deepcopy(document["facts"])
    requested = required_facts if required_facts is not None else ["product_name", "core_claims", "packaging"]
    guard.require(set(requested).issubset(facts["facts"]), "所需事实字段不在产品包中，请先补充资料")
    for name in requested:
        facts["facts"][name]["required_for_plan"] = True
    facts["cache_binding"] = {"path": str(cache_path), "sha256": guard.sha256_file(cache_path)}
    report = guard.validate_facts(facts)
    guard.require(report["ok"], "当前任务所需事实缺失或冲突，先提问，不进入快速策划：" + "; ".join(report["blockers"]))
    context = {"schema_version": 1, "stage": "task_context", "facts": facts, "assets": document["assets"]}
    destination = new_directory(output_root)
    path = write_json(destination, "task-context.json", context)
    lines = ["# 本次任务产品上下文", "", facts["product_scope"], "", "仅复用未变化且有证据的产品资料；本次 brief、参考图与用户意图仍须读取。", "", "## 事实"]
    for name, entry in facts["facts"].items():
        if name in guard.CRITICAL_FACTS or entry.get("required_for_plan"):
            value = entry.get("resolution", {}).get("value", entry.get("value", "尚无可用值"))
            lines.append(f"- {name} [{entry['status']}]：{json.dumps(value, ensure_ascii=False)}")
    lines.extend(["", "## 产品图片索引（不是原图替代品）"])
    for item in document["assets"]["images"]:
        lines.append(f"- {item['id']} / {item['role']} / {item['product_scope']}：{item['path']}；{item['observation']['summary']}")
    with (destination / "task-context.md").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
    return {"ok": True, "network_called": False, "context": path, "summary": str(destination / "task-context.md"),
            "next_action": "read_current_brief_and_new_references_then_author_one_plan"}


def package_plan(*, context, inventory, reference_manifest, plan, output_root):
    context_path = Path(context).resolve()
    context_doc = guard.load(context_path)
    guard.require(context_doc.get("stage") == "task_context" and context_doc.get("schema_version") == 1,
                  "需要 prepare_task context 生成的任务上下文")
    facts = copy.deepcopy(context_doc["facts"])
    guard.require("cache_binding" in facts, "快速任务必须绑定有效产品包")
    cache = load_valid_cache(facts["cache_binding"]["path"])
    guard.require(context_doc["assets"] == cache["assets"], "产品图语义不得在任务上下文中擅改")
    source_inventory = guard.load(inventory)
    guard.validate_inventory_snapshot(source_inventory)
    require_output_outside_inventory(output_root, source_inventory)
    require_output_outside_inventory(output_root, cache["inventory"])
    manifest = guard.load(reference_manifest)
    reference_report = guard.validate_references(manifest)
    selected_inputs = {str(Path(row["absolute_path"]).resolve()).casefold() for row in source_inventory["files"]}
    guard.require(all(str(Path(item["path"]).resolve()).casefold() in selected_inputs for item in manifest["images"]),
                  "所有新参考图必须属于本次材料清单范围")
    document = copy.deepcopy(guard.load(plan))
    guard.require("input_binding" not in document, "输入须为新策划草稿，不要复用已绑定的旧版策划")
    used = document.get("fact_keys_used")
    guard.require(isinstance(used, list) and used and all(isinstance(key, str) for key in used),
                  "快速策划须用 fact_keys_used 列出本篇使用的事实字段（含用量/营养值等）")
    guard.require(set(used).issubset(facts["facts"]), "本篇使用了产品包不存在的字段，请先补充并确认产品资料")
    for key in used:
        facts["facts"][key]["required_for_plan"] = True
    facts_report = guard.validate_facts(facts)
    guard.require(facts_report["ok"], "本篇新增必需事实尚未确认，先 HITL，不生成 HTML")
    guard.validate_plan(document, facts_report, reference_report)
    asset_paths = {str(Path(item["path"]).resolve()).casefold() for item in cache["assets"]["images"]}
    for page in document["pages"]:
        guard.require(all(str(Path(path).resolve()).casefold() in asset_paths for path in page.get("product_image_paths", [])),
                      f"{page['id']} 使用了未进入产品包的图片，请先识别并更新产品包")
    # Reject invalid generation parameters before asking the user to approve.
    import image_request_guard as image_guard
    guard.require(document.get("tool", "edit_batch_images") == "edit_batch_images",
                  "快速策划只支持 edit_batch_images")
    guard.require(1 <= len(document["pages"]) <= 100, "快速策划页数须为 1–100")
    refs_by_id = {item["id"]: item["path"] for item in manifest["images"]}
    output_names = set()
    for page in document["pages"]:
        image_guard.ident(page["id"], "page.id")
        guard.require(page.get("tool", "edit_batch_images") == "edit_batch_images", "快速策划页不能切换工具")
        strength = image_guard.parse_reference_strength(page["prompt"])
        for field in ("reference_strength", "visual_strength"):
            guard.require(field not in page or page[field] == strength, f"{page['id']} 参考强度字段与提示词冲突")
        image_guard.size_pixels(page["size"])
        count = page.get("n", document.get("n", 1))
        image_guard.integer(count, 1, 4, "n")
        for field in ("model", "response_format"):
            value = page.get(field, document.get(field))
            if value is not None:
                image_guard.nonempty(value, field)
                guard.require(field != "response_format" or value in {"url", "b64_json"}, "response_format 不受支持")
            guard.require(field not in page or page[field] is not None, f"{field} 不接受显式 null")
        ids = page["reference_image_ids"]
        guard.require(len(ids) == len(set(ids)), "参考图 ID 不得重复")
        combined = [refs_by_id[key] for key in ids] + page.get("product_image_paths", [])
        guard.require(1 <= len(combined) <= 10, "每页总图片数须为 1–10")
        guard.require(len(combined) == len({image_guard.normalized_path(p) for p in combined}), "每页图片不得重复")
        guard.require("image_paths" not in page or page["image_paths"] == combined, "图序须为原帖参考在前、产品图在后")
        for stem in image_guard.predicted_output_stems({"id": page["id"], "n": count}, True, True):
            guard.require(stem.casefold() not in output_names, "预测输出文件名冲突")
            output_names.add(stem.casefold())
    destination = new_directory(output_root)
    paths = {}
    for key, name, value in (
        ("inventory", "00-material-inventory.json", source_inventory),
        ("facts", "01-product-facts.json", facts),
        ("facts_report", "01-product-facts-report.json", facts_report),
        ("reference_manifest", "02-reference-manifest.json", manifest),
        ("reference_report", "02-reference-report.json", reference_report),
    ):
        paths[key] = write_json(destination, name, value)
    document["input_binding"] = {"schema_version": 1, "artifacts": [
        {"kind": kind, "path": paths[key], "sha256": guard.sha256_file(paths[key])}
        for kind, key in (("material_inventory", "inventory"), ("product_facts", "facts"),
                          ("reference_manifest", "reference_manifest"))]}
    plan_report = guard.validate_plan(document, facts_report, reference_report)
    html_content = render(document)
    paths["plan"] = write_json(destination, "03-plan.json", document)
    paths["plan_report"] = write_json(destination, "03-plan-report.json", plan_report)
    html = destination / "03-plan.html"
    with html.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(html_content)
    paths["plan_html"] = str(html)
    return {"ok": True, "network_called": False, "page_count": len(document["pages"]),
            "paths": paths, "next_action": "deliver_html_then_stop_and_wait_for_user_approval",
            "note": "未创建批准记录，未生图，未进行独立视觉验收。"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    context_parser = commands.add_parser("context")
    context_parser.add_argument("--cache", required=True)
    context_parser.add_argument("--required-facts", nargs="+")
    plan_parser = commands.add_parser("plan")
    for key in ("context", "inventory", "reference-manifest", "plan"):
        plan_parser.add_argument("--" + key, required=True)
    for command in (context_parser, plan_parser):
        command.add_argument("--output-root", required=True)
    options = vars(parser.parse_args(argv))
    command = options.pop("command")
    try:
        result = (make_context if command == "context" else package_plan)(**options)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
