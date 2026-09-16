"""Offline guard for Waveeee Image MCP 0.4.2 requests and output dimensions.

No network requests, image generation, or image rewriting. Image inspection reads
container metadata, not a full pixel decoder or a visual/content quality check.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import struct
import sys
import zlib

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from workflow_guard import json_sha256, sha256_file, validate_approval, validate_facts, validate_references


TOOLS = {"generate_image", "edit_image", "generate_batch_images", "edit_batch_images"}
REFERENCE_STRENGTHS = {
    "strict": "【参考强度】严格参考",
    "moderate": "【参考强度】中度参考",
    "light": "【参考强度】轻度参考",
}
REFERENCE_MARKER = re.compile(r"【参考强度】\s*(严格参考|中度参考|轻度参考)(?=$|[\s。；;，,：:!！?？（(])")
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
GENERATION_KEYS = {"prompt", "model", "size", "n", "response_format"}
SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
               0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³"
}


def parse_reference_strength(prompt):
    """Read the declared strength without rewriting approved prompt bytes."""
    matches = REFERENCE_MARKER.findall(prompt)
    if len(matches) != 1 or prompt.count("【参考强度】") != 1:
        raise ValueError("参考强度必须唯一明确为 严格参考、中度参考或轻度参考")
    declaration = re.split(r"[\r\n。；;]", prompt.split("【参考强度】", 1)[1].lstrip(), maxsplit=1)[0]
    if len(re.findall(r"严格参考|中度参考|轻度参考", declaration)) != 1:
        raise ValueError("同一参考强度声明不能包含多个候选值")
    labels = {marker.removeprefix("【参考强度】"): key
              for key, marker in REFERENCE_STRENGTHS.items()}
    return labels[matches[0]]


def load(path):
    with Path(path).open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value, label):
    require(isinstance(value, str) and bool(value.strip()), f"{label} 必须是非空字符串")
    return value


def ident(value, label):
    require(isinstance(value, str) and ID_PATTERN.fullmatch(value),
            f"{label} 需为 1–80 位稳定 ID，使用字母、数字、点、下划线、短横线，首位为字母或数字")
    return value


def integer(value, low, high, label):
    require(type(value) is int and low <= value <= high,
            f"{label} 必须是 {low}–{high} 的整数")


def size_pixels(value):
    """Mirror the existing MCP client constraints, not an invented size enum."""
    require(isinstance(value, str), "size 必须显式提供 WIDTHxHEIGHT 字符串")
    match = re.fullmatch(r"([0-9]+)x([0-9]+)", value.strip())
    require(match, "size 必须显式提供 WIDTHxHEIGHT；prompt 中的比例不算参数")
    width, height = map(int, match.groups())
    require(width > 0 and height > 0, "size 的宽和高必须大于 0")
    require(max(width, height) < 3840, "size 的单边必须严格小于 3840px")
    require(width % 16 == height % 16 == 0, "size 的宽、高必须是 16 的倍数")
    require(655360 <= width * height <= 8294400, "size 总像素必须在 655360–8294400 之间")
    require(max(width, height) <= min(width, height) * 3, "size 长边与短边比例不能超过 3:1")
    return width, height


def ratio_pair(value):
    require(isinstance(value, str), "预期比例必须是 W:H 字符串")
    match = re.fullmatch(r"([0-9]+):([0-9]+)", value)
    require(match, "预期比例必须是 W:H 字符串")
    width, height = map(int, match.groups())
    require(width > 0 and height > 0, "预期比例必须大于 0")
    return width, height


def ratio_matches(actual, expected):
    return actual[0] * expected[1] == actual[1] * expected[0]


def absolute_file(value, label, check_exists=True):
    nonempty(value, label)
    path = Path(value)
    require(path.is_absolute(), f"{label} 必须是当前执行机器上的绝对路径，不接受 URL、相对路径或文件名")
    if check_exists:
        require(path.is_file(), f"{label} 不存在或不是文件：{value}")
    return path


def normalized_path(value):
    """Compare request paths as Windows paths, independent of test host casing."""
    return str(PureWindowsPath(value)).casefold()


def windows_component(value, label):
    """Check output names for the Windows production runtime, on any test OS."""
    require(not re.search(r'[\x00-\x1f<>:"/\\|?*]', value),
            f"{label} 含 Windows 非法字符或控制字符")
    require(not value.endswith((".", " ")), f"{label} 不得以点或空格结尾，以免 Windows 路径别名")
    require(value.split(".", 1)[0].upper() not in WINDOWS_RESERVED,
            f"{label} 使用 Windows 保留设备名：{value}")


def safe_filename(value):
    """Mirror storage.safe_filename (including stripping trailing dots/_)."""
    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff._-]+", "_", str(value)).strip("._") or "image"


def predicted_output_stems(task, batch, edit):
    if batch:
        base = safe_filename(task["id"])
    else:
        fallback = "edited-image" if edit else "image"
        base = safe_filename(Path(task.get("filename", fallback)).stem or fallback)
    n = task.get("n", 1)
    stems = [f"{base}-{index}" if n > 1 else base for index in range(1, n + 1)]
    for stem in stems:
        windows_component(stem + ".png", "实际输出文件名")
    return stems


def output_subdir(value, run_id, batch):
    if value is None:
        require(not batch, "本项目批量留存规范要求 output_subdir 包含唯一 run_id，避免覆盖 manifest")
        return
    nonempty(value, "output_subdir")
    win = PureWindowsPath(value)
    posix = PurePosixPath(value.replace("\\", "/"))
    require(not win.drive and not win.root and not posix.is_absolute()
            and ".." not in posix.parts, "output_subdir 必须是输出根目录内的相对子目录")
    for part in posix.parts:
        windows_component(part, "output_subdir 路径段")
    if batch:
        require(run_id in posix.parts, "本项目批量留存规范要求 output_subdir 中有完整 run_id 路径段")


def validate_bound_plan_request(request, tasks, plan, semantic_images):
    """Keep new fast-plan inputs and page arguments tied to user approval.

    Legacy plans without input_binding retain their previous validation path.
    This runs in standalone preflight too, not only in the request compiler.
    """
    if "input_binding" not in plan:
        return
    binding = plan["input_binding"]
    require(isinstance(binding, dict) and isinstance(binding.get("artifacts"), list),
            "批准策划 input_binding.artifacts 必须是数组")
    artifacts = binding["artifacts"]
    require(all(isinstance(item, dict) and isinstance(item.get("kind"), str) for item in artifacts),
            "批准策划 input_binding.artifacts 必须包含有 kind 的对象")
    by_kind = {item["kind"]: item for item in artifacts}
    require(len(by_kind) == len(artifacts), "批准策划的输入绑定 kind 重复")
    require(request["tool"] == "edit_batch_images"
            and request["reference_context"]["provided"] is True,
            "带输入绑定的快速策划只支持有原帖参考的 edit_batch_images")
    require(plan.get("tool", "edit_batch_images") == "edit_batch_images",
            "批准的快速策划指定了其他工具，不能用批量编辑请求替代")
    for context_key, artifact_key in (("facts_input", "product_facts"),
                                      ("reference_manifest", "reference_manifest")):
        artifact = by_kind.get(artifact_key)
        require(isinstance(artifact, dict), f"批准策划缺少 {artifact_key} 输入绑定")
        bound_path = absolute_file(artifact.get("path"), f"input_binding.{artifact_key}.path")
        current_path = absolute_file(request["planning_context"].get(context_key),
                                     f"planning_context.{context_key}")
        require(normalized_path(str(bound_path.resolve())) == normalized_path(str(current_path.resolve())),
                f"{context_key} 不是批准策划绑定的材料路径，禁止借用其他事实或参考报告")
        require(artifact.get("sha256") == sha256_file(current_path),
                f"{context_key} 内容不再符合批准策划的输入哈希，必须重新策划并确认")
    pages = plan.get("pages")
    require(isinstance(pages, list) and pages and all(isinstance(p, dict) for p in pages),
            "批准策划 pages 必须包含页面对象")
    approved_ids = [ident(page.get("id"), "批准策划 page.id") for page in pages]
    require(len(set(approved_ids)) == len(approved_ids), "批准策划存在重复页面 ID")
    requested_ids = [task["id"] for task in tasks]
    require(set(requested_ids).issubset(approved_ids), "请求含批准策划之外的页面 ID")
    require(requested_ids == [page_id for page_id in approved_ids if page_id in requested_ids],
            "请求页面顺序与批准策划不一致")
    by_id = {page["id"]: page for page in pages}
    reference = request["reference_context"]
    for task in tasks:
        page = by_id[task["id"]]
        page_id = task["id"]
        require(page.get("tool", "edit_batch_images") == "edit_batch_images",
                f"{page_id} 在批准策划中指定其他工具")
        for field in ("prompt", "size"):
            require(task[field] == page.get(field),
                    f"{page_id}.{field} 偏离批准策划；修改后必须重新确认")
        expected_n = page.get("n", plan.get("n", 1))
        require(task.get("n", 1) == expected_n, f"{page_id}.n 偏离批准策划")
        for field in ("model", "response_format"):
            expected = page.get(field, plan.get(field))
            require(task.get(field) == expected,
                    f"{page_id}.{field} 偏离批准策划；新快速策划不得在确认后临时补写")
        ref_ids = page.get("reference_image_ids")
        require(isinstance(ref_ids, list) and ref_ids and len(set(ref_ids)) == len(ref_ids),
                f"{page_id} 批准策划的参考图 ID 为空或重复")
        require(all(ref_id in semantic_images for ref_id in ref_ids),
                f"{page_id} 批准策划引用了当前语义清单中不存在的图片")
        ref_paths = [semantic_images[ref_id]["path"] for ref_id in ref_ids]
        product_paths = page.get("product_image_paths", [])
        require(isinstance(product_paths, list), f"{page_id}.product_image_paths 必须是数组")
        require(page.get("product_required") is not True or product_paths,
                f"{page_id} 批准策划要求产品出镜但未绑定产品图")
        expected_paths = [normalized_path(value) for value in ref_paths + product_paths]
        require(len(set(expected_paths)) == len(expected_paths), f"{page_id} 批准策划包含重复图片路径")
        require([normalized_path(value) for value in task["image_paths"]] == expected_paths,
                f"{page_id}.image_paths 偏离批准策划的原帖图/产品图及顺序")
        if "image_paths" in page:
            require([normalized_path(value) for value in page["image_paths"]] == expected_paths,
                    f"{page_id} 批准策划的显式 image_paths 与参考图/产品图顺序冲突")
        require(reference["source_image_ids_by_task"][page_id] == ref_ids,
                f"{page_id} 请求的参考图 ID 或顺序偏离批准策划")
        require([normalized_path(value) for value in reference["source_images_by_task"][page_id]]
                == [normalized_path(value) for value in ref_paths],
                f"{page_id} 请求的原帖参考图路径或顺序偏离批准策划")


def validate_request(request, strict_ratio=None, check_paths=True):
    require(isinstance(request, dict), "请求包必须是 JSON 对象")
    require(type(request.get("schema_version")) is int and request["schema_version"] == 2,
            "schema_version 必须为 2；旧请求不含策划确认门，禁止继续生图")
    require(not set(request) - {"schema_version", "run_id", "id", "tool", "arguments", "expectations",
                                "reference_context", "planning_context"},
            "请求包含未知字段；页面说明、参考角色等不要混入此 MCP 请求包")
    run_id = ident(request.get("run_id"), "run_id")
    tool = request.get("tool")
    require(isinstance(tool, str) and tool in TOOLS, "tool 必须为四个受支持的生图/编辑工具之一")
    args = request.get("arguments")
    require(isinstance(args, dict), "arguments 必须是 JSON 对象")
    batch = "batch" in tool
    edit = tool.startswith("edit")
    if batch:
        require("id" not in request, "批量请求请将稳定 id 放在每个 tasks 项，不能只设置外层 id")
        allowed = {"tasks", "concurrency", "retries", "output_subdir"}
        tasks = args.get("tasks")
        require(isinstance(tasks, list) and 1 <= len(tasks) <= 100, "tasks 必须含 1–100 个任务")
        if "concurrency" in args:
            integer(args["concurrency"], 1, 16, "concurrency")
        if "retries" in args:
            integer(args["retries"], 0, 5, "retries")
    else:
        ident(request.get("id"), "id")
        allowed = GENERATION_KEYS | {"filename", "output_subdir"} | ({"image_paths"} if edit else set())
        tasks = [dict(args, id=request["id"])]
        if "filename" in args:
            filename = nonempty(args["filename"], "filename")
            require("/" not in filename and "\\" not in filename, "filename 只能是文件名，不得含目录")
            require(not re.search(r'[\x00-\x1f<>:"|?*]', filename),
                    "filename 含非法字符或控制字符；请明确命名，不依赖服务端自动清洗")
    require(not set(args) - allowed, f"{tool} arguments 有未知或不适用字段：{sorted(set(args) - allowed)}")
    output_subdir(args.get("output_subdir"), run_id, batch)
    expectations = request.get("expectations", {})
    require(isinstance(expectations, dict), "expectations 必须是以任务 id 为键的对象")
    strict = ratio_pair(strict_ratio) if strict_ratio is not None else None
    checks, ids, output_names, task_inputs = [], set(), {}, {}
    for index, task in enumerate(tasks, 1):
        require(isinstance(task, dict), f"tasks[{index}] 必须是对象")
        task_id = ident(task.get("id"), f"tasks[{index}].id")
        require(task_id not in ids, f"任务 id 重复：{task_id}")
        ids.add(task_id)
        task_allowed = GENERATION_KEYS | {"id"} | ({"image_paths"} if edit else set())
        if not batch:
            task_allowed |= {"filename", "output_subdir"}
        require(not set(task) - task_allowed, f"{task_id} 有未知或不适用字段：{sorted(set(task) - task_allowed)}")
        nonempty(task.get("prompt"), f"{task_id}.prompt")
        width, height = size_pixels(task.get("size"))
        if "model" in task:
            nonempty(task["model"], f"{task_id}.model")
        integer(task.get("n", 1), 1, 4, f"{task_id}.n")
        stems = predicted_output_stems(task, batch, edit)
        for stem in stems:
            normalized = stem.casefold()
            require(normalized not in output_names,
                    f"实际输出文件名碰撞：{task_id} 与 {output_names.get(normalized)} 都可能写入 {stem}.*；"
                    "请修改 ID/filename，不依赖大小写、尾点/下划线或自动序号区分")
            output_names[normalized] = task_id
        require(task.get("response_format", "auto") in {"auto", "url", "b64_json"},
                f"{task_id}.response_format 必须为 auto、url 或 b64_json")
        refs = task.get("image_paths", [])
        if edit:
            require(isinstance(refs, list) and 1 <= len(refs) <= 10,
                    f"{task_id}.image_paths 必须含 1–10 个参考图绝对路径")
            for ref in refs:
                absolute_file(ref, f"{task_id}.image_paths", check_paths)
        task_inputs[task_id] = {"prompt": task["prompt"], "image_paths": refs}
        expected = expectations.get(task_id, {})
        require(isinstance(expected, dict) and not set(expected) - {"size", "ratio"},
                f"{task_id} expectation 只支持 size 和 ratio")
        if "size" in expected:
            require((width, height) == size_pixels(expected["size"]),
                    f"{task_id} 请求 size 不符合用户确认的预期 size；不要自动替换")
        expected_ratio = expected.get("ratio")
        if "ratio" in expected:
            require(ratio_matches((width, height), ratio_pair(expected_ratio)),
                    f"{task_id} 请求 size 与用户预期比例不一致；不要自动替换")
        if strict is not None:
            require(ratio_matches((width, height), strict),
                    f"{task_id} 请求 size 不符合 --strict-ratio {strict_ratio}")
        checks.append({"id": task_id, "requested_size": task["size"], "width": width,
                       "height": height, "n": task.get("n", 1), "reference_count": len(refs),
                       "predicted_output_stems": stems,
                       "expected_ratio": expected_ratio or strict_ratio or f"{width}:{height}"})
    require(not set(expectations) - ids, "expectations 中存在本次没有请求的任务 id")

    planning = request.get("planning_context")
    require(isinstance(planning, dict), "planning_context 必填，必须证明已先生成策划 HTML 并获得用户确认")
    require(set(planning) in ({"facts_input", "facts_report", "plan_approval"},
                              {"facts_input", "facts_report", "reference_manifest", "reference_report",
                               "plan_approval"}),
            "planning_context 必须包含产品事实输入/报告、可选参考语义输入/报告和策划确认")
    facts_input_path = absolute_file(planning.get("facts_input"), "planning_context.facts_input", check_paths)
    facts_report_path = absolute_file(planning.get("facts_report"), "planning_context.facts_report", check_paths)
    approval_path = absolute_file(planning.get("plan_approval"), "planning_context.plan_approval", check_paths)
    if check_paths:
        facts_input = load(facts_input_path)
        facts_report = load(facts_report_path)
        require(facts_report.get("stage") == "product_facts" and facts_report.get("ok") is True,
                "产品事实冲突门未通过，禁止生图")
        require(facts_report.get("input_sha256") == json_sha256(facts_input),
                "产品事实输入在检查后发生变化，必须重新运行事实门")
        require(validate_facts(facts_input).get("ok") is True, "当前产品事实仍有冲突或缺口，禁止生图")
        approved = validate_approval(load(approval_path), check_paths=True)
        approved_plan = load(approved["plan_json"])

    reference = request.get("reference_context")
    require(isinstance(reference, dict) and type(reference.get("provided")) is bool,
            "reference_context 必须明确 provided:true/false，不能让有参考任务静默退化")
    if reference["provided"]:
        require(set(reference) == {"provided", "visual_strength_by_task", "source_images_by_task",
                                   "source_image_ids_by_task"},
                "有参考时 reference_context 必须包含逐页强度、原图路径和语义清单 image id")
        require("reference_manifest" in planning and "reference_report" in planning,
                "有参考任务必须在 planning_context 提供语义清单输入与报告")
        if check_paths:
            manifest_path = absolute_file(planning["reference_manifest"], "planning_context.reference_manifest")
            reference_report_path = absolute_file(planning["reference_report"], "planning_context.reference_report")
            manifest = load(manifest_path)
            reference_report = load(reference_report_path)
            require(reference_report.get("stage") == "reference_semantics" and reference_report.get("ok") is True,
                    "参考图逐张语义清单未通过，禁止生图")
            require(reference_report.get("input_sha256") == json_sha256(manifest),
                    "参考图语义清单在检查后发生变化，必须重新识别并运行语义门")
            current_reference_report = validate_references(manifest)
            semantic_images = {item["id"]: item for item in current_reference_report.get("images", [])}
        require(edit, "用户提供过参考图文时必须调用 edit_image/edit_batch_images，不能只传产品图")
        strengths = reference.get("visual_strength_by_task")
        sources = reference.get("source_images_by_task")
        source_ids = reference.get("source_image_ids_by_task")
        require(isinstance(strengths, dict) and set(strengths) == ids,
                "visual_strength_by_task 必须逐项覆盖本次全部任务 ID")
        require(isinstance(sources, dict) and set(sources) == ids,
                "source_images_by_task 必须逐项覆盖本次全部任务 ID")
        require(isinstance(source_ids, dict) and set(source_ids) == ids,
                "source_image_ids_by_task 必须逐项覆盖本次全部任务 ID")
        for task_id in ids:
            strength = strengths[task_id]
            require(strength in REFERENCE_STRENGTHS,
                    f"{task_id} 参考强度必须为 strict、moderate 或 light")
            require(parse_reference_strength(task_inputs[task_id]["prompt"]) == strength,
                    f"{task_id}.prompt 必须显式写 {REFERENCE_STRENGTHS[strength]}")
            source_paths = sources[task_id]
            semantic_ids = source_ids[task_id]
            require(isinstance(source_paths, list) and bool(source_paths),
                    f"{task_id} 必须列出至少一张原帖参考图；不能只传目标产品图")
            require(isinstance(semantic_ids, list) and bool(semantic_ids),
                    f"{task_id} 必须列出参考图语义清单 ID")
            require(len(source_paths) == len(semantic_ids),
                    f"{task_id} 原图路径与语义清单 ID 必须一一对应")
            request_paths = {normalized_path(value) for value in task_inputs[task_id]["image_paths"]}
            for source_id, source_path in zip(semantic_ids, source_paths):
                absolute_file(source_path, f"{task_id}.source_images_by_task", check_paths)
                require(normalized_path(source_path) in request_paths,
                        f"{task_id} 的原帖参考图未进入实际 image_paths；不能只写在策划或提示词中")
                if check_paths:
                    require(source_id in semantic_images, f"{task_id} 的参考图 ID 不在已验证语义清单：{source_id}")
                    require(normalized_path(semantic_images[source_id]["path"]) == normalized_path(source_path),
                            f"{task_id} 的参考图 ID 与真实路径错配，禁止把别页图片送入 MCP")
    else:
        require(set(reference) == {"provided"},
                "没有参考时 reference_context 只填写 provided:false")
        require("reference_report" not in planning and "reference_manifest" not in planning,
                "没有参考时不应挂接参考语义清单；先修正任务材料状态")
    if check_paths:
        validate_bound_plan_request(request, tasks, approved_plan,
                                    semantic_images if reference["provided"] else {})
    return checks


def preflight(request, strict_ratio=None):
    checks = validate_request(request, strict_ratio)
    canonical = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    warnings = ["预检未调用 MCP；通过不代表服务已返回目标尺寸。",
                "run_id 唯一性需与历史目录核对；本地预检无法证明服务端目录未被使用。"]
    if "model" not in request["arguments"] and "batch" not in request["tool"]:
        warnings.append("model 未显式提供，实际模型由 MCP 服务配置决定。")
    if "batch" not in request["tool"] and not request["arguments"].get("output_subdir"):
        warnings.append("单图未指定 output_subdir：MCP 使用默认输出目录；重名会增加序号，请自行保存真实返回路径。")
    if "batch" not in request["tool"]:
        warnings.append("单图 filename 先经 Path.stem 去最后扩展名，再经 safe_filename 清洗；"
                        "请核对 predicted_output_stems，同一目录的不同输入文件名可能变成同名输出。")
    warnings.append("预测输出名按请求 n 推算；返回数量、真实扩展名及重名避让可能改变最终路径，请以 MCP 返回和结果检查为准。")
    page_records = []
    batch = "batch" in request["tool"]
    tasks = request["arguments"]["tasks"] if batch else [dict(request["arguments"], id=request["id"])]
    common = ({key: value for key, value in request["arguments"].items() if key != "tasks"} if batch else {})
    for task in tasks:
        page_records.append({"id": task["id"], "parent_request_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
                             "actual_tool": request["tool"], "common_arguments": copy.deepcopy(common),
                             "exact_task_arguments": copy.deepcopy(task)})
    return {"schema_version": 2, "ok": True, "stage": "preflight", "run_id": request["run_id"],
            "request_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "mcp_call": {"name": request["tool"], "arguments": copy.deepcopy(request["arguments"])},
            "page_request_records": page_records, "checks": checks, "warnings": warnings}


def png_size(data):
    require(len(data) >= 33 and data[12:16] == b"IHDR", "PNG 缺少完整 IHDR")
    offset, dimensions, idat = 8, None, False
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        end = offset + 12 + length
        require(end <= len(data), "PNG 数据块被截断")
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack_from(">I", data, offset + 8 + length)[0]
        require(zlib.crc32(kind + payload) & 0xFFFFFFFF == crc, "PNG 数据块 CRC 不匹配")
        if kind == b"IHDR":
            require(dimensions is None and offset == 8 and length == 13, "PNG IHDR 无效")
            dimensions = struct.unpack_from(">II", payload)
        elif kind == b"IDAT":
            idat = True
        elif kind == b"IEND":
            require(length == 0 and dimensions and idat and end == len(data), "PNG 容器结构不完整")
            return dimensions
        offset = end
    raise ValueError("PNG 缺少完整 IEND")


def jpeg_size(data):
    require(data.endswith(b"\xff\xd9"), "JPEG 缺少结束标记，可能被截断")
    offset = 2
    dimensions = None
    while offset < len(data):
        require(data[offset] == 0xFF, "JPEG 段标记无效")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        require(offset < len(data), "JPEG 段被截断")
        marker = data[offset]
        offset += 1
        if marker == 0xD9:
            break
        if marker in {0x01, 0xD8} or 0xD0 <= marker <= 0xD7:
            continue
        require(offset + 2 <= len(data), "JPEG 段长度被截断")
        length = struct.unpack_from(">H", data, offset)[0]
        require(length >= 2 and offset + length <= len(data), "JPEG 段越界")
        if marker in SOF_MARKERS:
            require(length >= 8, "JPEG SOF 被截断")
            height, width = struct.unpack_from(">HH", data, offset + 3)
            require(width > 0 and height > 0, "不支持 JPEG 延迟尺寸或零尺寸")
            dimensions = (width, height)
        if marker == 0xDA:
            require(dimensions, "JPEG 在扫描数据前没有尺寸信息")
            return dimensions
        offset += length
    raise ValueError("JPEG 缺少 SOF/SOS 尺寸与扫描信息")


def webp_size(data):
    require(len(data) >= 20 and struct.unpack_from("<I", data, 4)[0] + 8 == len(data),
            "WebP RIFF 长度不匹配或被截断")
    offset, canvas, image_dimensions = 12, None, None
    while offset + 8 <= len(data):
        kind = data[offset:offset + 4]
        length = struct.unpack_from("<I", data, offset + 4)[0]
        end = offset + 8 + length
        require(end + (length % 2) <= len(data), "WebP 数据块被截断")
        payload = data[offset + 8:end]
        if kind == b"VP8X":
            require(length == 10, "WebP VP8X 头无效")
            require(not payload[0] & 0x02, "动画 WebP 不在此静态图片检查范围")
            canvas = (int.from_bytes(payload[4:7], "little") + 1,
                      int.from_bytes(payload[7:10], "little") + 1)
        elif kind == b"VP8 ":
            require(length >= 10 and not payload[0] & 1 and payload[3:6] == b"\x9d\x01\x2a",
                    "WebP VP8 帧头无效")
            image_dimensions = tuple(value & 0x3FFF for value in struct.unpack_from("<HH", payload, 6))
        elif kind == b"VP8L":
            require(length >= 5 and payload[0] == 0x2F, "WebP VP8L 头无效")
            bits = int.from_bytes(payload[1:5], "little")
            require(bits >> 29 == 0, "WebP VP8L 版本不支持")
            image_dimensions = ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
        offset = end + length % 2
    require(offset == len(data) and image_dimensions, "WebP 缺少完整静态图像数据块")
    require(canvas is None or canvas == image_dimensions, "WebP 画布与图像尺寸不一致")
    return canvas or image_dimensions


def image_metadata(path):
    """Inspect stored raster dimensions, irrespective of misleading extensions."""
    data = Path(path).read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        kind, dimensions = "PNG", png_size(data)
    elif data.startswith(b"\xff\xd8"):
        kind, dimensions = "JPEG", jpeg_size(data)
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        kind, dimensions = "WebP", webp_size(data)
    else:
        raise ValueError("未知或不支持的图片格式；仅支持静态 PNG、JPEG、WebP")
    require(min(dimensions) > 0, "图片包含零尺寸")
    return {"format": kind, "width": dimensions[0], "height": dimensions[1],
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def unwrap_result(value):
    require(isinstance(value, dict), "MCP 返回必须为对象")
    if "jsonrpc" in value:
        require("error" not in value, "MCP JSON-RPC 返回错误")
        value = value.get("result")
        require(isinstance(value, dict), "JSON-RPC 缺少 result 对象")
    require(not value.get("isError"), "MCP 返回 isError，不能当作图片成功结果")
    if "structuredContent" in value:
        value = value["structuredContent"]
    elif "content" in value:
        texts = [item.get("text") for item in value["content"]
                 if isinstance(item, dict) and item.get("type") == "text"]
        require(len(texts) == 1, "无法唯一识别 MCP 文本结果，请保存原始结构化返回")
        value = json.loads(texts[0])
    require(isinstance(value, dict), "MCP 图片结果必须为对象")
    return value


def inspect_result(request, result, strict_ratio=None):
    checks = validate_request(request, strict_ratio, check_paths=False)
    result = unwrap_result(result)
    expected = {item["id"]: item for item in checks}
    batch = "batch" in request["tool"]
    rows = result.get("results") if batch else [dict(result, id=request["id"])]
    require(isinstance(rows, list), "批量 MCP 返回缺少 results 数组")
    by_id = {}
    for row in rows:
        require(isinstance(row, dict), "MCP results 项必须是对象")
        task_id = row.get("id")
        require(isinstance(task_id, str) and task_id in expected, "MCP 返回未知或缺失任务 id")
        require(task_id not in by_id, f"MCP 返回重复任务 id：{task_id}")
        by_id[task_id] = row
    report_tasks = []
    all_paths = set()
    for task_id, check in expected.items():
        row = by_id.get(task_id)
        task_report = {"id": task_id, "requested_size": check["requested_size"],
                       "expected_ratio": check["expected_ratio"], "ok": False, "images": [], "errors": []}
        report_tasks.append(task_report)
        if row is None:
            task_report["errors"].append("MCP 返回缺少该任务")
            continue
        if row.get("success") is not True:
            task_report["errors"].append("MCP 未明确返回 success:true；不能认定生成成功")
        paths = row.get("images")
        if not isinstance(paths, list) or not paths:
            task_report["errors"].append("MCP 返回没有非空 images 数组")
            continue
        if len(paths) != check["n"]:
            task_report["errors"].append(f"输出图片数量 {len(paths)} 不等于请求 n={check['n']}")
        for value in paths:
            entry = {"path": value, "ok": False}
            task_report["images"].append(entry)
            try:
                path = absolute_file(value, "输出图片")
                normalized = str(path.resolve())
                require(normalized not in all_paths, "多个结果重复指向同一图片路径，不能当作独立输出")
                all_paths.add(normalized)
                entry.update(image_metadata(path))
                actual = (entry["width"], entry["height"])
                entry["pixels_match"] = actual == (check["width"], check["height"])
                entry["ratio_match"] = ratio_matches(actual, ratio_pair(check["expected_ratio"]))
                entry["ok"] = entry["pixels_match"] and entry["ratio_match"]
                if not entry["ok"]:
                    entry["error"] = "实际存储像素或比例与请求不符；保留原图并反馈，禁止自动裁切冒充通过"
            except (ValueError, OSError, struct.error) as exc:
                entry["error"] = str(exc)
        task_report["ok"] = not task_report["errors"] and all(item["ok"] for item in task_report["images"])
    return {"schema_version": 1, "stage": "inspect", "run_id": request["run_id"],
            "ok": result.get("success") is True and all(item["ok"] for item in report_tasks),
            "mcp_success": result.get("success") is True,
            "inspection_scope": "container_metadata_and_stored_pixel_dimensions_only",
            "visual_review_required": True, "full_decode_performed": False,
            "review_mode": "human_on_delivery", "automatic_visual_review": False,
            "visual_review_status": "pending_human_review", "publish_ready": False,
            "notes": ["未进行完整像素解码；容器尺寸符合不等于坏图检测、视觉质量、文字或产品准确性通过。",
                      "默认交付图片由用户人工审稿；visual_review_required 表示仍待内容审核，不触发自动视觉评估或返工。",
                      "JPEG 比较编码存储宽高，不应用 EXIF 旋转；带旋转的图需人工核对实际显示方向。"],
            "tasks": report_tasks}


def emit(value, output=None):
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if output:
        with Path(output).open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    else:
        print(text, end="")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    before = commands.add_parser("preflight", help="离线校验请求，不调用 MCP")
    before.add_argument("--input", required=True)
    after = commands.add_parser("inspect", help="检查 MCP 返回中的真实图片尺寸，不改图")
    after.add_argument("--request", required=True)
    after.add_argument("--result", required=True)
    for command in (before, after):
        command.add_argument("--strict-ratio", help="可选，如 3:4；不自动修改请求")
        command.add_argument("--output", help="新建 JSON 报告；已有文件会被拒绝覆盖")
    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            value = preflight(load(args.input), args.strict_ratio)
        else:
            value = inspect_result(load(args.request), load(args.result), args.strict_ratio)
        emit(value, args.output)
        return 0 if value["ok"] else 1
    except (ValueError, TypeError, OSError, KeyError) as exc:
        emit({"ok": False, "stage": args.command, "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
