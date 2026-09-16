import base64
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from download_image import MaterialsError, check_url, save_attachment

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8r0AAAAASUVORK5CYII=")


class Response(io.BytesIO):
    headers = {}


class Opener:
    def __init__(self, body=PNG):
        self.body = body

    def open(self, request, timeout):
        assert "Authorization" not in request.headers
        return Response(self.body)


class DownloadImageTests(unittest.TestCase):
    def attachment(self):
        return {"url": "https://example.oss-cn-hangzhou.aliyuncs.com/image?Signature=SECRET",
                "filename": "wrong.jpg", "size": len(PNG), "resourceId": "resource"}

    def test_real_type_unique_paths_and_secret_not_returned(self):
        with tempfile.TemporaryDirectory() as folder:
            first = save_attachment(self.attachment(), folder, Opener())
            second = save_attachment(self.attachment(), folder, Opener())
            self.assertNotEqual(first["absolute_path"], second["absolute_path"])
            self.assertEqual(Path(first["absolute_path"]).suffix, ".png")
            self.assertEqual(Path(first["absolute_path"]).read_bytes(), PNG)
            self.assertNotIn("SECRET", str(first))

    def test_mismatch_cleans_new_file(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(MaterialsError):
                save_attachment(self.attachment(), folder, Opener(b"error"))
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_non_image_error_page_rejected(self):
        attachment = self.attachment()
        attachment["size"] = 5
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(MaterialsError):
                save_attachment(attachment, folder, Opener(b"error"))

    def test_storage_boundary(self):
        for url in ("http://example.aliyuncs.com/a", "https://example.aliyuncs.com.attacker.test/a",
                    "https://127.0.0.1/a", "https://name:password@example.aliyuncs.com/a"):
            with self.assertRaises(MaterialsError):
                check_url(url)


if __name__ == "__main__":
    unittest.main()
