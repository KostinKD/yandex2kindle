import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from core_client import TestClient
from app import main


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(main, name, self.root / path) for name, path in
                        (("DB", "app.db"), ("BASE", "calibre"), ("INCOMING", "incoming"))]
        for item in self.patches:
            item.start()
        self.client = TestClient(main.app)
        with main.connect() as db:
            for title, author in (("Война и мир", "Лев Толстой"), ("Анна Каренина", "Лев Толстой"), ("Дюна", "Фрэнк Герберт")):
                db.execute("INSERT INTO books(title,author,filename,sha256,created_at) VALUES(?,?,?,?,?)",
                           (title, author, str(self.root / f"{title}.epub"), title, "2026-01-01T00:00:00+00:00"))

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_search_unicode_and_pagination(self):
        found = self.client.get("/api/books", params={"q": "ТОЛСТОЙ", "limit": 1}).json()
        self.assertEqual([b["title"] for b in found], ["Анна Каренина"])
        found = self.client.get("/api/books", params={"q": "ТОЛСТОЙ", "limit": 1, "offset": 1}).json()
        self.assertEqual(found[0]["title"], "Война и мир")
        self.assertEqual(self.client.get("/api/books?q=%25").json(), [])
        for url in ("/api/books?limit=0", "/opds?offset=-1", "/?page=0"):
            self.assertEqual(self.client.get(url).status_code, 422)

    def test_opds_navigation_and_search(self):
        root = ET.fromstring(self.client.get("/opds?q=ТОЛСТОЙ&limit=1").content)
        ns = {"a": main.ATOM}
        self.assertEqual(len(root.findall("a:entry", ns)), 1)
        links = {link.attrib["rel"]: link.attrib["href"] for link in root.findall("a:link", ns)}
        next_root = ET.fromstring(self.client.get(links["next"]).content)
        self.assertEqual(next_root.find("a:entry/a:title", ns).text, "Война и мир")
        self.assertNotIn("next", {link.attrib["rel"] for link in next_root.findall("a:link", ns)})
        search = self.client.get(links["search"])
        self.assertEqual(search.status_code, 200)
        self.assertIn("{searchTerms}", search.text)

    def test_wishlist_edit_delete_and_conflict(self):
        self.client.post("/wishlist", data={"title": "Первая"})
        self.client.post("/wishlist", data={"title": "Вторая"})
        self.assertEqual(self.client.post("/wishlist", data={"title": "   "}).status_code, 422)
        self.assertEqual(self.client.patch("/api/wishlist/1", json={"title": "Вторая"}).status_code, 409)
        self.assertEqual(self.client.patch("/api/wishlist/1", json={"title": " Новая ", "author": "Автор"}).status_code, 200)
        rows = self.client.get("/api/wishlist").json()
        self.assertEqual(next(row for row in rows if row["id"] == 1)["title"], "Новая")
        self.assertEqual(self.client.delete("/api/wishlist/1").status_code, 204)
        self.assertEqual(self.client.delete("/api/wishlist/1").status_code, 404)
        self.assertEqual(self.client.patch("/api/wishlist/99", json={"title": "Нет"}).status_code, 404)

    def test_progress_persistence_validation_and_form(self):
        self.assertEqual(self.client.get("/api/books/1/progress").json()["progress"], 0)
        response = self.client.put("/api/books/1/progress", json={"progress": 0.45, "position": "chapter:3"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.json()["updated_at"])
        self.client.post("/books/1/progress", data={"percent": 80})
        saved = self.client.get("/api/books/1/progress").json()
        self.assertEqual((saved["progress"], saved["position"]), (0.8, "chapter:3"))
        self.assertEqual(self.client.put("/api/books/1/progress", json={"progress": 1.1}).status_code, 422)
        self.assertEqual(self.client.get("/api/books/99/progress").status_code, 404)
        self.assertEqual(self.client.put("/api/books/99/progress", json={"progress": 0}).status_code, 404)

    def test_upload_duplicate_and_scan(self):
        result = subprocess.CompletedProcess([], 0, "Added book ids: 42", "")
        with patch.object(main.subprocess, "run", return_value=result) as run:
            response = self.client.post("/upload", files={"file": ("book.epub", b"book contents")}, data={"title": "Книга"})
            self.assertEqual(response.status_code, 200)
            self.client.post("/upload", files={"file": ("book.epub", b"book contents")})
            self.assertEqual(run.call_count, 1)
            book = self.client.get("/api/books").json()[0]
            self.assertEqual(self.client.get(f'/download/{book["id"]}').content, b"book contents")
            main.INCOMING.mkdir()
            source = main.INCOMING / "scan.fb2"
            source.write_bytes(b"another book")
            self.assertEqual(self.client.post("/scan").json()[0]["status"], "added")
            self.assertFalse(source.exists())

    def test_import_failures_preserve_incoming_and_cleanup_uploads(self):
        main.INCOMING.mkdir()
        source = main.INCOMING / "failed.epub"
        source.write_bytes(b"book")
        with patch.object(main.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(self.client.post("/scan").json()[0]["status"], "error")
            self.assertTrue(source.exists())
            self.assertEqual(self.client.post("/upload", files={"file": ("book.epub", b"book")}).status_code, 400)
        self.assertFalse(list(self.root.glob("tmp*.epub")))
        self.assertEqual(self.client.post("/upload", files={"file": ("book.epub", b"")}).status_code, 400)
        self.assertEqual(self.client.post("/upload", files={"file": ("book.exe", b"book")}).status_code, 400)
        with patch.object(main, "MAX_FILE_SIZE", 3):
            self.assertEqual(self.client.post("/upload", files={"file": ("book.epub", b"large")}).status_code, 413)

    def test_html_escapes_user_content(self):
        self.client.post("/wishlist", data={"title": '<script>alert("x")</script>'})
        page = self.client.get("/?q=%22%3E%3Cscript%3E").text
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)

    def test_kindle_catalog_search_and_import(self):
        from app import integrations
        candidate = dict(id='candidate', title='<Dune>', author='Herbert', suffix='.epub', score=1)
        with patch.object(integrations, 'search', return_value={'items': [candidate]}) as search:
            response = self.client.get('/kindle/integrations/results', params={'q': 'Dune', 'author': 'Herbert', 'source': 'opds', 'page_number': 2, 'wish_id': 1})
            self.assertEqual(response.status_code, 200)
            search.assert_called_once_with('opds', 'Dune', 'Herbert', 2)
            self.assertIn('&lt;Dune&gt;', response.text)
            self.assertIn('action="/kindle/integrations/import"', response.text)
            self.assertIn('name="wish_id" value="1"', response.text)
        with patch.object(integrations, 'acquire', return_value={'id': 1}) as acquire:
            response = self.client.post('/kindle/integrations/import', data={'candidate_id': 'candidate', 'wish_id': 1})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(acquire.call_args.args[0].wish_id, 1)
            self.assertIn('/download/1', response.text)

    def test_kindle_catalog_opens_without_remote_request(self):
        from app import integrations
        with patch.object(integrations, 'search') as search:
            response = self.client.get('/kindle/integrations?q=book&results=true')
        self.assertEqual(response.status_code, 200)
        search.assert_not_called()
        self.assertIn('id="catalog-results"', response.text)
        self.assertIn('/static/catalog-search.js?v=2', response.text)

    def test_kindle_errors_and_wishlist(self):
        from app import integrations
        from fastapi import HTTPException
        with patch.object(integrations, 'search', side_effect=HTTPException(502, '<upstream failed>')):
            response = self.client.get('/kindle/integrations/results?q=book')
            self.assertIn('&lt;upstream failed&gt;', response.text)
        with patch.object(integrations, 'acquire', side_effect=HTTPException(404, 'Повторите поиск')):
            response = self.client.post('/kindle/integrations/import', data={'candidate_id': 'old'})
            self.assertEqual(response.status_code, 404)
            self.assertIn('/kindle/integrations', response.text)
        self.client.post('/wishlist', data={'title': 'Дюна', 'author': 'Герберт'})
        response = self.client.get('/kindle/wishlist')
        self.assertIn('Найти в каталогах', response.text)
        for url in ('/kindle', '/kindle/login', '/kindle/integrations', '/kindle/wishlist', '/'):
            self.assertIn('/static/clear-fields.js', self.client.get(url).text)
