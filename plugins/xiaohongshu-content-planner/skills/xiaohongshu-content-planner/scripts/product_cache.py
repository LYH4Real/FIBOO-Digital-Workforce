"""Offline, version-bound product facts and image-observation reuse.

This packages supplied records; it does not extract facts, inspect images,
authenticate a human confirmation, call a model, or approve a campaign.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from workflow_guard import (
    absolute_file, emit, load, meaningful, nonempty, require, sha256_file,
    validate_facts, validate_inventory_snapshot, validate_references,
)


ARTIFACT_KINDS = {"product_facts": "facts", "product_assets": "assets",
                  "material_inventory": "inventory"}
RECORD_ONLY = "只校验记录、来源路径及文件哈希；不证明事实真实性、实际看图或人类确已确认。"


def _read_artifact(path, kind):
    path = absolute_file(str(path), kind).resolve()
    before = sha256_file(path)
    document = load(path)
    require(sha256_file(path) == before, f"读取期间文件发生变化：{path}")
    return document, {"kind": kind, "path": str(path), "sha256": before}


def _material_paths(snapshot):
    root = Path(snapshot["root"]).resolve()
    result = {}
    for item in snapshot["files"]:
        path = absolute_file(item.get("absolute_path"), "inventory.files.absolute_path").resolve()
        require(path.is_relative_to(root), f"材料实际路径不得越出 inventory.root：{path}")
        key = str(path).casefold()
        require(key not in result, "材料清单包含重复的实际路径")
        result[key] = item
    return result


def _in_materials(value, label, materials):
    path = absolute_file(value, label).resolve()
    require(str(path).casefold() in materials, f"{label} 未纳入 inventory 材料范围：{path}")
    return path


def _validate_payload(facts, assets, snapshot):
    require(isinstance(facts, dict) and "cache_binding" not in facts,
            "cache.facts 必须为原始事实 JSON，禁止已有 cache_binding（递归缓存）")
    require(isinstance(snapshot, dict) and snapshot.get("schema_version") == 1,
            "inventory schema_version 必须为 1")
    require(isinstance(snapshot.get("scope"), dict) and
            isinstance(snapshot["scope"].get("include"), list) and
            bool(snapshot["scope"]["include"]) and
            isinstance(snapshot["scope"].get("exclude"), list),
            "缓存必须使用带明确 include/exclude 的 inventory")
    current = validate_inventory_snapshot(snapshot)
    materials = _material_paths(current)
    fact_report = validate_facts(facts)
    require(fact_report["ok"], "产品事实门未通过：" + "; ".join(fact_report["blockers"]))
    sources = {}
    for source in facts["sources"]:
        _in_materials(source.get("path"), f"sources.{source['id']}.path", materials)
        nonempty(source.get("locator"), f"sources.{source['id']}.locator")
        sources[source["id"]] = source
    usable, pending = [], []
    for name, entry in facts["facts"].items():
        resolution = entry.get("resolution")
        distinct = {json.dumps(item["value"], sort_keys=True, ensure_ascii=False)
                    for item in entry.get("candidates", [])}
        conflict = entry["status"] == "conflict" or len(distinct) > 1
        if resolution:
            source_id = resolution.get("source_id")
            require(source_id in sources and sources[source_id].get("kind") == "user_confirmation",
                    f"{name}.resolution.source_id 必须引用有文件和定位的用户确认证据")
            require(entry["status"] not in {"missing", "not_applicable"},
                    f"{name} 有 resolution 却标为缺失/不适用，请先消除记录矛盾")
            if "value" in entry:
                require(entry["value"] == resolution["value"],
                        f"{name}.value 与 resolution.value 不一致，请先消除记录矛盾")
        elif conflict:
            require(not entry["required_for_plan"] and entry["status"] != "confirmed",
                    f"{name} 存在未解决冲突，必须 HITL；不能标为可用或必需")
        if resolution or entry["status"] == "confirmed":
            source_id = resolution.get("source_id") if resolution else entry.get("source_id")
            require(source_id in sources, f"可用事实 {name}.source_id 必须引用 sources")
            value = resolution["value"] if resolution else entry.get("value")
            require(meaningful(value) and not (isinstance(value, str) and not value.strip()),
                    f"可用事实 {name}.value 不得为空")
            usable.append(name)
        else:
            pending.append(name)
    require(isinstance(assets, dict) and assets.get("schema_version") == 1,
            "assets schema_version 必须为 1")
    require(assets.get("product_scope") == facts["product_scope"],
            "assets.product_scope 必须与 facts.product_scope 一致")
    images = assets.get("images")
    require(isinstance(images, list), "assets.images 必须是数组；暂无产品图时使用空数组")
    if images:
        validate_references(assets)
    paths = set()
    for item in images:
        path = _in_materials(item.get("path"), f"assets.images.{item['id']}.path", materials)
        key = str(path).casefold()
        require(key not in paths, "同一产品图实际路径重复登记")
        paths.add(key)
        nonempty(item.get("role"), f"assets.images.{item['id']}.role")
        require(item.get("product_scope") == facts["product_scope"],
                f"产品图 {item['id']}.product_scope 与本产品包不一致")
    warnings = list(fact_report["warnings"])
    if not images:
        warnings.append("产品图为 0 张：可复用已确认文字事实，后续产品出镜页必须先补图并完成实际语义识别。")
    if pending:
        warnings.append("未作为可用事实的字段：" + ", ".join(pending))
    return {"usable_fact_names": usable, "pending_fact_names": pending,
            "image_count": len(images), "source_count": len(sources),
            "material_file_count": len(current["files"]), "warnings": warnings,
            "validation_scope": RECORD_ONLY}


def _selected_destination(destination, snapshot):
    root = Path(snapshot["root"]).resolve()
    def paths(values):
        return [(Path(value) if Path(value).is_absolute() else root / value).resolve()
                for value in values]
    includes = paths(snapshot["scope"]["include"])
    excludes = paths(snapshot["scope"]["exclude"])
    return (any(destination == path or destination.is_relative_to(path) for path in includes)
            and not any(destination == path or destination.is_relative_to(path) for path in excludes))


def _short(value, limit=180):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…（完整记录见 cache.json）"


def _context(document, report):
    lines = ["# 产品预处理摘要", "", f"产品范围：{_short(document['product_scope'])}",
             f"来源 {report['source_count']} 份；材料 {report['material_file_count']} 个；产品图 {report['image_count']} 张。",
             "", RECORD_ONLY, "",
             "复用前必须运行 check；本摘要不是批准记录、参考图拆解或生成图片质检。",
             "", "## 可用事实（摘要）", ""]
    for name in report["usable_fact_names"][:24]:
        entry = document["facts"]["facts"][name]
        record = entry.get("resolution") or entry
        lines.append(f"- {_short(name, 60)}：{_short(record['value'])}（source_id: {_short(record['source_id'], 60)}）")
    if len(report["usable_fact_names"]) > 24:
        lines.append("- 其余事实按需读取 cache.json；摘要不代表完整资料。")
    lines.extend(["", "## 已记录的产品图片语义", ""])
    for item in document["assets"]["images"][:12]:
        lines.append(f"- {_short(item['id'], 60)} / {_short(item['role'], 60)}：{_short(item['observation']['summary'])}")
    if report["image_count"] > 12:
        lines.append("- 其余产品图按需读取 cache.json。")
    lines.extend(["", "## 边界与待补充", "",
                  "- 当前 campaign brief、目标人群、参考程度和逐页策划另行确认，不写入本产品包。",
                  "- 只有未变化材料的既有语义记录可以复用；新参考图、新包装/资料及本次生成结果仍需实际查看。"])
    lines.extend("- " + _short(warning, 400) for warning in report["warnings"])
    return "\n".join(lines) + "\n"


def _summary(path, document, report):
    return {"ok": True, "kind": "product_cache", "cache_path": str(path),
            "product_scope": document["product_scope"],
            "usable_fact_count": len(report["usable_fact_names"]),
            "pending_fact_count": len(report["pending_fact_names"]),
            "image_count": report["image_count"], "warnings": report["warnings"],
            "validation_scope": RECORD_ONLY}


def build_cache(facts_path, assets_path, inventory_path, output_root):
    """Package validated supplied records into a new immutable-by-convention folder."""
    facts, facts_artifact = _read_artifact(facts_path, "product_facts")
    assets, assets_artifact = _read_artifact(assets_path, "product_assets")
    snapshot, inventory_artifact = _read_artifact(inventory_path, "material_inventory")
    report = _validate_payload(facts, assets, snapshot)
    now = datetime.now(timezone.utc)
    destination = Path(output_root).resolve() / ("product-cache-" + now.strftime("%Y%m%dT%H%M%S%fZ-") + uuid.uuid4().hex[:8])
    require(not _selected_destination(destination, snapshot),
            "输出目录不能落入 inventory 扫描范围；请放到材料范围外或先明确 exclude 输出目录")
    artifacts = [facts_artifact, assets_artifact, inventory_artifact]
    for item in artifacts:
        require(sha256_file(item["path"]) == item["sha256"], "输入记录在准备期间变化，请重新运行")
    document = {"schema_version": 1, "kind": "product_cache", "created_at": now.isoformat(),
                "product_scope": facts["product_scope"], "facts": facts, "assets": assets,
                "inventory": snapshot, "input_artifacts": artifacts,
                "validation_scope": RECORD_ONLY}
    destination.mkdir(parents=True, exist_ok=False)
    cache_path = destination / "cache.json"
    emit(document, cache_path)
    with (destination / "product-context.md").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(_context(document, report))
    result = _summary(cache_path, document, report)
    result["context_path"] = str(destination / "product-context.md")
    return result


def load_valid_cache(cache_path):
    """Return a full cache only after current source bytes and scope still match."""
    document = load(absolute_file(str(cache_path), "cache_path"))
    require(isinstance(document, dict) and document.get("schema_version") == 1 and
            document.get("kind") == "product_cache", "不是 schema_version=1 的 product_cache")
    nonempty(document.get("created_at"), "cache.created_at")
    require(document.get("product_scope") == document.get("facts", {}).get("product_scope"),
            "cache.product_scope 与 facts 不一致")
    artifacts = document.get("input_artifacts")
    require(isinstance(artifacts, list) and len(artifacts) == len(ARTIFACT_KINDS),
            "input_artifacts 必须包含 facts、assets、inventory 三个原始记录")
    kinds = set()
    for item in artifacts:
        require(isinstance(item, dict), "input_artifacts 条目必须为对象")
        kind = item.get("kind")
        require(kind in ARTIFACT_KINDS and kind not in kinds, "input_artifacts kind 缺失、重复或不支持")
        kinds.add(kind)
        original, binding = _read_artifact(item.get("path"), kind)
        require(binding["sha256"] == item.get("sha256"), f"原始 {kind} 记录已变化，必须重新预处理")
        require(original == document.get(ARTIFACT_KINDS[kind]),
                f"cache.{ARTIFACT_KINDS[kind]} 与绑定的原始记录不一致")
    _validate_payload(document["facts"], document["assets"], document["inventory"])
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="打包已读取并有来源的产品资料，不调用模型")
    for name in ("facts", "assets", "inventory", "output-root"):
        build.add_argument("--" + name, required=True)
    check = commands.add_parser("check", help="重扫材料范围，验证当前可复用性")
    check.add_argument("--cache", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = build_cache(args.facts, args.assets, args.inventory, args.output_root)
        else:
            document = load_valid_cache(args.cache)
            # load_valid_cache already performed the expensive source-scope scan.
            entries = document["facts"]["facts"]
            usable = [name for name, entry in entries.items() if entry.get("resolution") or entry["status"] == "confirmed"]
            result = {"ok": True, "kind": "product_cache", "cache_path": str(Path(args.cache).resolve()),
                      "product_scope": document["product_scope"], "usable_fact_count": len(usable),
                      "pending_fact_count": len(entries) - len(usable), "image_count": len(document["assets"]["images"]),
                      "validation_scope": RECORD_ONLY}
            if not result["image_count"]:
                result["warnings"] = ["产品图为 0 张，后续产品出镜页须先补图并实际识别。"]
        emit(result)
        return 0
    except (ValueError, TypeError, OSError, KeyError, AttributeError, json.JSONDecodeError) as exc:
        emit({"ok": False, "stage": "product_cache", "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
