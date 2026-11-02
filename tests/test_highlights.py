import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
from core_client import TestClient
from app import main


class HighlightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(main, "DB", self.root / "app.db")
        self.patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def create_book(self, title="Мастер и Маргарита", author="Михаил Булгаков"):
        book_file = self.root / "bulgakov.epub"
        book_file.write_bytes(b"dummy")
        with patch("subprocess.run") as mock_calibre:
            mock_calibre.return_value = Mock(returncode=0, stdout="Added book ids: 1\n", stderr="")
            res = main.add_file(book_file, title=title, author=author)
            return res["id"]

    def test_highlights_crud(self):
        book_id = self.create_book()

        # Initially empty
        res = self.client.get(f"/api/books/{book_id}/highlights")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), [])

        # Create highlight
        quote = "Рукописи не горят."
        create_res = self.client.post(
            f"/api/books/{book_id}/highlights",
            json={
                "text": quote,
                "comment": "Великая цитата Воланда",
                "cfi": "epubcfi(/6/12!/4/2)",
                "progress": 0.62
            }
        )
        self.assertEqual(create_res.status_code, 200)
        hl_data = create_res.json()
        self.assertEqual(hl_data["text"], quote)
        self.assertEqual(hl_data["comment"], "Великая цитата Воланда")
        hl_id = hl_data["id"]

        # Retrieve list
        list_res = self.client.get(f"/api/books/{book_id}/highlights")
        self.assertEqual(list_res.status_code, 200)
        items = list_res.json()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], hl_id)

        # Highlights page
        page_res = self.client.get("/highlights")
        self.assertEqual(page_res.status_code, 200)
        self.assertIn("Рукописи не горят", page_res.text)
        self.assertIn("Мастер и Маргарита", page_res.text)

        # Export to Markdown
        export_res = self.client.get("/highlights/export")
        self.assertEqual(export_res.status_code, 200)
        self.assertIn("text/markdown", export_res.headers.get("content-type", ""))
        self.assertIn("# Мои цитаты и заметки", export_res.text)
        self.assertIn("> Рукописи не горят.", export_res.text)
        self.assertIn("Мастер и Маргарита", export_res.text)

        # Delete highlight
        del_res = self.client.delete(f"/api/highlights/{hl_id}")
        self.assertEqual(del_res.status_code, 200)

        # Verify deletion
        list_after = self.client.get(f"/api/books/{book_id}/highlights")
        self.assertEqual(list_after.json(), [])
