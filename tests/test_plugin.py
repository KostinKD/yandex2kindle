import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from core_client import TestClient
from app import main


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_download_koreader_plugin_zip(self):
        response = self.client.get("/plugins/yandex2kindle.koplugin.zip")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/zip", response.headers.get("content-type", ""))
        self.assertIn("attachment", response.headers.get("content-disposition", ""))

        # Verify ZIP contains _meta.lua and main.lua
        zip_buffer = io.BytesIO(response.content)
        with zipfile.ZipFile(zip_buffer, "r") as z:
            names = z.namelist()
            self.assertTrue(any("_meta.lua" in name for name in names))
            self.assertTrue(any("main.lua" in name for name in names))
