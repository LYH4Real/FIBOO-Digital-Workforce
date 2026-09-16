import copy
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "product_materials.py"
spec = importlib.util.spec_from_file_location("product_materials", SCRIPT)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def fields(name_id="name", include_name=True):
    items = [
        {"fieldId": name_id, "fieldName": "产品名称", "type": "text"},
        {"fieldId": "series", "fieldName": "产品系列", "type": "singleSelect", "config": {"options": [{"id": "basic", "name": "基础健康系列"}]}},
        {"fieldId": "state", "fieldName": "上架情况", "type": "multipleSelect", "config": {"options": [{"id": "on", "name": "已上架"}, {"id": "off", "name": "已下架"}, {"id": "soon", "name": "待上架"}]}},
        {"fieldId": "updated", "fieldName": "最近更新", "type": "date"},
        {"fieldId": "selling", "fieldName": "产品卖点", "type": "text"},
        {"fieldId": "photo", "fieldName": "产品图", "type": "attachment"},
        {"fieldId": "doc", "fieldName": "产品资料", "type": "url"},
        {"fieldId": "private", "fieldName": "授权人员手机号", "type": "text"},
    ]
    return {"data": {"fields": items if include_name else items[1:]}, "success": True, "error": {}, "status": "success"}


RULE = {"operator": "and", "operands": [{"operator": "any_of", "operands": ["state", ["on", "soon"]]}]}


def record(identifier, title, state="on", name_id="name"):
    return {"recordId": identifier, "cells": {
        name_id: title,
        "series": {"id": "basic", "name": "基础健康系列"},
        "state": [{"id": state, "name": {"on": "已上架", "off": "已下架", "soon": "待上架"}[state]}],
        "updated": "2026-08-11T17:13:14+08:00",
        "selling": {"markdown": "包含蛋白粉的卖点"},
        "doc": {"link": "https://qr.dingtalk.com/page/yunpan?spaceId=123&fileId=456&type=file", "text": "产品资料"},
        "photo": [{"filename": "3.png", "resourceId": "image-resource", "size": 100, "type": "image", "resourceUrl": "/core/api/resources/img/privateopaque", "url": "https://images.oss.aliyuncs.com/image.png?Expires=123&Signature=SECRET_SIGNATURE&OSSAccessKeyId=SECRET_KEY"}],
        "private": "not permitted in projection",
    }}


class FakeDWS:
    def __init__(self, records=None, fields_payload=None, rule=None, has_more=False, profiles=None):
        self.records = records if records is not None else [record("box", "多重蛋白粉（盒装）"), record("can", "多重蛋白粉（400g）"), record("shake", "奶昔")]
        self.fields_payload = fields_payload or fields()
        self.rule = RULE if rule is None else rule
        self.has_more = has_more
        self.profiles = profiles or {"profiles": [{"profile": "corp:user", "isCurrent": True, "isOrgCurrent": True}], "currentProfile": "corp:user"}
        self.calls = []

    def call(self, arguments, profile=None):
        self.calls.append((arguments, profile))
        if arguments[:2] == ["profile", "list"]:
            return copy.deepcopy(self.profiles)
        if arguments[:3] == ["aitable", "field", "list"]:
            return copy.deepcopy(self.fields_payload)
        if arguments[:4] == ["aitable", "view", "get", "filter"]:
            return {"data": copy.deepcopy(self.rule), "status": "success", "error": {}}
        if arguments[:3] == ["aitable", "record", "query"]:
            if "--record-ids" in arguments:
                target = arguments[arguments.index("--record-ids") + 1]
                return {"data": {"records": copy.deepcopy([row for row in self.records if row["recordId"] == target])}, "success": True, "status": "success", "error": {}}
            return {"data": {"records": copy.deepcopy(self.records), "hasMore": self.has_more, "complete": not self.has_more, "pages": 1, "fetchedCount": len(self.records)}}
        raise AssertionError(arguments)


