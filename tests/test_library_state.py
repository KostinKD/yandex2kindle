import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core_client import TestClient
from app import integrations, main


class LibraryStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(main, "DB", Path(self.temp.name) / "app.db")
        self.patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def book(self, title="Преступление и наказание", author="Достоевский Фёдор Михайлович", content=b"book"):
        with main.connect() as db:
            index = db.execute("SELECT COUNT(*) FROM books").fetchone()[0] + 1
            path = Path(self.temp.name) / f"{index}.fb2"
            path.write_bytes(content)
            return db.execute("INSERT INTO books(title,author,filename,sha256,created_at) VALUES(?,?,?,?,?)",
                              (title, author, str(path), str(index), "2026-01-01T00:00:00Z")).lastrowid

    def wish(self, title="Преступление и наказание", author="Федор Достоевский"):
        self.client.post("/wishlist", data={"title": title, "author": author})
        return self.client.get("/api/wishlist").json()[0]

    def test_existing_file_is_linked_with_reordered_author_and_patronymic(self):
        book_id = self.book()
        row = self.wish()
        self.assertEqual((row["book_id"], row["status"], row["link_method"]), (book_id, "in_library", "automatic"))
        self.assertEqual(self.client.post("/api/library/reconcile").json(), {"linked": 0})

    def test_different_author_and_missing_author_do_not_auto_match(self):
        self.book("Дюна", "Фрэнк Герберт")
        self.assertIsNone(self.wish("Дюна", "Стивен Кинг")["book_id"])
        self.assertIsNone(self.wish("Дюна", "")["book_id"])

    def test_ambiguous_editions_require_manual_selection(self):
        self.book()
        second = self.book()
        wish = self.wish()
        self.assertEqual(wish["status"], "wanted")
        response = self.client.put(f'/api/wishlist/{wish["id"]}/book', json={"book_id": second})
        self.assertEqual(response.status_code, 200)
        self.client.post("/api/library/reconcile")
        row = self.client.get("/api/wishlist").json()[0]
        self.assertEqual((row["book_id"], row["link_method"]), (second, "manual"))

    def test_device_confirmation_is_explicit_and_download_does_not_set_it(self):
        book_id = self.book()
        self.wish()
        self.assertEqual(self.client.get(f"/download/{book_id}").status_code, 200)
        row = self.client.get("/api/wishlist").json()[0]
        self.assertIsNone(row["kindle_confirmed_at"])
        result = self.client.put(f"/api/books/{book_id}/kindle", json={"on_device": True}).json()
        self.assertFalse(result["delivery_verified"])
        row = self.client.get("/api/wishlist").json()[0]
        self.assertEqual(row["status"], "on_kindle")
        self.assertIn("вручную", row["status_label"])
        self.client.put(f"/api/books/{book_id}/kindle", json={"on_device": False})
        self.assertEqual(self.client.get("/api/wishlist").json()[0]["status"], "in_library")

    def test_progress_updates_status_without_erasing_device_confirmation(self):
        book_id = self.book()
        self.wish()
        self.client.put(f"/api/books/{book_id}/kindle", json={"on_device": True})
        self.client.put(f"/api/books/{book_id}/progress", json={"progress": .4})
        row = self.client.get("/api/wishlist").json()[0]
        self.assertEqual(row["status"], "reading")
        self.assertIsNotNone(row["kindle_confirmed_at"])
        self.client.put(f"/api/books/{book_id}/progress", json={"progress": 1})
        self.assertEqual(self.client.get("/api/wishlist").json()[0]["status"], "finished")

    def test_yandex_resync_preserves_local_status_and_manual_binding(self):
        book_id = self.book()
        remote = {"external_id": "uuid-1", "title": "Преступление и наказание", "author": "Федор Достоевский", "state": "pending"}
        with patch.object(integrations, "fetch_yandex_library", return_value=("yandex:me", [remote])):
            result = self.client.post("/api/integrations/yandex/sync").json()
            self.assertEqual(result["linked"], 1)
            wish_id = self.client.get("/api/wishlist").json()[0]["id"]
            self.client.put(f"/api/wishlist/{wish_id}/book", json={"book_id": book_id})
            self.client.put(f"/api/books/{book_id}/kindle", json={"on_device": True})
            self.client.post("/api/integrations/yandex/sync")
        row = self.client.get("/api/wishlist").json()[0]
        self.assertEqual((row["status"], row["link_method"], row["yandex_states"]), ("on_kindle", "manual", ["pending"]))
        self.assertEqual(len(self.client.get("/api/wishlist").json()), 1)

    def test_bad_link_or_confirmation_returns_not_found(self):
        book_id = self.book()
        wish = self.wish()
        self.assertEqual(self.client.put("/api/wishlist/999/book", json={"book_id": book_id}).status_code, 404)
        self.assertEqual(self.client.put(f'/api/wishlist/{wish["id"]}/book', json={"book_id": 999}).status_code, 404)
        self.assertEqual(self.client.put("/api/books/999/kindle", json={"on_device": True}).status_code, 404)

    def test_unlink_is_respected_by_future_reconciliation(self):
        book_id = self.book()
        wish = self.wish()
        self.client.delete(f'/api/wishlist/{wish["id"]}/book')
        self.client.post("/api/library/reconcile")
        self.assertIsNone(self.client.get("/api/wishlist").json()[0]["book_id"])
        self.client.put(f'/api/wishlist/{wish["id"]}/book', json={"book_id": book_id})
        self.assertEqual(self.client.get("/api/wishlist").json()[0]["book_id"], book_id)

    def test_status_filter_handles_books_that_are_both_reading_and_on_kindle(self):
        book_id = self.book()
        self.wish()
        self.client.put(f"/api/books/{book_id}/kindle", json={"on_device": True})
        self.client.put(f"/api/books/{book_id}/progress", json={"progress": .4})
        page = self.client.get("/?wish_status=on_kindle")
        self.assertIn('class="wish-row"', page.text)
        self.assertNotIn('class="wish-row"', self.client.get("/?wish_status=wanted").text)
        self.assertEqual(self.client.get("/?wish_status=unknown").status_code, 422)

    def test_explicit_import_links_selected_wish_even_when_names_differ(self):
        book_id = self.book()
        wish = self.wish("Преступление и наказание: другое издание", "Ф. Достоевский")
        with main.connect() as db:
            db.execute("INSERT INTO remote_candidates VALUES(?,?,?,?,?,?,?,?)",
                       ("candidate", "flibusta", "book", "Преступление и наказание", "Достоевский", "https://catalog.test/book", ".epub", 9999999999))
        def fetch(url, source, target=None, **kwargs):
            target.write(b"book file")
            return b"", url
        with patch.object(integrations, "fetch_remote", side_effect=fetch), \
             patch.object(main, "add_file", return_value={"status": "duplicate", "id": book_id}):
            result = self.client.post("/api/sources/import", json={"candidate_id": "candidate", "wish_id": wish["id"]})
        self.assertEqual(result.status_code, 200, result.text)
        row = self.client.get("/api/wishlist").json()[0]
        self.assertEqual((row["book_id"], row["link_method"]), (book_id, "manual"))

    def test_new_metadata_invalidates_only_automatic_binding(self):
        self.book()
        wish = self.wish()
        self.client.patch(f'/api/wishlist/{wish["id"]}', json={"title": "Другая книга", "author": "Другой автор"})
        self.assertIsNone(self.client.get("/api/wishlist").json()[0]["book_id"])

    def test_home_renders_saved_state_and_import_context(self):
        book_id = self.book()
        wish = self.wish()
        self.client.put(f"/api/books/{book_id}/kindle", json={"on_device": True})
        page = self.client.get("/wishlist")
        self.assertEqual(page.status_code, 200)
        self.assertIn("На Kindle · отмечено вручную", page.text)
        self.assertIn("Подтвердить связь", page.text)
