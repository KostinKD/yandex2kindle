import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from core_client import TestClient
from app import main


class PwaTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_pwa_manifest(self):
        res = self.client.get("/manifest.webmanifest")
        self.assertEqual(res.status_code, 200)
        self.assertIn("application/manifest+json", res.headers.get("content-type", ""))
        data = res.json()
        self.assertEqual(data["short_name"], "Y2K")
        self.assertEqual(data["display"], "standalone")

    def test_service_worker_root_scope(self):
        res = self.client.get("/sw.js")
        self.assertEqual(res.status_code, 200)
        self.assertIn("application/javascript", res.headers.get("content-type", ""))
        self.assertEqual(res.headers.get("service-worker-allowed"), "/")
        self.assertIn("y2k-cache", res.text)
