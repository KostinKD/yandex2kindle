import hashlib
import sqlite3
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient
from app import auth, main


def statistics_blob(root, duration=120, title="История чтения", book_id=7, extra=False):
    path = root / "fixture.sqlite3"
    path.unlink(missing_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE book(id INTEGER PRIMARY KEY, title TEXT, authors TEXT,
              md5 TEXT, pages INTEGER, total_read_time INTEGER, total_read_pages INTEGER);
            CREATE TABLE page_stat_data(id_book INTEGER, page INTEGER, start_time INTEGER,
              duration INTEGER, total_pages INTEGER, UNIQUE(id_book,page,start_time));
            CREATE VIEW page_stat AS SELECT * FROM page_stat_data;
        ''')
        db.execute("INSERT INTO book VALUES(?,?,?,?,?,?,?)",
                   (book_id, title, "Автор", "a" * 32, 100, duration, 1))
        # 23:59 Moscow: split 120 seconds into 60 on each date.
        stamp = int(datetime(2026, 10, 31, 20, 59, tzinfo=timezone.utc).timestamp())
        db.execute("INSERT INTO page_stat_data VALUES(?,?,?,?,?)", (book_id, 10, stamp, duration, 100))
        if extra:
            db.execute("INSERT INTO page_stat_data VALUES(?,?,?,?,?)", (book_id, 11, stamp + 300, 60, 100))
    return path.read_bytes()


class KOReaderStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(main, "DB", self.root / "app.db"),
                        patch.dict("os.environ", {"READING_TIMEZONE": "Europe/Moscow"})]
        for item in self.patches:
            item.start()
        auth.create_account("owner", "owner-password-123", owner=True)
        auth.create_account("alice", "alice-password-123")
        self.client = TestClient(main.app)
        self.client.post("/login", data={"username": "owner", "password": "owner-password-123"})
        self.dav = TestClient(main.app)
        self.credentials = ("owner", "owner-password-123")

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def upload(self, content):
        return self.client.post("/api/stats/koreader/import",
                                files={"file": ("statistics.sqlite3", content, "application/octet-stream")})

    def test_import_history_without_library_book_and_split_midnight(self):
        result = self.upload(statistics_blob(self.root))
        self.assertEqual(result.status_code, 200, result.text)
        data = self.client.get("/api/stats").json()
        self.assertEqual(data["koreader_reading_seconds"], 120)
        self.assertEqual(data["koreader_books"], 1)
        self.assertEqual(data["total_reading_minutes"], 2)
        self.assertEqual(data["total_sessions"], 1)
        self.assertEqual(data["daily_activity"], [
            {"date": "2026-10-31", "koreader_seconds": 60, "estimated_seconds": 0},
            {"date": "2026-11-01", "koreader_seconds": 60, "estimated_seconds": 0}])
        self.assertEqual(data["book_statistics"][0]["book_id"], None)
        page = self.client.get("/stats")
        self.assertIn("История чтения", page.text)
        self.assertIn("/webdav/", page.text)

    def test_reimport_and_renumbered_source_ids_do_not_duplicate_history(self):
        self.assertEqual(self.upload(statistics_blob(self.root)).status_code, 200)
        self.assertEqual(self.upload(statistics_blob(self.root, book_id=99)).status_code, 200)
        self.assertEqual(self.client.get("/api/stats").json()["koreader_reading_seconds"], 120)
        self.upload(statistics_blob(self.root, duration=180, book_id=99, extra=True))
        self.assertEqual(self.client.get("/api/stats").json()["koreader_reading_seconds"], 240)
        self.upload(statistics_blob(self.root, duration=60))
        self.assertEqual(self.client.get("/api/stats").json()["koreader_reading_seconds"], 240)

    def test_match_unique_metadata_but_do_not_overwrite_progress(self):
        with main.connect() as db:
            db.execute("INSERT INTO books(title,author,filename,sha256,created_at) VALUES(?,?,?,?,?)",
                       ("История чтения", "Автор", "/missing", "x", "2026-01-01"))
            db.execute("INSERT INTO reading_progress VALUES(1,0.8,'chapter5','2026-11-01')")
        self.upload(statistics_blob(self.root))
        self.assertEqual(self.client.get("/api/stats").json()["book_statistics"][0]["book_id"], 1)
        self.assertEqual(self.client.get("/api/books/1/progress").json()["progress"], 0.8)

    def test_invalid_upload_preserves_previous_snapshot_and_history(self):
        content = statistics_blob(self.root)
        self.upload(content)
        for bad in (b"not sqlite", b"", statistics_blob(self.root, duration=-1)):
            response = self.upload(bad)
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.dav.get("/webdav/statistics.sqlite3", auth=self.credentials).content, content)
        self.assertEqual(self.client.get("/api/stats").json()["koreader_reading_seconds"], 120)

    def test_source_views_are_rejected(self):
        path = self.root / "bad.sqlite3"
        with sqlite3.connect(path) as db:
            db.executescript("CREATE VIEW book AS SELECT 1 AS id; CREATE VIEW page_stat_data AS SELECT 1;")
        self.assertEqual(self.upload(path.read_bytes()).status_code, 422)

    def test_generated_source_columns_are_rejected_before_reading(self):
        content = statistics_blob(self.root)
        path = self.root / "fixture.sqlite3"
        with sqlite3.connect(path) as db:
            db.executescript('''
                ALTER TABLE book RENAME TO original_book;
                CREATE TABLE book(id INTEGER PRIMARY KEY, title TEXT GENERATED ALWAYS AS (printf('%s', 'generated')) VIRTUAL,
                                  authors TEXT, md5 TEXT);
                INSERT INTO book(id,authors,md5) SELECT id,authors,md5 FROM original_book;
            ''')
        self.assertEqual(self.upload(path.read_bytes()).status_code, 422)

    def test_timestamps_outside_supported_calendar_are_rejected(self):
        statistics_blob(self.root)
        path = self.root / "fixture.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("UPDATE page_stat_data SET start_time=253402214400")
        self.assertEqual(self.upload(path.read_bytes()).status_code, 422)

    def test_estimated_web_time_preserves_seconds_across_midnight(self):
        with main.connect() as db:
            db.execute("INSERT INTO books(title,author,filename,sha256,created_at) VALUES(?,?,?,?,?)",
                       ("Веб-книга", "Автор", "/missing", "x", "2026-01-01"))
            db.execute("INSERT INTO reading_sessions(book_id,duration_seconds,created_at) VALUES(1,60,?)",
                       ("2026-10-31T21:00:30.500000+00:00",))
        data = self.client.get("/api/stats").json()
        self.assertEqual(data["estimated_reading_seconds"], 60)
        self.assertEqual(data["daily_activity"], [
            {"date": "2026-10-31", "koreader_seconds": 0, "estimated_seconds": 30},
            {"date": "2026-11-01", "koreader_seconds": 0, "estimated_seconds": 30}])

    def test_binary_match_takes_precedence_over_metadata_and_ambiguous_titles(self):
        with main.connect() as db:
            for title, sha in (("История чтения", "x"), ("История чтения", "y"), ("Другое издание", "z")):
                db.execute("INSERT INTO books(title,author,filename,sha256,created_at) VALUES(?,?,?,?,?)",
                           (title, "Автор", "/missing", sha, "2026-01-01"))
        self.upload(statistics_blob(self.root))
        self.assertIsNone(self.client.get("/api/stats").json()["book_statistics"][0]["book_id"])
        with main.connect() as db:
            db.execute("INSERT INTO book_documents VALUES(3,'binary',?)", ("a" * 32,))
        self.upload(statistics_blob(self.root))
        self.assertEqual(self.client.get("/api/stats").json()["book_statistics"][0]["book_id"], 3)

    def test_concurrent_conditional_writes_only_one_succeeds(self):
        from concurrent.futures import ThreadPoolExecutor
        file = "/webdav/statistics.sqlite3"
        self.upload(statistics_blob(self.root))
        etag = self.dav.get(file, auth=self.credentials).headers["etag"]
        contents = [statistics_blob(self.root, duration=180), statistics_blob(self.root, duration=240)]
        # TestClient without a context manager does not start lifespan.
        def send(content):
            client = TestClient(main.app)
            try:
                return client.put(file, content=content, auth=self.credentials, headers={"If-Match": etag})
            finally:
                client.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, contents))
        self.assertEqual(sorted(result.status_code for result in results), [204, 412])
        snapshot = self.dav.get(file, auth=self.credentials).content
        expected = 180 if snapshot == contents[0] else 240
        self.assertEqual(self.client.get("/api/stats").json()["koreader_reading_seconds"], expected)

    def test_invalid_webdav_put_is_atomic_and_upload_requires_website_session(self):
        content = statistics_blob(self.root)
        self.upload(content)
        file = "/webdav/statistics.sqlite3"
        response = self.dav.put(file, auth=self.credentials, content=b"invalid")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.dav.get(file, auth=self.credentials).content, content)
        self.assertEqual(self.dav.post("/api/stats/koreader/import", auth=self.credentials,
                                      files={"file": ("statistics.sqlite3", content)}).status_code, 403)

    def test_html_form_redirect_and_untrusted_titles_are_escaped(self):
        response = self.client.post("/api/stats/koreader/import", headers={"Accept": "text/html"},
                                    files={"file": ("statistics.sqlite3", statistics_blob(self.root, title="<script>alert(1)</script>"))},
                                    follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        page = self.client.get(response.headers["location"]).text
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("<script>alert(1)</script>", page)

    def test_webdav_get_put_etags_and_listing(self):
        root = "/webdav/"
        file = root + "statistics.sqlite3"
        self.assertEqual(self.dav.get(file, auth=self.credentials).status_code, 404)
        self.assertEqual(self.dav.options(root, auth=self.credentials).status_code, 200)
        listing = self.dav.request("PROPFIND", root, auth=self.credentials, headers={"Depth": "1"})
        self.assertEqual(listing.status_code, 207)
        self.assertEqual(len(ET.fromstring(listing.content).findall("{DAV:}response")), 1)
        content = statistics_blob(self.root)
        response = self.dav.put(file, auth=self.credentials, content=content, headers={"If-None-Match": "*"})
        self.assertEqual(response.status_code, 201, response.text)
        first = self.dav.get(file, auth=self.credentials)
        self.assertEqual(first.content, content)
        etag = first.headers["etag"]
        head = self.dav.head(file, auth=self.credentials)
        self.assertEqual(head.headers["etag"], etag)
        self.assertEqual(int(head.headers["content-length"]), len(content))
        self.assertEqual(head.content, b"")
        newer = statistics_blob(self.root, duration=180)
        self.assertEqual(self.dav.put(file, auth=self.credentials, content=newer,
                                     headers={"If-Match": etag}).status_code, 204)
        self.assertEqual(self.dav.put(file, auth=self.credentials, content=content,
                                     headers={"If-Match": etag}).status_code, 412)
        self.assertEqual(self.dav.put(file, auth=self.credentials, content=content,
                                     headers={"If-None-Match": "*"}).status_code, 412)
        listing = self.dav.request("PROPFIND", root, auth=self.credentials, headers={"Depth": "1"})
        self.assertEqual(len(ET.fromstring(listing.content).findall("{DAV:}response")), 2)
        self.assertEqual(self.dav.delete(file, auth=self.credentials).status_code, 204)
        self.assertEqual(self.dav.get(file, auth=self.credentials).status_code, 404)
        self.assertEqual(self.client.get("/api/stats").json()["koreader_reading_seconds"], 180)

    def test_authentication_isolation_and_basic_write_scope(self):
        content = statistics_blob(self.root)
        file = "/webdav/statistics.sqlite3"
        self.assertEqual(self.dav.put(file, content=content).status_code, 401)
        self.assertEqual(self.dav.put(file, content=content, auth=("owner", "wrong")).status_code, 401)
        self.assertEqual(self.dav.put(file, content=content, auth=self.credentials).status_code, 201)
        self.assertEqual(self.dav.get(file, auth=("alice", "alice-password-123")).status_code, 404)
        alice = TestClient(main.app)
        alice.post("/login", data={"username": "alice", "password": "alice-password-123"})
        self.assertEqual(alice.get("/api/stats").json()["koreader_reading_seconds"], 0)
        self.assertEqual(self.dav.post("/wishlist", auth=self.credentials, data={"title": "Forbidden"}).status_code, 403)
        self.assertEqual(self.dav.put(file, content=content, auth=self.credentials,
                                     headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.dav.put("/webdav/other.sqlite3", content=content, auth=self.credentials).status_code, 404)
        self.assertEqual(self.dav.put("/webdav/nested/statistics.sqlite3", content=content,
                                     auth=self.credentials).status_code, 404)

    def test_upload_size_limit(self):
        with patch("app.koreader_stats.MAX_UPLOAD", 100):
            self.assertEqual(self.upload(b"x" * 101).status_code, 413)
            self.assertEqual(self.dav.put("/webdav/statistics.sqlite3", auth=self.credentials,
                                         content=b"x" * 101).status_code, 413)
