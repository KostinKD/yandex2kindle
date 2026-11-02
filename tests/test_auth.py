import hashlib
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient
from app import main, auth


class PrivateLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(main, name, self.root / path) for name, path in
                        (("DB", "app.db"), ("BASE", "calibre"), ("INCOMING", "incoming"))]
        for item in self.patches:
            item.start()
        auth.create_account("owner", "owner-password-123", owner=True)
        auth.create_account("alice", "alice-password-123")
        auth.create_account("bob", "bob-password-123")
        self.owner = self.login("owner")
        self.alice = self.login("alice")
        self.bob = self.login("bob")

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def login(self, name):
        client = TestClient(main.app)
        result = client.post("/login", data={"username": name, "password": name+"-password-123"}, follow_redirects=False)
        self.assertEqual(result.status_code, 303)
        self.assertIn("HttpOnly", result.headers["set-cookie"])
        return client

    def seed_book(self, username, title):
        with auth.accounts() as db:
            user = dict(db.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone())
        token = auth.current_user.set(user)
        try:
            filename = main.library_db().parent / "book.epub"
            filename.parent.mkdir(parents=True, exist_ok=True)
            filename.write_bytes(title.encode())
            with main.connect() as db:
                db.execute("INSERT INTO books(title,author,filename,sha256,created_at) VALUES(?,?,?,?,?)",
                           (title, username, str(filename), hashlib.sha256(title.encode()).hexdigest(), "2026-01-01T00:00:00Z"))
        finally:
            auth.current_user.reset(token)

    def test_anonymous_and_logout(self):
        client = TestClient(main.app)
        for path in ("/api/books", "/opds", "/opds/search.xml", "/download/1", "/download/1/epub", "/docs", "/openapi.json"):
            response = client.get(path)
            self.assertEqual(response.status_code, 401, path)
        self.alice.post("/logout")
        self.assertEqual(self.alice.get("/api/books").status_code, 401)

    def test_quote_exchange_is_scoped_to_device_account(self):
        from app import kosync
        self.seed_book('owner', 'Owner quote book')
        document = kosync.partial_md5(main.DB.parent / 'book.epub')
        payload = {'document': document, 'annotations': [{'key': 'one', 'text': 'Private quote'}]}
        headers = {'x-auth-user': 'alice', 'x-auth-key': hashlib.md5(b'alice-password-123').hexdigest()}
        self.assertEqual(self.alice.post('/syncs/highlights', headers=headers, json=payload).status_code, 404)
        self.seed_book('alice', 'Alice quote book')
        with auth.accounts() as db:
            alice_id = db.execute("SELECT id FROM accounts WHERE username='alice'").fetchone()[0]
        payload['document'] = kosync.partial_md5(main.DB.parent / 'users' / str(alice_id) / 'book.epub')
        self.assertEqual(self.alice.post('/syncs/highlights', headers=headers, json=payload).status_code, 200)
        self.assertEqual(self.alice.get('/api/books/1/highlights').json()[0]['text'], 'Private quote')
        self.assertEqual(self.owner.get('/api/books/1/highlights').json(), [])

    def test_books_wishlist_progress_and_download_isolation(self):
        self.seed_book("owner", "Old library")
        self.seed_book("alice", "Alice only")
        self.assertEqual(self.bob.get("/api/books").json(), [])
        self.assertEqual(self.owner.get("/api/books").json()[0]["title"], "Old library")
        self.assertEqual(self.alice.get("/api/books").json()[0]["title"], "Alice only")
        self.assertEqual(self.bob.get("/download/1").status_code, 404)
        self.assertEqual(self.bob.get("/download/1/epub").status_code, 404)
        self.assertEqual(self.bob.put("/api/books/1/progress", json={"progress": 0.8}).status_code, 404)
        self.assertEqual(self.bob.get('/books/1').status_code, 404)
        self.assertEqual(self.bob.patch('/api/books/1', json={'title': 'Stolen'}).status_code, 404)
        self.assertEqual(self.alice.patch('/api/books/1', json={'title': 'My edit'}).status_code, 200)
        self.assertEqual(self.owner.get('/api/books').json()[0]['title'], 'Old library')
        self.alice.post("/wishlist", data={"title": "Private wish"})
        self.assertEqual(self.bob.get("/api/wishlist").json(), [])
        self.assertEqual(self.bob.delete("/api/wishlist/1").status_code, 404)

    def test_reader_basic_auth_and_opds(self):
        self.seed_book("alice", "Private EPUB")
        client = TestClient(main.app)
        credentials = ("alice", "alice-password-123")
        response = client.get("/opds", auth=credentials)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Private EPUB", response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(client.get("/download/1", auth=credentials).content, b"Private EPUB")
        self.assertEqual(client.get("/download/1", auth=("bob", "bob-password-123")).status_code, 404)
        self.assertEqual(client.get("/opds", auth=("alice", "wrong")).status_code, 401)
        self.assertEqual(client.post("/wishlist", auth=credentials, data={"title": "No"}).status_code, 403)

    def test_account_admin_and_csrf(self):
        self.assertEqual(self.alice.post("/accounts", data={"username": "eve", "password": "long-password-123"}).status_code, 403)
        self.assertEqual(self.owner.post("/accounts", data={"username": "eve", "password": "long-password-123"}, follow_redirects=False).status_code, 303)
        self.assertEqual(self.alice.post("/wishlist", data={"title": "No"}, headers={"origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.alice.get("/api/integrations").status_code, 403)
        self.assertEqual(self.alice.post("/integrations/yandex/sync").status_code, 403)

    def test_kosync_uses_personal_library(self):
        self.seed_book("alice", "Alice EPUB")
        self.seed_book("bob", "Bob EPUB")
        client = TestClient(main.app)
        headers = {"x-auth-user": "alice", "x-auth-key": hashlib.md5(b"alice-password-123").hexdigest()}
        self.assertEqual(client.get("/users/auth", headers=headers).status_code, 200)
        response = client.put("/syncs/progress", headers=headers, json={"document": hashlib.md5(b"Alice EPUB").hexdigest(), "progress": "chapter1", "percentage": 0.4, "device": "Kindle"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.alice.get("/api/books/1/progress").json()["progress"], 0.4)
        self.assertEqual(self.bob.get("/api/books/1/progress").json()["progress"], 0)

    def test_login_rate_limit(self):
        client = TestClient(main.app)
        for _ in range(10):
            self.assertEqual(client.post("/login", data={"username": "alice", "password": "bad"}).status_code, 401)
        self.assertEqual(client.post("/login", data={"username": "alice", "password": "bad"}).status_code, 429)

    def test_owner_recovery_preserves_library_and_invalidates_sessions(self):
        self.seed_book("owner", "Keep this book")
        with main.connect() as db:
            original_key = db.execute("SELECT key_hash FROM kosync_users WHERE username='reader'").fetchone()[0]
        self.assertEqual(auth.reset_owner("replacement-password-123"), "owner")
        self.assertIsNone(auth.authenticate("owner", "owner-password-123"))
        self.assertIsNotNone(auth.authenticate("owner", "replacement-password-123"))
        self.assertEqual(self.owner.get("/api/books").status_code, 401)
        with main.connect() as db:
            self.assertEqual(db.execute("SELECT title FROM books").fetchone()[0], "Keep this book")
            self.assertEqual(db.execute("SELECT key_hash FROM kosync_users WHERE username='reader'").fetchone()[0], original_key)

    def test_kindle_browser_login_search_and_isolation(self):
        self.seed_book("alice", "Private Kindle book")
        client = TestClient(main.app)
        response = client.get("/kindle")
        self.assertEqual(response.url.path, "/kindle/login")
        self.assertNotIn("app.js", response.text)
        self.assertNotIn("app.css", response.text)
        wrong = client.post("/login", data={"username": "alice", "password": "wrong", "next_path": "/kindle"})
        self.assertEqual(wrong.status_code, 401)
        self.assertNotIn("app.js", wrong.text)
        response = client.post("/login", data={"username": "alice", "password": "alice-password-123", "next_path": "/kindle"})
        self.assertEqual(response.url.path, "/kindle")
        self.assertIn("Private Kindle book", response.text)
        self.assertNotIn("Private Kindle book", self.bob.get("/kindle").text)
        self.assertNotIn("app.js", response.text)
        self.assertNotIn("<img", response.text)
        self.assertIn("/download/1", response.text)
        self.assertNotIn("Private Kindle book", client.get("/kindle?q=missing").text)
        self.assertIn("&lt;script&gt;", client.get("/kindle", params={"q": "<script>"}).text)
        self.assertEqual(client.get("/kindle?page_number=0").status_code, 422)
        response = client.post("/logout", data={"next_path": "/kindle/login"})
        self.assertEqual(response.url.path, "/kindle/login")

    def test_kindle_login_does_not_allow_external_redirect(self):
        client = TestClient(main.app)
        response = client.post("/login", data={"username": "alice", "password": "alice-password-123", "next_path": "https://evil.example"}, follow_redirects=False)
        self.assertEqual(response.headers["location"], "/")

    def test_upload_storage_and_deduplication_are_personal(self):
        result = subprocess.CompletedProcess([], 0, "Added book ids: 1", "")
        with patch("app.main.subprocess.run", return_value=result) as calibre:
            for client in (self.alice, self.bob, self.alice):
                response = client.post("/upload", files={"file": ("same.epub", b"same private contents")}, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
            self.assertEqual(calibre.call_count, 2)
            paths = [call.args[0][3] for call in calibre.call_args_list]
            self.assertNotEqual(paths[0], paths[1])
        self.assertEqual(len(self.alice.get("/api/books").json()), 1)
        self.assertEqual(len(self.bob.get("/api/books").json()), 1)
        self.assertEqual(self.owner.get("/api/books").json(), [])
        with main.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM books").fetchone()[0], 0)
