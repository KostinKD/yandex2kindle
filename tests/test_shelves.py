import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
from core_client import TestClient
from app import main


class ShelvesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(main, "DB", self.root / "app.db")
        self.patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def create_book(self, title="Марсианин", author="Энди Вейер"):
        book_file = self.root / "martian.epub"
        book_file.write_bytes(b"dummy")
        with patch("subprocess.run") as mock_calibre:
            mock_calibre.return_value = Mock(returncode=0, stdout="Added book ids: 1\n", stderr="")
            res = main.add_file(book_file, title=title, author=author)
            return res["id"]

    def test_book_rating_and_review(self):
        book_id = self.create_book()
        
        # Initial rating
        r0 = self.client.get(f"/api/books/{book_id}/rating")
        self.assertEqual(r0.status_code, 200)
        self.assertEqual(r0.json()["rating"], 0)

        # Set rating & review
        set_res = self.client.post(
            f"/api/books/{book_id}/rating",
            json={"rating": 5, "review": "Шикарная научная фантастика!"}
        )
        self.assertEqual(set_res.status_code, 200)
        self.assertEqual(set_res.json()["rating"], 5)
        self.assertEqual(set_res.json()["review"], "Шикарная научная фантастика!")

        # Verify in home HTML
        home = self.client.get("/")
        self.assertIn("★★★★★", home.text)

    def test_shelves_crud_and_filtering(self):
        book_id = self.create_book("Книга 1")
        book_id_2 = self.create_book("Книга 2")

        # Create shelf
        c_res = self.client.post("/api/shelves", json={"name": "Любимое", "color": "#f59e0b"})
        self.assertEqual(c_res.status_code, 200)
        shelf_id = c_res.json()["id"]

        # Link book 1
        link_res = self.client.post(f"/api/shelves/{shelf_id}/books/{book_id}")
        self.assertEqual(link_res.status_code, 200)

        # List shelves
        list_res = self.client.get("/api/shelves")
        self.assertEqual(list_res.status_code, 200)
        data = list_res.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "Любимое")
        self.assertEqual(data[0]["book_count"], 1)

        # Filter by shelf
        home_shelf = self.client.get(f"/?shelf={shelf_id}")
        self.assertEqual(home_shelf.status_code, 200)
        self.assertIn("Книга 1", home_shelf.text)
        self.assertNotIn("Книга 2", home_shelf.text)

        # Unlink
        del_link = self.client.delete(f"/api/shelves/{shelf_id}/books/{book_id}")
        self.assertEqual(del_link.status_code, 200)

        # Delete shelf
        del_shelf = self.client.delete(f"/api/shelves/{shelf_id}")
        self.assertEqual(del_shelf.status_code, 200)
        self.assertEqual(self.client.get("/api/shelves").json(), [])