class ProductMaterialsTests(unittest.TestCase):
    def test_find_matches_name_only_and_returns_both_packaging_candidates(self):
        dws = FakeDWS()
        result = m.ProductMaterials(dws).find("多重蛋白粉")
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual([row["record_id"] for row in result["candidates"]], ["box", "can"])
        result = m.ProductMaterials(FakeDWS()).find("蛋白粉")
        self.assertEqual(result["count"], 2, "selling-point matches must not include the milkshake")

    def test_exact_product_name_has_priority(self):
        result = m.ProductMaterials(FakeDWS()).find("多重蛋白粉（盒装）")
        self.assertEqual(result["status"], "unique")
        self.assertEqual(result["match_type"], "exact")

    def test_list_indexes_all_or_exact_status_without_fetching_details(self):
        rows = [record("live", "在售产品"), record("retired", "下架产品", state="off")]
        dws = FakeDWS(records=rows)
        result = m.ProductMaterials(dws, scope="table").list_products()
        self.assertEqual(result["count"], 2)
        self.assertIsNone(result["status_filter"])
        result = m.ProductMaterials(dws, scope="table").list_products("已下架")
        self.assertEqual([item["record_id"] for item in result["products"]], ["retired"])
        args = dws.calls[-1][0]
        self.assertEqual(set(args[args.index("--field-ids") + 1].split(",")), {"name", "series", "state", "updated"})
        self.assertNotIn("产品图", result["products"][0]["fields"])
        view_result = m.ProductMaterials(FakeDWS(records=rows)).list_products("已下架")
        self.assertEqual(view_result["count"], 0, "status must not widen the selected view")

    def test_list_rejects_unknown_or_nonexact_status_before_record_query(self):
        for status in ("off", "下架", " 已下架 "):
            dws = FakeDWS()
            with self.subTest(status=status), self.assertRaises(m.MaterialsError) as caught:
                m.ProductMaterials(dws, scope="table").list_products(status)
            self.assertEqual(caught.exception.category, "invalid_status")
            self.assertFalse(any(args[:3] == ["aitable", "record", "query"] for args, _ in dws.calls))
        parsed = m.parser().parse_args(["list", "--scope", "table", "--status", "已下架"])
        self.assertEqual((parsed.command, parsed.scope, parsed.status), ("list", "table", "已下架"))

    def test_field_ids_are_resolved_from_latest_directory(self):
        dws = FakeDWS(records=[record("new", "测试新品", name_id="renamed-id")], fields_payload=fields("renamed-id"))
        result = m.ProductMaterials(dws).find("测试")
        self.assertEqual(result["count"], 1)
        query = dws.calls[-1][0]
        projection = query[query.index("--field-ids") + 1].split(",")
        self.assertIn("renamed-id", projection)
        self.assertNotIn("private", projection)
        self.assertNotIn("selling", projection)

    def test_missing_required_field_fails_before_query(self):
        dws = FakeDWS(fields_payload=fields(include_name=False))
        with self.assertRaises(m.MaterialsError) as caught:
            m.ProductMaterials(dws).find("产品")
        self.assertEqual(caught.exception.category, "field_changed")
        self.assertFalse(any(args[:3] == ["aitable", "record", "query"] for args, _ in dws.calls))

    def test_view_option_ids_become_exact_option_names(self):
        dws = FakeDWS()
        m.ProductMaterials(dws).find("蛋白粉")
        args = dws.calls[-1][0]
        rule = json.loads(args[args.index("--filters") + 1])
        self.assertEqual(rule["operands"][0]["operands"], ["state", ["已上架", "待上架"]])
        view_call = dws.calls[2][0]
        self.assertEqual(view_call[view_call.index("--view-id") + 1], "zkiuymun6a9yvf8ixgovv")

    def test_nested_filter_and_or_conversion(self):
        definitions, _ = m.field_directory(fields())
        rule = {"operator": "or", "operands": [RULE, {"operator": "any_of", "operands": ["state", ["off"]]}]}
        converted = m.convert_filter(rule, definitions)
        self.assertTrue(m.in_view(record("off", "old", state="off")["cells"], converted, definitions))

    def test_unknown_filter_does_not_silently_query_whole_table(self):
        dws = FakeDWS(rule={"operator": "and", "operands": [{"operator": "unknown", "operands": ["state", "on"]}]})
        with self.assertRaises(m.MaterialsError) as caught:
            m.ProductMaterials(dws).find("产品")
        self.assertEqual(caught.exception.category, "unsupported_view_filter")
        self.assertFalse(any(args[:3] == ["aitable", "record", "query"] for args, _ in dws.calls))

    def test_get_checks_view_membership_even_when_record_ids_ignore_filter(self):
        dws = FakeDWS(records=[record("retired", "已下架产品", state="off")])
        with self.assertRaises(m.MaterialsError) as caught:
            m.ProductMaterials(dws).get("retired")
        self.assertEqual(caught.exception.category, "not_found_in_scope")

    def test_explicit_table_scope_includes_retired_records_without_view_calls(self):
        dws = FakeDWS(records=[record("retired", "已下架产品", state="off")])
        result = m.ProductMaterials(dws, scope="table").get("retired")
        self.assertEqual(result["scope"], "table")
        self.assertIsNone(result["view_id"])
        self.assertFalse(any(args[:3] == ["aitable", "view", "get"] for args, _ in dws.calls))

    def test_get_redacts_all_attachment_urls_and_private_fields(self):
        result = m.ProductMaterials(FakeDWS()).get("box")
        encoded = json.dumps(result, ensure_ascii=False)
        for forbidden in ("SECRET_SIGNATURE", "SECRET_KEY", "resourceUrl", "privateopaque", "not permitted in projection"):
            self.assertNotIn(forbidden, encoded)
        self.assertIn("fileId=456", encoded)
        attachment = result["product"]["fields"]["产品图"][0]
        self.assertEqual(attachment["filename"], "3.png")
        self.assertEqual(attachment["resourceId"], "image-resource")
        self.assertFalse(attachment["downloaded"])

    def test_get_raw_keeps_fresh_attachment_only_in_memory(self):
        service = m.ProductMaterials(FakeDWS())
        raw = service.get_raw("box")
        self.assertIn("Signature=", raw["cells"][service.names["产品图"]][0]["url"])
        self.assertEqual(service.profile, "corp:user")

    def test_signed_links_in_markdown_are_removed(self):
        row = record("a", "产品")
        row["cells"]["selling"] = {"markdown": "图片 https://host.invalid/file?signature=SECRET_TEXT 说明"}
        result = m.ProductMaterials(FakeDWS(records=[row])).get("a")
        self.assertNotIn("SECRET_TEXT", json.dumps(result))

    def test_incomplete_pagination_fails_instead_of_returning_partial_candidates(self):
        with self.assertRaises(m.MaterialsError) as caught:
            m.ProductMaterials(FakeDWS(has_more=True)).find("蛋白粉")
        self.assertEqual(caught.exception.category, "pagination_limit")

    def test_query_has_explicit_bounded_all_pages_and_no_fulltext_search(self):
        dws = FakeDWS()
        m.ProductMaterials(dws).find("蛋白粉")
        args = dws.calls[-1][0]
        self.assertIn("--all", args)
        self.assertEqual(args[args.index("--page-limit") + 1], "10")
        self.assertEqual(args[args.index("--limit") + 1], "100")
        self.assertNotIn("--query", args)

    def test_profile_is_unique_and_consistent(self):
        dws = FakeDWS()
        result = m.ProductMaterials(dws, profile="corp:user").find("蛋白粉")
        self.assertEqual(result["profile"], "corp:user")
        self.assertEqual(datetime.fromisoformat(result["retrieved_at"]).utcoffset(), timedelta(0))
        self.assertTrue(all(profile == "corp:user" for _, profile in dws.calls[1:]))
        with self.assertRaises(m.MaterialsError):
            m.choose_profile({"profiles": [{"profile": "one"}, {"profile": "two"}]})
        self.assertEqual(m.choose_profile({"profiles": [{"profile": "single"}]}), "single")

    def test_profile_id_is_preferred_over_human_display_name(self):
        self.assertEqual(m.choose_profile({"profiles": [{"profile": "corp:user", "name": "Display Name", "isOrgCurrent": True}]}), "corp:user")

    def test_empty_view_filter_is_explicitly_unfiltered(self):
        dws = FakeDWS(rule={})
        result = m.ProductMaterials(dws).find("蛋白粉")
        self.assertEqual(result["scope"], "view")
        args = dws.calls[-1][0]
        self.assertEqual(json.loads(args[args.index("--filters") + 1]), {"operator": "and", "operands": []})

    def test_get_missing_record_returns_clear_scoped_not_found(self):
        with self.assertRaises(m.MaterialsError) as caught:
            m.ProductMaterials(FakeDWS()).get("missing")
        self.assertEqual(caught.exception.category, "not_found_in_scope")

    def test_filter_cannot_expand_projection_to_private_fields(self):
        directory = fields()
        directory["data"]["fields"][-1].update({"type": "multipleSelect", "config": {"options": [{"id": "on", "name": "selected"}]}})
        dws = FakeDWS(fields_payload=directory, rule={"operator": "and", "operands": [{"operator": "any_of", "operands": ["private", ["on"]]}]})
        with self.assertRaises(m.MaterialsError) as caught:
            m.ProductMaterials(dws).find("产品")
        self.assertEqual(caught.exception.category, "unsupported_view_filter")
        self.assertFalse(any(args[:3] == ["aitable", "record", "query"] for args, _ in dws.calls))

    def test_dws_errors_do_not_expose_stderr_credentials(self):
        payload = {"status": "failure", "success": False, "error": {"category": "auth", "code": "AUTH_EXPIRED", "message": "token=SECRET_CREDENTIAL https://host.invalid/a?Signature=SECRET_URL"}}
        completed = subprocess.CompletedProcess([], 1, "", json.dumps(payload))
        with patch.object(m.subprocess, "run", return_value=completed) as run:
            with self.assertRaises(m.MaterialsError) as caught:
                m.DWS("dws.exe").call(["profile", "list"])
        output = json.dumps(caught.exception.as_dict())
        self.assertNotIn("SECRET", output)
        self.assertEqual(caught.exception.code, "AUTH_EXPIRED")
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")

    def test_dws_success_false_or_failure_status_without_error_is_rejected(self):
        for payload in ({"success": False, "error": {}}, {"status": "error", "error": {}}, {"status": "failed", "error": {}}):
            with self.subTest(payload=payload), patch.object(m.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), "")):
                with self.assertRaises(m.MaterialsError):
                    m.DWS().call(["profile", "list"])

    def test_global_options_work_before_or_after_subcommand(self):
        for args in (["--scope", "table", "find", "--name", "产品"], ["get", "--record-id", "box", "--scope", "table"]):
            self.assertEqual(m.parser().parse_args(args).scope, "table")


if __name__ == "__main__":
    unittest.main()
