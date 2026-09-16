"""Bounded, read-only product lookup through the installed DWS CLI (stdlib only)."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.parse import parse_qsl, urlsplit


BASE_ID = "XPwkYGxZV3RoKY39F4jRm2LlWAgozOKL"
TABLE_ID = "dv19yqvsgs3oebp3pcjys"
VIEW_ID = "zkiuymun6a9yvf8ixgovv"
PAGE_LIMIT = 10
PAGE_SIZE = 100
INDEX_FIELDS = ("产品名称", "产品系列", "上架情况", "最近更新")
DETAIL_FIELDS = INDEX_FIELDS + (
    "产品卖点", "Slogan", "产品图", "产品资料", "产品详情钉盘链接", "白底图",
    "原片共享盘位置", "包装设计图链接",
)
SECRET_KEY = re.compile(r"token|secret|password|authorization|cookie|signature|credential|signed", re.I)
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.I)


class MaterialsError(Exception):
    def __init__(self, category: str, message: str, code: str | None = None):
        super().__init__(message)
        self.category, self.message, self.code = category, message, code

    def as_dict(self) -> dict:
        result = {"category": self.category, "message": self.message}
        if self.code:
            result["code"] = self.code
        return result


def safe_code(value: Any) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", value) else None


def temporary_url(value: str) -> bool:
    try:
        for key, _ in parse_qsl(urlsplit(value).query, keep_blank_values=True):
            normalized = key.lower().replace("-", "_")
            if SECRET_KEY.search(normalized) or normalized in {"expires", "expiry", "expiration", "expire", "ossaccesskeyid", "accesskeyid", "access_key", "auth_key", "policy"} or normalized.startswith(("x_amz_", "x_oss_", "x_cos_")):
                return True
    except ValueError:
        return True
    return False


def safe_text(value: str) -> str:
    return URL_PATTERN.sub(lambda match: "[临时链接已隐藏]" if temporary_url(match.group(0)) else match.group(0), value)


def public_value(value: Any) -> Any:
    if isinstance(value, str):
        return safe_text(value)
    if isinstance(value, list):
        return [public_value(item) for item in value]
    if isinstance(value, dict):
        return {key: public_value(item) for key, item in value.items() if not SECRET_KEY.search(str(key))}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return None


def text_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return "".join(text_value(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "name", "value", "content", "markdown"):
            if key in value:
                return text_value(value[key])
    return ""


def data_layers(value: Any):
    """Inspect only known response wrappers, never arbitrary nested user cells."""
    for _ in range(5):
        yield value
        if isinstance(value, dict) and "data" in value:
            value = value["data"]
        else:
            break


def collection(value: Any, keys: tuple[str, ...], label: str) -> list:
    for layer in data_layers(value):
        if isinstance(layer, list):
            return layer
        if isinstance(layer, dict):
            for key in keys:
                if isinstance(layer.get(key), list):
                    return layer[key]
    raise MaterialsError("invalid_response", f"DWS 未返回可识别的{label}数组。")


class DWS:
    def __init__(self, executable: str = "dws"):
        self.executable = executable

    def call(self, arguments: list[str], profile: str | None = None) -> dict:
        command = [self.executable, *arguments, "--format", "json"]
        if profile:
            command += ["--profile", profile]
        try:
            result = subprocess.run(command, shell=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            raise MaterialsError("timeout", "DWS 读取超时；未自动重试。") from None
        except OSError:
            raise MaterialsError("local_runtime", "无法启动 DWS，请检查安装路径或 --dws 参数。") from None
        payload = None
        for stream in (result.stdout, result.stderr):
            try:
                candidate = json.loads(stream.lstrip("\ufeff"))
            except (ValueError, AttributeError):
                continue
            if isinstance(candidate, dict):
                payload = candidate
                if candidate.get("ok") is False or candidate.get("error"):
                    break
        if payload is None:
            raise MaterialsError("invalid_response", "DWS 未返回有效 JSON；原始输出已隐藏。")
        if result.returncode != 0 or payload.get("ok") is False or payload.get("success") is False or payload.get("error") or payload.get("outcome") not in (None, "success") or payload.get("status") not in (None, "success"):
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            category = safe_code(error.get("category") or error.get("type")) or "dws_error"
            code = safe_code(error.get("code") or error.get("upstream_code") or error.get("server_error_code"))
            # Upstream messages can include signed URLs, headers and credentials.
            raise MaterialsError(category, "DWS 读取失败；请检查当前账号、权限及错误代码。原始错误详情已隐藏。", code)
        for layer in data_layers(payload):
            if isinstance(layer, dict) and (layer.get("complete") is False or layer.get("status") in {"partial_success", "failure"}):
                raise MaterialsError("incomplete", "DWS 返回部分结果，本次不将其作为完整资料。")
        return payload


def choose_profile(payload: dict, explicit: str | None = None) -> str:
    profiles = collection(payload, ("profiles", "items"), "账号")
    found = []
    for item in profiles:
        if not isinstance(item, dict):
            raise MaterialsError("invalid_response", "DWS 账号项格式不正确。")
        name = item.get("profile") or item.get("name") or item.get("id")
        if isinstance(name, str) and name:
            found.append((name, item))
    if explicit:
        matches = [name for name, _ in found if name == explicit]
        if len(matches) == 1:
            return matches[0]
        raise MaterialsError("profile_required", "指定 profile 不存在或不唯一，请检查 DWS 账号列表。")
    preferred = [name for name, item in found if item.get("isOrgCurrent") is True]
    if not preferred:
        preferred = [name for name, item in found if item.get("current") is True or item.get("isCurrent") is True]
    if len(preferred) == 1:
        return preferred[0]
    if not preferred and len(found) == 1:
        return found[0][0]
    raise MaterialsError("profile_required", "无法唯一确定当前组织账号，请用 --profile 指定。")


def field_directory(payload: dict) -> tuple[dict, dict]:
    items = collection(payload, ("fields", "items"), "字段")
    by_id, by_name = {}, {}
    for field in items:
        if not isinstance(field, dict):
            raise MaterialsError("invalid_response", "DWS 字段项格式不正确。")
        field_id = field.get("fieldId") or field.get("id")
        name = field.get("name") or field.get("fieldName")
        if not isinstance(field_id, str) or not isinstance(name, str):
            raise MaterialsError("invalid_response", "DWS 字段缺少 ID 或名称。")
        if field_id in by_id or name in by_name:
            raise MaterialsError("ambiguous_field", "字段名称或 ID 重复，无法安全投影。")
        by_id[field_id] = field
        by_name[name] = field_id
    for name in INDEX_FIELDS:
        if name not in by_name:
            raise MaterialsError("field_changed", f"缺少必需字段：{name}。请核对资料表字段变更。")
    return by_id, by_name


def option_names(field: dict) -> dict[str, str]:
    config = field.get("config") or field.get("property") or {}
    options = config.get("options", []) if isinstance(config, dict) else []
    if isinstance(options, dict):
        options = [{"id": key, **value} for key, value in options.items() if isinstance(value, dict)]
    if not isinstance(options, list):
        raise MaterialsError("unsupported_view_filter", "视图选项配置格式不受支持。")
    mapped = {}
    for option in options:
        if isinstance(option, dict):
            name = option.get("name")
            option_id = option.get("id") or option.get("optionId")
            if isinstance(name, str) and isinstance(option_id, str):
                mapped[option_id] = name
                mapped[name] = name
    return mapped


def view_filter(payload: dict) -> Any:
    for layer in data_layers(payload):
        if isinstance(layer, list):
            return layer
        if isinstance(layer, dict):
            if not layer:
                return []
            if "filter" in layer:
                return layer["filter"]
            if "operator" in layer:
                return layer
            if isinstance(layer.get("config"), dict) and "filter" in layer["config"]:
                return layer["config"]["filter"]
    raise MaterialsError("invalid_response", "未取得视图筛选配置，不能退回全表查询。")


def convert_filter(rule: Any, fields: dict, depth: int = 0) -> dict:
    if depth > 12:
        raise MaterialsError("unsupported_view_filter", "视图筛选嵌套过深。")
    if isinstance(rule, list):
        rule = {"operator": "and", "operands": rule}
    if not isinstance(rule, dict):
        raise MaterialsError("unsupported_view_filter", "视图筛选配置格式不受支持。")
    operator, operands = rule.get("operator"), rule.get("operands")
    if not isinstance(operands, list):
        raise MaterialsError("unsupported_view_filter", "视图筛选缺少 operands 数组。")
    if operator in {"and", "or"}:
        return {"operator": operator, "operands": [convert_filter(item, fields, depth + 1) for item in operands]}
    if operator != "any_of" or len(operands) != 2 or not isinstance(operands[0], str):
        raise MaterialsError("unsupported_view_filter", "当前仅支持视图的 and/or/any_of 筛选；没有改用全表。")
    field_id, selected = operands
    field = fields.get(field_id)
    if not field or field.get("type") not in {"multipleSelect", "singleSelect"}:
        raise MaterialsError("unsupported_view_filter", "视图筛选引用的选项字段不存在或类型已变化。")
    selected = selected if isinstance(selected, list) else [selected]
    mapping = option_names(field)
    if not selected or any(not isinstance(item, str) or item not in mapping for item in selected):
        raise MaterialsError("unsupported_view_filter", "视图筛选引用了未知选项，请刷新字段配置。")
    return {"operator": "any_of", "operands": [field_id, [mapping[item] for item in selected]]}


def filter_fields(rule: dict) -> set[str]:
    if rule["operator"] in {"and", "or"}:
        return set().union(*(filter_fields(item) for item in rule["operands"]))
    return {rule["operands"][0]}


def cell_map(record: dict) -> dict:
    cells = record.get("cells")
    if isinstance(cells, dict):
        return cells
    if isinstance(record.get("fields"), dict):
        return record["fields"]
    raise MaterialsError("invalid_response", "记录未返回按字段 ID 标识的 cells。")


def selected_names(value: Any, field: dict) -> list[str]:
    mapping = option_names(field)
    entries = value if isinstance(value, list) else [value]
    result = []
    for item in entries:
        if isinstance(item, dict):
            item = item.get("name") or item.get("id") or item.get("optionId") or item.get("value")
        if isinstance(item, str):
            result.append(mapping.get(item, item))
        elif item is not None:
            raise MaterialsError("invalid_response", "记录选项值格式不受支持。")
    return result


def in_view(cells: dict, rule: dict, fields: dict) -> bool:
    operator, operands = rule["operator"], rule["operands"]
    if operator == "and":
        return all(in_view(cells, child, fields) for child in operands)
    if operator == "or":
        return any(in_view(cells, child, fields) for child in operands)
    field_id, wanted = operands
    if field_id not in cells:
        raise MaterialsError("incomplete", "返回记录缺少视图筛选字段，无法确认所属范围。")
    return bool(set(selected_names(cells[field_id], fields[field_id])) & set(wanted))


def record_list(payload: dict, requested: str | None = None) -> list[dict]:
    records = collection(payload, ("records",), "记录")
    ids = []
    for record in records:
        if not isinstance(record, dict):
            raise MaterialsError("invalid_response", "记录项格式不正确。")
        identifier = record.get("recordId") or record.get("id") or record.get("record_id")
        if not isinstance(identifier, str) or not identifier:
            raise MaterialsError("invalid_response", "记录缺少稳定 recordId。")
        record["recordId"] = identifier
        ids.append(identifier)
    if len(ids) != len(set(ids)) or len(ids) > PAGE_LIMIT * PAGE_SIZE:
        raise MaterialsError("incomplete", "返回记录重复或超过分页上限，未形成完整结果。")
    markers = [layer.get("hasMore", layer.get("has_more")) for layer in data_layers(payload) if isinstance(layer, dict) and ("hasMore" in layer or "has_more" in layer)]
    if any(marker is not False for marker in markers) or not markers and not (requested and ids in ([], [requested])):
        raise MaterialsError("pagination_limit", "查询分页未确认结束，可能达到 page-limit；本次不返回部分匹配。")
    return records


def attachment_metadata(value: Any) -> list[dict]:
    if isinstance(value, dict):
        value = value.get("attachments", value.get("value", [value]))
    if value is None:
        return []
    if not isinstance(value, list):
        raise MaterialsError("invalid_response", "产品图附件格式不受支持。")
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise MaterialsError("invalid_response", "产品图附件项格式不受支持。")
        output = {}
        for key in ("resourceId", "resource_id", "name", "filename", "fileName", "size", "fileSize", "type", "mimeType", "width", "height"):
            if key in item:
                output[key] = public_value(item[key])
        output["downloaded"] = False
        result.append(output)
    return result


class ProductMaterials:
    def __init__(self, dws: DWS, profile: str | None = None, scope: str = "view"):
        self.dws, self.requested_profile, self.scope = dws, profile, scope

    def prepare(self):
        self.profile = choose_profile(self.dws.call(["profile", "list"]), self.requested_profile)
        arguments = ["--base-id", BASE_ID, "--table-id", TABLE_ID]
        self.fields, self.names = field_directory(self.dws.call(["aitable", "field", "list", *arguments], self.profile))
        self.rule = None
        if self.scope == "view":
            raw = view_filter(self.dws.call(["aitable", "view", "get", "filter", *arguments, "--view-id", VIEW_ID], self.profile))
            self.rule = convert_filter(raw, self.fields)
            if self.rule["operator"] not in {"and", "or"}:
                self.rule = {"operator": "and", "operands": [self.rule]}

    def query(self, names: tuple[str, ...], record_id: str | None = None) -> list[dict]:
        ids = [self.names[name] for name in names if name in self.names]
        if self.rule:
            if not filter_fields(self.rule).issubset(ids):
                raise MaterialsError("unsupported_view_filter", "视图筛选引用了本次白名单之外的字段，未扩大字段读取范围。")
        args = ["aitable", "record", "query", "--base-id", BASE_ID, "--table-id", TABLE_ID,
                "--field-ids", ",".join(ids), "--all", "--page-limit", str(PAGE_LIMIT), "--limit", str(PAGE_SIZE)]
        if self.rule:
            args += ["--filters", json.dumps(self.rule, ensure_ascii=False, separators=(",", ":"))]
        if record_id:
            args += ["--record-ids", record_id]
        records = record_list(self.dws.call(args, self.profile), requested=record_id)
        if record_id and any(record["recordId"] != record_id for record in records):
            raise MaterialsError("identity_mismatch", "DWS 返回了非请求记录，本次停止。")
        # Some DWS/API versions ignore filters when record-ids is supplied.
        if self.rule:
            records = [record for record in records if in_view(cell_map(record), self.rule, self.fields)]
        return records

    def project(self, record: dict, detail: bool = False) -> dict:
        cells = cell_map(record)
        result = {"record_id": record["recordId"], "fields": {}}
        for name in DETAIL_FIELDS if detail else INDEX_FIELDS:
            field_id = self.names.get(name)
            if field_id is None or field_id not in cells:
                continue
            value = cells[field_id]
            field_type = self.fields[field_id].get("type")
            if name == "产品图" or field_type == "attachment":
                value = attachment_metadata(value)
            elif field_type in {"multipleSelect", "singleSelect"}:
                value = selected_names(value, self.fields[field_id])
            result["fields"][name] = public_value(value)
        return result

    def result_header(self) -> dict:
        return {"ok": True, "base_id": BASE_ID, "table_id": TABLE_ID, "view_id": VIEW_ID if self.scope == "view" else None,
                "scope": self.scope, "profile": self.profile, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "complete": True, "source_status": "来源表记录，未经本工具业务审批"}

    def find(self, name: str) -> dict:
        needle = re.sub(r"\s+", "", name).casefold()
        if not needle:
            raise MaterialsError("validation", "产品名称不能为空。")
        self.prepare()
        records = self.query(INDEX_FIELDS)
        matches, exact = [], []
        for record in records:
            title = text_value(cell_map(record).get(self.names["产品名称"]))
            normalized = re.sub(r"\s+", "", title).casefold()
            if needle in normalized:
                matches.append(record)
            if needle == normalized:
                exact.append(record)
        selected = exact or matches
        return {**self.result_header(), "query": safe_text(name), "match_type": "exact" if exact else "name_contains",
                "status": "not_found" if not selected else "unique" if len(selected) == 1 else "ambiguous",
                "count": len(selected), "candidates": [self.project(record) for record in selected]}

    def list_products(self, status: str | None = None) -> dict:
        self.prepare()
        state_id = self.names["上架情况"]
        state_field = self.fields[state_id]
        if status is not None and status not in set(option_names(state_field).values()):
            raise MaterialsError("invalid_status", "上架情况必须与当前字段选项名称完全一致。")
        records = self.query(INDEX_FIELDS)
        if status is not None:
            records = [record for record in records if status in selected_names(cell_map(record).get(state_id), state_field)]
        return {**self.result_header(), "status_filter": status, "count": len(records),
                "products": [self.project(record) for record in records]}

    def get_raw(self, record_id: str) -> dict:
        """Fresh scoped record, in memory only, for the attachment downloader.

        Callers must never log or serialize this raw record: it can contain
        expiring signed image URLs. The CLI exposes only get(), not get_raw().
        """
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", record_id):
            raise MaterialsError("validation", "record-id 格式不正确。")
        self.prepare()
        records = self.query(DETAIL_FIELDS, record_id)
        if not records:
            raise MaterialsError("not_found_in_scope", "指定记录不存在、不可读或不属于所选视图范围。")
        return records[0]

    def get(self, record_id: str) -> dict:
        record = self.get_raw(record_id)
        return {**self.result_header(), "product": self.project(record, True),
                "missing_fields": [name for name in DETAIL_FIELDS if name not in self.names or self.names[name] not in cell_map(record)],
                "limitations": ["附件仅返回元数据，尚未下载或验证图片文件。", "本工具未解析资料 URL 所指文件，也未读取旧知识库缓存。"]}


def parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--profile", default=argparse.SUPPRESS)
    shared.add_argument("--scope", choices=("view", "table"), default=argparse.SUPPRESS)
    shared.add_argument("--output", type=Path, default=argparse.SUPPRESS)
    shared.add_argument("--dws", default=argparse.SUPPRESS)
    result = argparse.ArgumentParser(description=__doc__, parents=[shared])
    commands = result.add_subparsers(dest="command", required=True)
    find = commands.add_parser("find", parents=[shared])
    find.add_argument("--name", required=True)
    listing = commands.add_parser("list", parents=[shared])
    listing.add_argument("--status", help="按上架情况选项的完整名称筛选；省略时返回所选范围的全部产品索引")
    get = commands.add_parser("get", parents=[shared])
    get.add_argument("--record-id", required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        service = ProductMaterials(DWS(getattr(args, "dws", os.environ.get("DWS_EXECUTABLE", "dws"))),
                                   getattr(args, "profile", None), getattr(args, "scope", "view"))
        if args.command == "find":
            result = service.find(args.name)
        elif args.command == "list":
            result = service.list_products(args.status)
        else:
            result = service.get(args.record_id)
        encoded = json.dumps(public_value(result), ensure_ascii=False, indent=2)
        output = getattr(args, "output", None)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
        print(encoded)
        return 0
    except MaterialsError as exc:
        print(json.dumps({"ok": False, "error": exc.as_dict()}, ensure_ascii=False), file=sys.stderr)
        return 2
    except FileExistsError:
        print(json.dumps({"ok": False, "error": {"category": "output_exists", "message": "输出文件已存在，请使用新的 --output 路径；已有文件未覆盖。"}}, ensure_ascii=False), file=sys.stderr)
        return 2
    except OSError:
        print(json.dumps({"ok": False, "error": {"category": "local_io", "message": "本地文件读写失败；未输出原始系统错误。"}}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
