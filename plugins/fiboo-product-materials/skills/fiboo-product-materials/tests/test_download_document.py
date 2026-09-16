from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "download_document.py"
SPEC = importlib.util.spec_from_file_location("download_document", MODULE_PATH)
download = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(download)


class DownloadDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name).resolve()
        self.content = b"%PDF-1.7\nordinary file test\n%%EOF\n"
        self.metadata = {
            "fileId": "real-returned-file-id", "spaceId": "25609365143", "type": "FILE",
            "extension": "pdf", "name": "多重蛋白粉产品概念及资料.pdf",
            "fileSize": len(self.content),
            "md5": base64.b64encode(hashlib.md5(self.content).digest()).decode("ascii"),
            "docUrl": "https://alidocs.dingtalk.com/i/nodes/real-returned-file-id?secret=not-for-output",
            "modifyTime": "2026-09-15T10:00:00+08:00",
        }
        self.commands = []

    def fake_run(self, command, **kwargs):
        self.commands.append((command, kwargs))
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["cwd"], str(self.output))
        self.assertEqual(command[-4:], ["--profile", "confirmed-profile", "--format", "json"])
        if command[2] == "info":
            result = {"success": True, "result": self.metadata}
        else:
            self.assertEqual(command[command.index("--node") + 1], self.metadata["fileId"])
            self.assertEqual(command[command.index("--space-id") + 1], self.metadata["spaceId"])
            relative = Path(command[command.index("--output") + 1])
            self.assertFalse(relative.is_absolute())
            self.assertNotIn("..", relative.parts)
            (self.output / relative).write_bytes(self.content)
            result = {"success": True, "result": {"url": "https://download.example/signed?token=never-print"}}
        return subprocess.CompletedProcess(command, 0, json.dumps(result, ensure_ascii=False), "private stderr")

    def run_download(self):
        with patch.object(download.shutil, "which", return_value="dws.exe"), patch.object(download.subprocess, "run", side_effect=self.fake_run):
            return download.download_document("https://alidocs.dingtalk.com/i/nodes/shared-url-node", "confirmed-profile", self.output)

    def test_pdf_download_verifies_bytes_and_keeps_only_safe_metadata(self):
        result = self.run_download()
        saved = Path(result["file"]["absolute_path"])
        self.assertTrue(saved.is_absolute())
        self.assertEqual(saved.read_bytes(), self.content)
        self.assertEqual(result["file"]["size"], len(self.content))
        self.assertEqual(result["file"]["sha256"], hashlib.sha256(self.content).hexdigest())
        self.assertEqual(result["verification"], {"size": "matched", "md5": "matched"})
        self.assertEqual(result["source"]["node"], self.metadata["fileId"])
        self.assertEqual(result["source"]["modified_at"], self.metadata["modifyTime"])
        self.assertTrue(result["source"]["retrieved_at"].endswith("+00:00"))
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("never-print", json.dumps(result))

    def test_size_mismatch_removes_only_new_download(self):
        existing = self.output / self.metadata["name"]
        existing.write_bytes(b"keep this")
        self.metadata["fileSize"] += 1
        with self.assertRaises(download.DownloadError) as caught:
            self.run_download()
        self.assertEqual(caught.exception.code, "size_mismatch")
        self.assertEqual(existing.read_bytes(), b"keep this")
        self.assertEqual(list(self.output.iterdir()), [existing])

    def test_md5_mismatch_removes_download(self):
        self.metadata["md5"] = "00" * 16
        with self.assertRaises(download.DownloadError) as caught:
            self.run_download()
        self.assertEqual(caught.exception.code, "md5_mismatch")
        self.assertEqual(list(self.output.iterdir()), [])

    def test_folder_and_online_document_are_rejected_before_download(self):
        for node_type, extension in (("FOLDER", ""), ("ADOC", "adoc"), ("FILE", "adoc")):
            with self.subTest(node_type=node_type, extension=extension):
                self.commands.clear()
                self.metadata.update(type=node_type, extension=extension)
                with self.assertRaises(download.DownloadError) as caught:
                    self.run_download()
                self.assertEqual(caught.exception.code, "unsupported_type")
                self.assertEqual(caught.exception.details["node_type"], node_type)
                self.assertEqual(len(self.commands), 1)

    def test_metadata_size_limit_prevents_download(self):
        self.metadata["fileSize"] = download.MAX_BYTES + 1
        with self.assertRaises(download.DownloadError) as caught:
            self.run_download()
        self.assertEqual(caught.exception.code, "size_limit")
        self.assertEqual(len(self.commands), 1)

    def test_actual_size_limit_applies_when_metadata_has_no_size(self):
        self.metadata.pop("fileSize")
        with patch.object(download, "MAX_BYTES", len(self.content) - 1):
            with self.assertRaises(download.DownloadError) as caught:
                self.run_download()
        self.assertEqual(caught.exception.code, "size_limit")
        self.assertEqual(list(self.output.iterdir()), [])

    def test_repeated_download_never_overwrites_previous_file(self):
        first = self.run_download()
        second = self.run_download()
        self.assertNotEqual(first["file"]["absolute_path"], second["file"]["absolute_path"])
        self.assertEqual(Path(first["file"]["absolute_path"]).read_bytes(), self.content)

    def test_dws_executable_environment_override(self):
        with patch.dict(download.os.environ, {"DWS_EXECUTABLE": "configured-dws.exe"}), patch.object(download.shutil, "which", return_value="dws.exe") as find, patch.object(download.subprocess, "run", side_effect=self.fake_run):
            download.download_document("https://alidocs.dingtalk.com/i/nodes/id", "confirmed-profile", self.output)
        find.assert_called_once_with("configured-dws.exe")

    def test_failed_download_removes_partial_bytes_and_hides_cli_output(self):
        def interrupted(command, **kwargs):
            completed = self.fake_run(command, **kwargs)
            if command[2] == "+download":
                return subprocess.CompletedProcess(command, 1, "https://signed.example/?token=secret", "secret credential")
            return completed

        with patch.object(download.shutil, "which", return_value="dws.exe"), patch.object(download.subprocess, "run", side_effect=interrupted):
            with self.assertRaises(download.DownloadError) as caught:
                download.download_document("https://alidocs.dingtalk.com/i/nodes/id", "confirmed-profile", self.output)
        self.assertEqual(caught.exception.code, "dws_failed")
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(list(self.output.iterdir()), [])

    def test_invalid_url_or_relative_output_never_calls_dws(self):
        with patch.object(download.subprocess, "run") as runner:
            for url in ("https://dingtalk.com.evil.example/file", "file:///D:/file.pdf", "https://user:pass@alidocs.dingtalk.com/i/nodes/id"):
                with self.assertRaises(download.DownloadError):
                    download.download_document(url, "confirmed-profile", self.output)
            with self.assertRaises(download.DownloadError):
                download.download_document("https://alidocs.dingtalk.com/i/nodes/id", "confirmed-profile", "relative")
            runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
