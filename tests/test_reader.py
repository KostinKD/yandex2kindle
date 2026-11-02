import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
from core_client import TestClient
from app import main


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(main, "DB", self.root / "app.db")
        self.patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def create_book(self, title="Война и мир", author="Лев Толстой"):
        book_file = self.root / "test.epub"
        book_file.write_bytes(b"dummy epub")
        with patch("subprocess.run") as mock_calibre:
            mock_calibre.return_value = Mock(returncode=0, stdout="Added book ids: 1\n", stderr="")
            res = main.add_file(book_file, title=title, author=author)
            return res["id"]

    def test_reader_missing_book_returns_404(self):
        response = self.client.get("/reader/99999")
        self.assertEqual(response.status_code, 404)

    def test_reader_success_renders_ui(self):
        book_id = self.create_book("Тестовая книга", "Автор")
        response = self.client.get(f"/reader/{book_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Тестовая книга", response.text)
        self.assertIn("foliate-view", response.text)
        self.assertIn("reader.js", response.text)
        self.assertIn("reader.css", response.text)

    def test_reader_progress_sync(self):
        book_id = self.create_book()
        # Initial progress is 0
        res = self.client.get(f"/api/books/{book_id}/progress")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["progress"], 0.0)

        # Update progress with CFI
        put_res = self.client.put(
            f"/api/books/{book_id}/progress",
            json={"progress": 0.45, "position": "epubcfi(/6/4!/4/2/10)"}
        )
        self.assertEqual(put_res.status_code, 200)
        data = put_res.json()
        self.assertEqual(data["progress"], 0.45)
        self.assertEqual(data["position"], "epubcfi(/6/4!/4/2/10)")

        # Verify persistence
        res2 = self.client.get(f"/api/books/{book_id}/progress")
        self.assertEqual(res2.json()["progress"], 0.45)
        self.assertEqual(res2.json()["position"], "epubcfi(/6/4!/4/2/10)")
