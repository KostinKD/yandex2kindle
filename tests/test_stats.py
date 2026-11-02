import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
from core_client import TestClient
from app import main


class StatsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(main, "DB", self.root / "app.db")
        self.patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def create_book(self, title="1984", author="Джордж Оруэлл"):
        book_file = self.root / "1984.epub"
        book_file.write_bytes(b"dummy")
        with patch("subprocess.run") as mock_calibre:
            mock_calibre.return_value = Mock(returncode=0, stdout="Added book ids: 1\n", stderr="")
            res = main.add_file(book_file, title=title, author=author)
            return res["id"]

    def test_stats_api_and_page(self):
        book_id = self.create_book()

        # Update progress
        self.client.put(
            f"/api/books/{book_id}/progress",
            json={"progress": 0.5, "position": "cfi123"}
        )

        # Verify API
        api_res = self.client.get("/api/stats")
        self.assertEqual(api_res.status_code, 200)
        data = api_res.json()
        self.assertEqual(data["total_books"], 1)
        self.assertEqual(data["reading_books"], 1)
        self.assertEqual(data["finished_books"], 0)
        self.assertGreaterEqual(data["reading_streak_days"], 1)

        # Verify HTML page
        page_res = self.client.get("/stats")
        self.assertEqual(page_res.status_code, 200)
        self.assertIn("Статистика и аналитика чтения", page_res.text)
        self.assertIn("1984", page_res.text)
        self.assertIn("50.0%", page_res.text)

    def test_create_reading_session(self):
        book_id = self.create_book("Книга 2", "Автор")
        res = self.client.post(
            "/api/reading-sessions",
            json={
                "book_id": book_id,
                "duration_seconds": 1200,
                "start_progress": 0.1,
                "end_progress": 0.25
            }
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")

        # Stats reflects session
        api_res = self.client.get("/api/stats")
        self.assertGreaterEqual(api_res.json()["total_sessions"], 1)
