"""Build and validate production-stage artifacts for Xiaohongshu planning.

This tool is offline. It inventories files and validates agent-produced JSON; it
does not infer document facts, inspect visual semantics, call an MCP, or approve
content on the user's behalf.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import sys


CRITICAL_FACTS = {"product_name", "core_claims", "dosage", "packaging"}
FACT_STATUSES = {"confirmed", "missing", "conflict", "not_applicable"}
SEMANTIC_METHODS = {"multimodal_model", "vision_tool", "human_confirmed"}


def load(path):
    with Path(path).open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def emit(value, output=None):
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if output:
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    else:
        print(content, end="")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value, label):
    require(isinstance(value, str) and bool(value.strip()), f"{label} 必须是非空字符串")
    return value.strip()


def absolute_file(value, label, check_exists=True):
    path = Path(nonempty(value, label))
    require(path.is_absolute(), f"{label} 必须是绝对文件路径")
    if check_exists:
        require(path.is_file(), f"{label} 不存在或不是文件：{value}")
    return path


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def supported_raster(path):
    with Path(path).open("rb") as handle:
        header = handle.read(12)
    return (header.startswith(b"\x89PNG\r\n\x1a\n") or header.startswith(b"\xff\xd8")
            or (header.startswith(b"RIFF") and header[8:12] == b"WEBP"))


def json_sha256(value):
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _inventory_paths(root, values, label, must_exist=False):
    """Resolve explicit scope paths once; no globs or inferred exclusions."""
    paths = []
    for value in values or []:
        path = Path(nonempty(str(value), label))
        path = (path if path.is_absolute() else root / path).resolve()
        require(path.is_relative_to(root), f"{label} 必须位于任务目录中：{path}")
        if must_exist:
            require(path.is_file() or path.is_dir(), f"{label} 不存在或不是文件/文件夹：{path}")
        if path not in paths:
            paths.append(path)
    return paths


def _inventory_files(root, includes, excludes):
    def excluded(path):
        return any(path == item or item in path.parents for item in excludes)

    def fail(error):
        raise error

    found = set()
    for selected in includes:
        if excluded(selected):
            continue
        if selected.is_file():
            found.add(selected)
            continue
        # Prune excluded directories before walking them or hashing their files.
        # followlinks=False preserves the previous recursive scan's behavior.
        for directory, subdirs, names in os.walk(selected, followlinks=False, onerror=fail):
            parent = Path(directory)
            subdirs[:] = [name for name in subdirs if not excluded(parent / name)]
            for name in names:
                path = parent / name
                if path.is_file() and not excluded(path):
                    found.add(path)
    return sorted(found, key=lambda item: str(item).casefold())


def inventory(root, include=None, exclude=None):
    root = Path(root).resolve()
    require(root.is_dir(), f"任务目录不存在或不是文件夹：{root}")
    includes = _inventory_paths(root, include, "include", must_exist=True) or [root]
    excludes = _inventory_paths(root, exclude, "exclude")
    files = []
    for path in _inventory_files(root, includes, excludes):
        stat = path.stat()
        files.append({
            "relative_path": path.relative_to(root).as_posix(),
            "absolute_path": str(path.resolve()),
            "extension": path.suffix.lower(),
            "mime_guess": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "bytes": stat.st_size,
            "sha256": sha256_file(path),
        })
    counts = {}
    for item in files:
        counts[item["extension"] or "[no-extension]"] = counts.get(item["extension"] or "[no-extension]", 0) + 1
    return {
        "schema_version": 1,
        "stage": "material_inventory",
        "ok": True,
        "root": str(root),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "include": [path.relative_to(root).as_posix() for path in includes],
            "exclude": [path.relative_to(root).as_posix() for path in excludes],
            "change_detection": "sha256_every_selected_file_on_each_run",
        },
        "file_count": len(files),
        "counts_by_extension": counts,
        "files": files,
        "semantic_reading_required": True,
        "note": "本清单只证明目录与文件已枚举；文档内容和图片语义仍须实际读取。",
    }


def meaningful(value):
    if value is None:
        return False
    if isinstance(value, (str, list, dict)):
        return bool(value)
    return True


def validate_inventory_snapshot(document):
    """Rehash the explicit input scope, including newly added and removed files."""
    require(document.get("stage") == "material_inventory" and document.get("ok") is True,
            "需要有效材料清单")
    scope = document.get("scope")
    require(isinstance(scope, dict) and scope.get("include"), "复用材料清单必须明确 include/exclude 范围")
    current = inventory(document["root"], scope["include"], scope.get("exclude", []))
    def signatures(rows):
        result = {}
        for item in rows:
            key = str(Path(item["absolute_path"]).resolve()).casefold()
            require(key not in result, "材料清单有重复文件")
            result[key] = (item["sha256"], item["bytes"])
        return result
    before, after = signatures(document["files"]), signatures(current["files"])
    changed = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
    require(not changed, "材料范围发生新增、删除或内容变化，必须重新读取并确认：" + "; ".join(changed[:8]))
    return current


def validate_input_binding(binding):
    """A plan can bind its task inputs; this is freshness, not semantic proof."""
    require(isinstance(binding, dict) and binding.get("schema_version") == 1,
            "input_binding schema_version 必须为 1")
    artifacts = binding.get("artifacts")
    require(isinstance(artifacts, list) and artifacts, "input_binding.artifacts 不得为空")
    kinds = set()
    for item in artifacts:
        kind = item.get("kind")
        require(kind not in kinds, "input_binding kind 重复")
        kinds.add(kind)
        path = absolute_file(item.get("path"), "input_binding.path")
        require(sha256_file(path) == item.get("sha256"), "策划输入文件已变化，必须重新策划并确认：" + str(path))
        if kind == "material_inventory":
            validate_inventory_snapshot(load(path))
        elif kind == "product_facts":
            require(validate_facts(load(path))["ok"], "策划绑定的产品事实未通过")
        elif kind == "reference_manifest":
            validate_references(load(path))
    require({"material_inventory", "product_facts"}.issubset(kinds),
            "快速策划必须绑定材料清单和产品事实")


def validate_cache_binding(document):
    binding = document.get("cache_binding")
    if binding is None:
        return
    require(isinstance(binding, dict), "cache_binding 必须为对象")
    path = absolute_file(binding.get("path"), "cache_binding.path")
    require(sha256_file(path) == binding.get("sha256"), "产品预处理包已变化，请重新准备本次任务")
    from product_cache import load_valid_cache
    cached = load_valid_cache(path)["facts"]
    actual = {key: value for key, value in document.items() if key != "cache_binding"}
    require(set(actual) == set(cached), "任务事实不得更改缓存字段结构")
    require(set(actual["facts"]) == set(cached["facts"]), "任务事实字段与缓存不一致")
    for key in cached:
        if key != "facts":
            require(actual[key] == cached[key], "任务事实来源或产品范围与缓存不一致")
    for name, original in cached["facts"].items():
        entry = actual["facts"][name]
        require({k: v for k, v in entry.items() if k != "required_for_plan"} ==
                {k: v for k, v in original.items() if k != "required_for_plan"},
                f"{name} 不能在快速任务中更改缓存事实；先更新产品包")
        require(not original.get("required_for_plan") or entry.get("required_for_plan") is True,
                f"{name} 不得通过降低必需状态绕过事实门")


def _distinct_candidates(entry, source_ids, label):
    candidates = entry.get("candidates", [])
    require(isinstance(candidates, list), "fact.candidates 必须是数组")
    for index, item in enumerate(candidates, 1):
        require(isinstance(item, dict), f"{label}.candidates[{index}] 必须是对象")
        require(meaningful(item.get("value")), f"{label}.candidates[{index}].value 不得为空")
        require(item.get("source_id") in source_ids, f"{label}.candidates[{index}].source_id 不在 sources 中")
    return {json.dumps(item["value"], ensure_ascii=False, sort_keys=True) for item in candidates}


def validate_facts(document, check_sources=True):
    require(isinstance(document, dict) and document.get("schema_version") == 1,
            "产品事实文件 schema_version 必须为 1")
    nonempty(document.get("product_scope"), "product_scope")
    if check_sources:
        validate_cache_binding(document)
    sources = document.get("sources")
    require(isinstance(sources, list) and sources, "sources 必须列出本次实际读取的产品资料")
    source_ids = set()
    for index, source in enumerate(sources, 1):
        require(isinstance(source, dict), f"sources[{index}] 必须是对象")
        source_id = nonempty(source.get("id"), f"sources[{index}].id")
        require(source_id not in source_ids, f"产品资料 source id 重复：{source_id}")
        source_ids.add(source_id)
        if source.get("kind") != "user_confirmation":
            absolute_file(source.get("path"), f"sources[{index}].path", check_sources)
    facts = document.get("facts")
    require(isinstance(facts, dict), "facts 必须是对象")
    require(CRITICAL_FACTS.issubset(facts), "facts 必须包含 product_name、core_claims、dosage、packaging")
    blockers, warnings, checks = [], [], []
    for name, entry in facts.items():
        require(isinstance(entry, dict), f"facts.{name} 必须是对象")
        status = entry.get("status")
        require(status in FACT_STATUSES, f"facts.{name}.status 无效")
        required = entry.get("required_for_plan")
        require(type(required) is bool, f"facts.{name}.required_for_plan 必须为布尔值")
        distinct = _distinct_candidates(entry, source_ids, f"facts.{name}")
        resolution = entry.get("resolution")
        unresolved_conflict = name in CRITICAL_FACTS and (status == "conflict" or len(distinct) > 1) and not resolution
        if resolution:
            require(isinstance(resolution, dict), f"facts.{name}.resolution 必须是对象")
            require(meaningful(resolution.get("value")), f"facts.{name}.resolution.value 不得为空")
            require(resolution.get("source") == "user_confirmation",
                    f"facts.{name} 冲突只能由明确用户确认解决，不能由 Agent 自选")
            nonempty(resolution.get("evidence"), f"facts.{name}.resolution.evidence")
        if unresolved_conflict:
            blockers.append(f"{name} 存在未解决冲突，必须 HITL 提问")
        elif required and (status == "conflict" or len(distinct) > 1) and not resolution:
            blockers.append(f"{name} 是当前策划必需事实且存在冲突，必须 HITL 提问")
        if required and status in {"missing", "not_applicable"}:
            blockers.append(f"{name} 是当前策划必需事实，但尚未确认")
        if status == "confirmed":
            require("value" in entry or resolution, f"facts.{name} 标为 confirmed 时必须有 value 或 resolution")
        if name in CRITICAL_FACTS and status == "not_applicable" and not entry.get("reason"):
            warnings.append(f"{name} 标为不适用但未说明原因")
        checks.append({"field": name, "status": status, "required_for_plan": required,
                       "candidate_count": len(distinct), "resolved_by_user": bool(resolution)})
    return {
        "schema_version": 1,
        "stage": "product_facts",
        "input_sha256": json_sha256(document),
        "ok": not blockers,
        "product_scope": document["product_scope"],
        "checks": checks,
        "blockers": blockers,
        "warnings": warnings,
        "next_action": "continue_to_plan" if not blockers else "ask_user_and_stop_before_plan_html",
    }


def validate_references(document, check_paths=True):
    require(isinstance(document, dict) and document.get("schema_version") == 1,
            "参考图语义清单 schema_version 必须为 1")
    images = document.get("images")
    require(isinstance(images, list) and images, "images 必须包含至少一张已实际查看的参考图")
    ids, paths, rows = set(), set(), []
    required_observations = ("summary", "visible_text", "composition", "shot", "lighting", "style", "imperfections")
    for index, item in enumerate(images, 1):
        require(isinstance(item, dict), f"images[{index}] 必须是对象")
        image_id = nonempty(item.get("id"), f"images[{index}].id")
        require(image_id not in ids, f"参考图 id 重复：{image_id}")
        ids.add(image_id)
        path = absolute_file(item.get("path"), f"images[{index}].path", check_paths)
        if check_paths:
            require(supported_raster(path), f"{image_id} 不是受支持的 PNG/JPEG/WebP 图片文件")
        normalized = str(path).casefold()
        require(normalized not in paths, f"同一参考图路径被重复登记：{path}")
        paths.add(normalized)
        require(item.get("semantic_status") == "verified", f"{image_id} 必须完成语义识别后标为 verified")
        inspection = item.get("inspection")
        require(isinstance(inspection, dict), f"{image_id}.inspection 必须记录实际看图方式")
        require(inspection.get("method") in SEMANTIC_METHODS,
                f"{image_id} 必须由视觉工具、多模态模型或人工实际看图；文件名/OCR 推断不合格")
        nonempty(inspection.get("inspected_at"), f"{image_id}.inspection.inspected_at")
        observation = item.get("observation")
        require(isinstance(observation, dict), f"{image_id}.observation 必须是对象")
        for field in required_observations:
            value = observation.get(field)
            require((isinstance(value, str) and value.strip()) or (isinstance(value, list) and value),
                    f"{image_id}.observation.{field} 不得为空")
        if check_paths:
            actual_hash = sha256_file(path)
            require(item.get("sha256") == actual_hash, f"{image_id} 文件哈希与语义清单不一致，需重新看图")
        rows.append({"id": image_id, "path": str(path), "sha256": item.get("sha256"),
                     "page_index": item.get("page_index"), "summary": observation["summary"]})
    return {"schema_version": 1, "stage": "reference_semantics", "ok": True,
            "input_sha256": json_sha256(document),
            "image_count": len(rows), "images": rows,
            "next_action": "map_verified_reference_ids_to_plan_pages"}


def validate_plan(document, facts_report=None, reference_report=None):
    require(isinstance(document, dict) and document.get("schema_version") == 1,
            "策划 JSON schema_version 必须为 1")
    require(document.get("status") == "awaiting_user_approval",
            "策划在生图前必须处于 awaiting_user_approval；不能由 Agent 自行改成 approved")
    if "input_binding" in document:
        validate_input_binding(document["input_binding"])
    nonempty(document.get("task_id"), "task_id")
    nonempty(document.get("strategy"), "strategy")
    titles = document.get("title_candidates")
    require(isinstance(titles, list) and titles and all(isinstance(value, str) and value.strip() for value in titles),
            "title_candidates 必须包含非空标题")
    nonempty(document.get("body_copy"), "body_copy")
    contract = document.get("reference_contract")
    require(isinstance(contract, dict), "reference_contract 必须明确四个维度")
    require(set(("narrative", "composition", "image_style", "copy_tone")).issubset(contract),
            "reference_contract 缺少叙事、构图、图片风格或文案语气")
    pages = document.get("pages")
    require(isinstance(pages, list) and pages, "pages 必须包含至少一页分镜")
    ids = set()
    for index, page in enumerate(pages, 1):
        require(isinstance(page, dict), f"pages[{index}] 必须是对象")
        page_id = nonempty(page.get("id"), f"pages[{index}].id")
        require(page_id not in ids, f"页面 id 重复：{page_id}")
        ids.add(page_id)
        for field in ("purpose", "visual_blueprint", "prompt", "size"):
            nonempty(page.get(field), f"{page_id}.{field}")
        require(type(page.get("ugc_required")) is bool, f"{page_id}.ugc_required 必须明确 true/false")
        require(type(page.get("product_required")) is bool, f"{page_id}.product_required 必须明确 true/false")
        refs = page.get("reference_image_ids", [])
        require(isinstance(refs, list), f"{page_id}.reference_image_ids 必须是数组")
        product_paths = page.get("product_image_paths", [])
        require(isinstance(product_paths, list), f"{page_id}.product_image_paths 必须是数组")
        if page["product_required"]:
            require(product_paths, f"{page_id} 要求产品出镜，但没有产品参考图；应先向用户补材料")
        for path in product_paths:
            absolute_file(path, f"{page_id}.product_image_paths")
    questions = document.get("questions", [])
    require(isinstance(questions, list) and len(questions) <= 3,
            "questions 必须是 0–3 个真正影响作图的问题")
    if facts_report is not None:
        require(facts_report.get("stage") == "product_facts" and facts_report.get("ok") is True,
                "产品事实门未通过，不得生成策划 HTML")
    if reference_report is not None:
        require(reference_report.get("stage") == "reference_semantics" and reference_report.get("ok") is True,
                "参考图语义清单未通过，不得生成策划 HTML")
        available = {item["id"] for item in reference_report.get("images", [])}
        for page in pages:
            require(page.get("reference_image_ids"), f"{page['id']} 缺少对应或最接近的原帖参考图 ID")
            require(set(page.get("reference_image_ids", [])).issubset(available),
                    f"{page['id']} 引用了语义清单中不存在的参考图 id")
    return {"schema_version": 1, "stage": "plan", "ok": True,
            "task_id": document["task_id"], "page_ids": sorted(ids),
            "next_action": "render_html_then_wait_for_user_approval"}


def validate_approval(document, check_paths=True):
    require(isinstance(document, dict) and document.get("schema_version") == 1,
            "策划确认文件 schema_version 必须为 1")
    require(document.get("status") == "approved", "策划尚未获得用户明确确认")
    plan_path = absolute_file(document.get("plan_json"), "plan_json", check_paths)
    html_path = absolute_file(document.get("plan_html"), "plan_html", check_paths)
    if check_paths:
        require(document.get("plan_sha256") == sha256_file(plan_path), "确认所对应的策划 JSON 已变化，必须重新确认")
        require(document.get("plan_html_sha256") == sha256_file(html_path), "确认所对应的策划 HTML 已变化，必须重新确认")
        plan_document = load(plan_path)
        if "input_binding" in plan_document:
            validate_input_binding(plan_document["input_binding"])
    require(document.get("approved_by") == "user", "策划确认必须来自用户，Agent 不能自批")
    nonempty(document.get("approval_evidence"), "approval_evidence")
    nonempty(document.get("approved_at"), "approved_at")
    return {"schema_version": 1, "stage": "plan_approval", "ok": True,
            "plan_json": str(plan_path), "plan_sha256": document.get("plan_sha256"),
            "plan_html": str(html_path), "plan_html_sha256": document.get("plan_html_sha256")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inv = commands.add_parser("inventory", help="递归枚举任务目录并记录文件哈希")
    inv.add_argument("--root", required=True)
    inv.add_argument("--include", action="append", help="只扫描指定材料文件/子目录，可重复；路径相对 root 或为其内部绝对路径")
    inv.add_argument("--exclude", action="append", help="跳过指定文件/子目录及其后代，可重复；例如成图、历史目录，不使用通配符")
    facts = commands.add_parser("facts", help="检查产品关键事实冲突与缺口")
    facts.add_argument("--input", required=True)
    refs = commands.add_parser("references", help="检查参考图逐张语义清单")
    refs.add_argument("--input", required=True)
    plan = commands.add_parser("plan", help="检查策划 JSON 是否可渲染")
    plan.add_argument("--input", required=True)
    plan.add_argument("--facts-report")
    plan.add_argument("--reference-report")
    approval = commands.add_parser("approval", help="检查用户策划确认记录")
    approval.add_argument("--input", required=True)
    for command in (inv, facts, refs, plan, approval):
        command.add_argument("--output", help="新建 JSON 报告；已有文件拒绝覆盖")
    args = parser.parse_args(argv)
    try:
        if args.command == "inventory":
            value = inventory(args.root, args.include, args.exclude)
        elif args.command == "facts":
            value = validate_facts(load(args.input))
        elif args.command == "references":
            value = validate_references(load(args.input))
        elif args.command == "plan":
            value = validate_plan(load(args.input), load(args.facts_report) if args.facts_report else None,
                                  load(args.reference_report) if args.reference_report else None)
        else:
            value = validate_approval(load(args.input))
        emit(value, args.output)
        return 0 if value["ok"] else 1
    except (ValueError, TypeError, OSError, KeyError, json.JSONDecodeError) as exc:
        emit({"ok": False, "stage": args.command, "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
