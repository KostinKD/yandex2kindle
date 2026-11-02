import asyncio
import hashlib
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi import HTTPException
from core_client import TestClient

from app import integrations, kosync, main

FEED = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>book-1</id><title>Dune</title><author><name>Frank Herbert</name></author>
<link rel="http://opds-spec.org/acquisition" type="application/epub+zip" href="/books/1.epub"/>
<link rel="http://opds-spec.org/acquisition/buy" type="application/epub+zip" href="/buy"/>
<link rel="http://opds-spec.org/acquisition" type="application/pdf" href="https://untrusted.invalid/book"/>
</entry><entry><id>book-2</id><title>Another book</title>
<link rel="http://opds-spec.org/acquisition/open-access" type="application/pdf" href="/books/2.pdf"/>
</entry></feed>'''


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(main, key, self.root / name) for key, name in
                        (("DB", "db.sqlite"), ("BASE", "calibre"), ("INCOMING", "incoming"))]
        self.patches.append(patch.dict(os.environ, {
            "FLIBUSTA_SEARCH_URL": "https://catalog.test/opds/search?searchTerm={searchTerms}&page={startPage?}",
            "OPDS_SEARCH_URL": "https://catalog.test/search?q={searchTerms}",
            "REMOTE_ALLOWED_ORIGINS": "", "KOSYNC_LIBRARY_USER": "reader",
            "REMOTE_BOOK_SESSION": "false",
            "KOSYNC_ALLOW_REGISTRATION": "true", "YANDEX_SYNC_INTERVAL_SECONDS": "0",
        }))
        for item in self.patches:
            item.start()
        self.client = TestClient(main.app)
        self.client_type = httpx.Client

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def transport(self, handler):
        def factory(**kwargs):
            return self.client_type(transport=httpx.MockTransport(handler), **kwargs)
        return patch.object(integrations.httpx, "Client", side_effect=factory)

    def book(self, content=b"test book", title="Dune"):
        path = self.root / "book.epub"
        path.write_bytes(content)
        with main.connect() as db:
            return db.execute("INSERT INTO books(title,author,filename,sha256,created_at) VALUES(?,?,?,?,?)",
                              (title, "Frank Herbert", str(path), hashlib.sha256(content).hexdigest(), "2026-01-01T00:00:00Z")).lastrowid

    def register(self, username="reader", password="password"):
        key = hashlib.md5(password.encode()).hexdigest()
        response = self.client.post("/users/create", json={"username": username, "password": key})
        self.assertEqual(response.status_code, 201)
        return {"x-auth-user": username, "x-auth-key": key}

    def test_catalog_search_selection_download_and_duplicate(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, content=FEED if request.url.path.startswith("/opds") else b"epub content")
        with self.transport(handler), patch.object(main.subprocess, "run", return_value=Mock(returncode=0, stdout="Added book ids: 1", stderr="")) as calibre:
            response = self.client.get("/api/sources/flibusta/search", params={"q": "Dune", "author": "Frank Herbert"})
            self.assertEqual(response.status_code, 200, response.text)
            items = response.json()["items"]
            self.assertEqual(len(items), 2)
            self.assertEqual((items[0]["title"], items[0]["score"]), ("Dune", 1))
            self.assertNotIn("url", items[0])
            chosen = {"candidate_id": items[0]["id"]}
            self.assertEqual(self.client.post("/api/sources/import", json=chosen).json()["status"], "added")
            self.assertEqual(self.client.post("/api/sources/import", json=chosen).json()["status"], "duplicate")
            self.assertEqual(calibre.call_count, 1)
            self.assertFalse(list(self.root.glob("acquire-*")))
            self.assertTrue(all("auth-token" not in request.headers for request in requests))
        self.assertEqual(self.client.post("/api/sources/import", json={"candidate_id": "missing"}).status_code, 404)

    def test_catalog_page_does_not_wait_for_remote_search(self):
        with patch.object(integrations, "fetch_remote") as remote:
            response = self.client.get("/integrations?q=Dune&wish_id=1&results=true")
        self.assertEqual(response.status_code, 200)
        remote.assert_not_called()
        self.assertIn('id="catalog-results"', response.text)
        self.assertIn('name="wish_id" value="1"', response.text)
        with self.transport(lambda r: httpx.Response(200, content=FEED)):
            response = self.client.get("/integrations/results?q=Dune&wish_id=1")
        self.assertIn('class="result-row"', response.text)
        self.assertIn('name="candidate_id"', response.text)

    def test_empty_title_and_author_only_search(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, content=FEED)
        with self.transport(handler):
            response = self.client.get("/api/sources/flibusta/search", params={"author": "Frank Herbert"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["items"][0]["score"], 1)
            self.assertEqual(requests[-1].url.params["searchTerm"], "Frank Herbert")
            response = self.client.get("/api/sources/flibusta/search?q=")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(requests[-1].url.params["searchTerm"], "")
            response = self.client.get("/integrations/results?q=&author=")
            self.assertIn('class="result-row"', response.text)

    def test_catalog_errors_redirect_safety_and_size(self):
        cases = [(lambda r: httpx.Response(200, content=b"not xml"), 502),
                 (lambda r: httpx.Response(200, content=b'<!DOCTYPE feed><feed/>'), 502),
                 (lambda r: httpx.Response(403), 502),
                 (lambda r: httpx.Response(302, headers={"location": "http://127.0.0.1/private"}), 502)]
        for handler, status in cases:
            with self.transport(handler):
                self.assertEqual(self.client.get("/api/sources/flibusta/search?q=book").status_code, status)
        with self.transport(lambda r: httpx.Response(200, content=b"1234")):
            with self.assertRaises(HTTPException) as caught:
                integrations.fetch_remote("https://catalog.test/file", "flibusta", max_size=3)
            self.assertEqual(caught.exception.status_code, 413)
        with patch.dict(os.environ, {"FLIBUSTA_SEARCH_URL": ""}):
            self.assertEqual(self.client.get("/api/sources/flibusta/search?q=book").status_code, 503)
        with self.transport(lambda r: httpx.Response(200, content=FEED)):
            self.assertEqual(self.client.get("/api/sources/flibusta/search?q=%20").status_code, 200)

    def test_blocked_zlib_cdn_reports_only_origin_and_can_be_allowed(self):
        url = "https://cdn.catalog.test:8443/private/token/book.epub?key=secret"
        with self.assertRaises(HTTPException) as caught:
            integrations.allowed_url(url, "zlib")
        self.assertIn("https://cdn.catalog.test:8443", caught.exception.detail)
        self.assertIn("REMOTE_ALLOWED_ORIGINS", caught.exception.detail)
        self.assertNotIn("/private", caught.exception.detail)
        self.assertNotIn("secret", caught.exception.detail)
        with patch.dict(os.environ, {"REMOTE_ALLOWED_ORIGINS": "https://cdn.catalog.test:8443"}):
            self.assertEqual(integrations.allowed_url(url, "zlib"), url)
            with self.assertRaises(HTTPException):
                integrations.allowed_url("https://other.test/book.epub", "zlib")

    def test_flibusta_site_fallback(self):
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.host == "flibusta.is":
                raise httpx.ConnectError("unavailable", request=request)
            return httpx.Response(200, content=FEED)
        with patch.dict(os.environ, {"FLIBUSTA_SEARCH_URL": "https://flibusta.is/opds/search?searchTerm={searchTerms}&page={startPage?}"}), self.transport(handler):
            response = self.client.get("/api/sources/flibusta/search", params={"q": "Dune", "page": 2})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(response.json()["items"]), 2)
            self.assertEqual([r.url.host for r in requests], ["flibusta.is", "flibusta.site"])
            self.assertEqual(requests[1].url.params["searchTerm"], "Dune")
            self.assertEqual(requests[1].url.params["page"], "2")
            with main.connect() as db:
                urls = [row[0] for row in db.execute("SELECT url FROM remote_candidates")]
            self.assertTrue(all(url.startswith("https://flibusta.site/") for url in urls))
            self.assertEqual(integrations.allowed_url("https://static.flibusta.site/converter/get", "flibusta"),
                             "https://static.flibusta.site/converter/get")

    def test_zlib_search_import_and_internal_results(self):
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.host == "z-zibrary.bz":
                raise httpx.ConnectError("unavailable", request=request)
            if request.url.path == "/eapi/book/search":
                return httpx.Response(200, json={"success": 1, "books": [
                    {"id": 123, "hash": "abc123", "title": "Dune", "author": "Frank Herbert", "extension": "epub"}]})
            if request.url.path.endswith("/file"):
                return httpx.Response(200, json={"success": 1, "file": {"downloadLink": "https://z-lib.mx/dl/123"}})
            return httpx.Response(200, content=b"epub content")
        with self.transport(handler), patch.dict(os.environ, {"ZLIB_REMIX_USERID": "123", "ZLIB_REMIX_USERKEY": "session-key"}), patch.object(main.subprocess, "run", return_value=Mock(returncode=0, stdout="Added book ids: 1", stderr="")) as calibre:
            response = self.client.get("/api/sources/zlib/search?q=Dune&author=Frank%20Herbert")
            self.assertEqual(response.status_code, 200, response.text)
            item = response.json()["items"][0]
            self.assertEqual((item["source"], item["title"], item["score"]), ("zlib", "Dune", 1))
            self.assertNotIn("url", item)
            for url in ("/integrations/results?source=zlib&q=Dune", "/kindle/integrations/results?source=zlib&q=Dune"):
                page = self.client.get(url)
                self.assertEqual(page.status_code, 200, page.text)
                self.assertIn("Dune", page.text)
                self.assertIn('name="candidate_id"', page.text)
                self.assertNotIn("Открыть поиск на сайте", page.text)
            result = self.client.post("/api/sources/import", json={"candidate_id": item["id"]})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["status"], "added")
            self.assertEqual(calibre.call_count, 1)
        download = next(r for r in requests if r.url.path == "/dl/123")
        self.assertNotIn("remix_userkey", download.headers.get("cookie", ""))
        with self.assertRaises(HTTPException):
            integrations.allowed_url("https://untrusted.invalid/file", "zlib")

    def test_same_origin_redirect_and_expired_candidate(self):
        def handler(request):
            if request.url.path == "/opds/search":
                return httpx.Response(302, headers={"location": "/actual/feed"})
            return httpx.Response(200, content=FEED)
        with self.transport(handler):
            result = self.client.get("/api/sources/flibusta/search?q=Dune").json()
        with main.connect() as db:
            db.execute("UPDATE remote_candidates SET expires_at=0")
        self.assertEqual(self.client.post("/api/sources/import", json={"candidate_id": result["items"][0]["id"]}).status_code, 404)

    def test_download_has_separate_timeouts_and_reports_read_failure(self):
        requests = []
        def handler(request):
            requests.append(request)
            raise httpx.ReadTimeout("upstream stalled", request=request)
        with self.transport(handler):
            with self.assertRaises(HTTPException) as caught:
                integrations.fetch_remote("https://catalog.test/file", "flibusta", target=io.BytesIO())
            self.assertEqual(caught.exception.status_code, 504)
            self.assertIn("скачивание", caught.exception.detail.lower())
            self.assertIn("120 секунд", caught.exception.detail)
            self.assertIn("0 байт", caught.exception.detail)
            with self.assertRaises(HTTPException):
                integrations.fetch_remote("https://catalog.test/search", "flibusta")
        self.assertEqual(requests[0].extensions["timeout"]["read"], 120)
        self.assertEqual(requests[1].extensions["timeout"]["read"], 20)
        self.assertEqual(requests[0].extensions["timeout"]["connect"], 60)

    def test_connection_timeout_is_configurable_and_has_specific_error(self):
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ConnectTimeout("connection failed", request=request)
        with patch.dict(os.environ, {"REMOTE_CONNECT_TIMEOUT_SECONDS": "45"}), self.transport(handler):
            with self.assertRaises(HTTPException) as caught:
                integrations.fetch_remote("https://catalog.test/search", "flibusta")
        self.assertEqual(calls[0].extensions["timeout"]["connect"], 45)
        self.assertEqual(caught.exception.status_code, 504)
        self.assertIn("45 секунд", caught.exception.detail)
        with patch.dict(os.environ, {"REMOTE_CONNECT_TIMEOUT_SECONDS": "bad"}), self.assertRaises(HTTPException) as caught:
            integrations.fetch_remote("https://catalog.test/search", "flibusta")
        self.assertEqual(caught.exception.status_code, 503)

    def test_catalog_can_respect_proxy_settings_or_use_direct_connection(self):
        for value, expected in (("true", True), ("false", False)):
            with patch.dict(os.environ, {"REMOTE_TRUST_ENV": value}), self.transport(lambda r: httpx.Response(200, content=FEED)) as factory:
                integrations.fetch_remote("https://catalog.test/search", "flibusta")
                self.assertEqual(factory.call_args.kwargs["trust_env"], expected)

    def test_catalog_headers_are_browser_compatible_and_configurable(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, content=FEED)
        with patch.dict(os.environ, {"REMOTE_USER_AGENT": ""}), self.transport(handler):
            integrations.fetch_remote("https://catalog.test/search", "flibusta")
            integrations.fetch_remote("https://catalog.test/book", "flibusta", target=io.BytesIO())
        self.assertTrue(calls[0].headers["user-agent"].startswith("Mozilla/5.0"))
        self.assertIn("application/atom+xml", calls[0].headers["accept"])
        self.assertEqual(calls[1].headers["accept"], "*/*")
        with patch.dict(os.environ, {"REMOTE_USER_AGENT": "MyCatalogClient/2"}), self.transport(handler):
            integrations.fetch_remote("https://catalog.test/search", "flibusta")
        self.assertEqual(calls[-1].headers["user-agent"], "MyCatalogClient/2")

    def test_book_session_cookies_and_referer_follow_browser_redirect_policy(self):
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.path == "/b/1":
                return httpx.Response(200, content=b"Book page", headers={"set-cookie": "session=test-session; Domain=catalog.test; Path=/; Secure"})
            if request.url.path == "/b/1/epub":
                return httpx.Response(302, headers={"location": "https://static.catalog.test/file?key=private-key"})
            self.assertEqual(request.headers["referer"], "https://catalog.test/")
            self.assertIn("session=test-session", request.headers["cookie"])
            return httpx.Response(200, content=b"book")
        trace = []
        with patch.dict(os.environ, {"REMOTE_BOOK_SESSION": "true", "REMOTE_ALLOWED_ORIGINS": "https://static.catalog.test"}), self.transport(handler):
            integrations.fetch_remote("https://catalog.test/b/1/epub", "flibusta", target=io.BytesIO(), trace=trace)
        self.assertEqual(len(requests), 3)
        self.assertEqual(requests[1].headers["referer"], "https://catalog.test/b/1")
        self.assertNotIn("private-key", str(trace))
        self.assertNotIn("test-session", str(trace))
        self.assertTrue(trace[-1]["request_had_cookie"])

    def test_transient_download_502_is_retried_once_before_writing_bytes(self):
        count = 0
        def handler(request):
            nonlocal count
            count += 1
            return httpx.Response(502 if count == 1 else 200, content=b"error" if count == 1 else b"book")
        output = io.BytesIO()
        with self.transport(handler):
            integrations.fetch_remote("https://catalog.test/book", "flibusta", target=output)
        self.assertEqual(count, 2)
        self.assertEqual(output.getvalue(), b"book")
        with self.transport(lambda r: httpx.Response(502)) as factory, self.assertRaises(HTTPException):
            integrations.fetch_remote("https://catalog.test/book", "flibusta", target=io.BytesIO())

    def test_explicitly_trusted_download_cdn_redirect(self):
        def handler(request):
            if request.url.host == "catalog.test":
                return httpx.Response(302, headers={"location": "https://static.flibusta.site/converter/get/convert?out=epub"})
            return httpx.Response(200, content=b"book contents")
        target = io.BytesIO()
        with patch.dict(os.environ, {"REMOTE_ALLOWED_ORIGINS": "https://static.flibusta.site"}), self.transport(handler):
            integrations.fetch_remote("https://catalog.test/book", "flibusta", target=target)
        self.assertEqual(target.getvalue(), b"book contents")

    def test_failed_download_cleans_partial_file_and_does_not_run_calibre(self):
        class BrokenStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b"partial"
                raise httpx.ReadTimeout("stalled")
        def handler(request):
            if request.url.path.startswith("/opds"):
                return httpx.Response(200, content=FEED)
            return httpx.Response(200, stream=BrokenStream())
        with self.transport(handler), patch.object(main.subprocess, "run") as calibre:
            chosen = self.client.get("/api/sources/flibusta/search?q=Dune").json()["items"][0]["id"]
            response = self.client.post("/api/sources/import", json={"candidate_id": chosen})
        self.assertEqual(response.status_code, 504)
        self.assertIn("7 байт", response.json()["detail"])
        self.assertFalse(list(self.root.glob("acquire-*")))
        calibre.assert_not_called()

    def test_original_fb2_zip_is_listed_and_imported_locally(self):
        content = b'<?xml version="1.0"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><body/></FictionBook>'
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("../../external/book.fb2", content)
        feed = FEED.replace(b'application/epub+zip', b'application/fb2+zip')
        def handler(request):
            return httpx.Response(200, content=feed if request.url.path.startswith("/opds") else archive.getvalue())
        with self.transport(handler), patch.object(main.subprocess, "run", return_value=Mock(returncode=0, stdout="Added book ids: 2", stderr="")):
            found = self.client.get("/api/sources/flibusta/search?q=Dune").json()["items"]
            self.assertEqual(found[0]["suffix"], ".fb2.zip")
            result = self.client.post("/api/sources/import", json={"candidate_id": found[0]["id"]})
        self.assertEqual(result.status_code, 200, result.text)
        response = self.client.get(f'/download/{result.json()["id"]}')
        self.assertEqual(response.content, content)
        self.assertIn(".fb2", response.headers["content-disposition"])
        self.assertFalse((self.root / "external").exists())

    def test_zip_rejects_multiple_books_and_oversized_content(self):
        path = self.root / "book.zip"
        with zipfile.ZipFile(path, "w") as bundle:
            bundle.writestr("one.fb2", b"one")
            bundle.writestr("two.fb2", b"two")
        with self.assertRaises(HTTPException) as caught:
            integrations.unpack_fb2(path, self.root / "book.fb2")
        self.assertEqual(caught.exception.status_code, 502)
        with zipfile.ZipFile(path, "w") as bundle:
            bundle.writestr("one.fb2", b"too big")
        with patch.object(main, "MAX_FILE_SIZE", 3), self.assertRaises(HTTPException) as caught:
            integrations.unpack_fb2(path, self.root / "book.fb2")
        self.assertEqual(caught.exception.status_code, 413)

    def test_download_denial_explains_external_access_failure(self):
        with self.transport(lambda request: httpx.Response(403)), self.assertRaises(HTTPException) as caught:
            integrations.fetch_remote("https://catalog.test/book", "flibusta", target=io.BytesIO())
        self.assertEqual(caught.exception.status_code, 502)
        self.assertIn("HTTP 403", caught.exception.detail)
        self.assertIn("FB2 ZIP", caught.exception.detail)

    def card(self, identity="uuid-1", title="Dune"):
        return {"book": {"uuid": identity, "title": title, "authors": "Frank Herbert"}}

    def test_yandex_pagination_idempotence_and_renamed_book(self):
        cards = [self.card(str(i), f"Book {i}") for i in range(100)]
        calls = []
        pages = [cards, [self.card()]]
        def handler(request):
            self.assertEqual(request.headers["auth-token"], "secret-token")
            self.assertEqual(request.url.host, "api.bookmate.yandex.net")
            if request.url.path.endswith("/profile"):
                return httpx.Response(200, json={"user": {"login": "my-account"}})
            calls.append(dict(request.url.params))
            return httpx.Response(200, json={"library_cards": pages.pop(0)})
        with self.transport(handler), patch.dict(os.environ, {"YANDEX_BOOK_TOKEN": "secret-token"}):
            response = self.client.post("/api/integrations/yandex/sync")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["added"], 101)
            self.assertEqual(calls[1], {"page": "2", "per_page": "100"})
            pages.append([self.card(title="Dune, corrected")])
            self.assertEqual(self.client.post("/api/integrations/yandex/sync").json()["updated"], 1)
            pages.append([self.card(title="Dune, corrected")])
            self.assertEqual(self.client.post("/api/integrations/yandex/sync").json()["added"], 0)
            wishes = self.client.get("/api/wishlist").json()
            self.assertEqual(len(wishes), 101)
            self.assertTrue(all(row["source"] == "yandex:my-account" for row in wishes))
            self.assertNotIn("secret-token", self.client.get("/api/integrations").text)

    def test_yandex_failure_is_atomic_and_persisted(self):
        cards = [self.card(str(i), f"Book {i}") for i in range(100)]
        def handler(request):
            if request.url.path.endswith("/profile"):
                return httpx.Response(200, json={"user": {"login": "my-account"}})
            if request.url.params["page"] == "1":
                return httpx.Response(200, json={"library_cards": cards})
            return httpx.Response(500, text="token must never be exposed")
        with self.transport(handler), patch.dict(os.environ, {"YANDEX_BOOK_TOKEN": "private"}):
            self.assertEqual(self.client.post("/api/integrations/yandex/sync").status_code, 502)
        self.assertEqual(self.client.get("/api/wishlist").json(), [])
        status = self.client.get("/api/integrations").json()["states"][0]
        self.assertEqual(status["status"], "error")
        self.assertNotIn("token must never", status["message"])
        with patch.dict(os.environ, {"YANDEX_BOOK_TOKEN": ""}):
            self.assertEqual(self.client.post("/api/integrations/yandex/sync").status_code, 503)

    def test_yandex_unauthorized_and_sync_lock(self):
        with self.transport(lambda r: httpx.Response(401)), patch.dict(os.environ, {"YANDEX_BOOK_TOKEN": "bad"}):
            self.assertEqual(self.client.post("/api/integrations/yandex/sync").status_code, 401)
        with integrations.SYNC_LOCK:
            self.assertEqual(self.client.post("/api/integrations/yandex/sync").status_code, 409)

    def test_yandex_author_variants_and_optional_fields(self):
        books = [
            {"uuid": "1", "title": "One", "authors": "Author One"},
            {"uuid": "2", "name": "Two", "authors": [{"name": "Author Two"}]},
            {"uuid": "3", "title": "Three", "authors": None, "authors_objects": [{"name": "Author Three"}]},
            {"uuid": "4", "title": "Four"},
        ]
        def handler(request):
            data = {"user": {"login": "my-account"}} if request.url.path.endswith("/profile") else {
                "library_cards": [{"book": book} for book in books]}
            return httpx.Response(200, json=data)
        with self.transport(handler), patch.dict(os.environ, {"YANDEX_BOOK_TOKEN": "private"}):
            response = self.client.post("/api/integrations/yandex/sync")
        self.assertEqual(response.status_code, 200, response.text)
        rows = {row["title"]: row["author"] for row in self.client.get("/api/wishlist").json()}
        self.assertEqual(rows, {"One": "Author One", "Two": "Author Two", "Three": "Author Three", "Four": ""})

    def test_yandex_contract_errors_are_specific_and_redirects_not_followed(self):
        for handler, expected in (
            (lambda r: httpx.Response(200, json={"user": None}), "profile.user"),
            (lambda r: httpx.Response(200, text="invalid"), "JSON"),
            (lambda r: httpx.Response(302, headers={"location": "https://untrusted.test"}), "HTTP 302"),
            (lambda r: httpx.Response(200, json={"user": {"login": "me"}}), "library_cards"),
        ):
            with self.transport(handler), patch.dict(os.environ, {"YANDEX_BOOK_TOKEN": "private"}):
                response = self.client.post("/api/integrations/yandex/sync")
            self.assertEqual(response.status_code, 502)
            self.assertIn(expected, response.json()["detail"])
            self.assertNotIn("private", response.text)

    def test_scheduled_sync_runs_and_retries(self):
        async def scenario():
            with patch.dict(os.environ, {"YANDEX_SYNC_INTERVAL_SECONDS": "60"}):
                with patch.object(integrations, "sync_yandex", side_effect=[HTTPException(502), {"status": "ok"}]) as sync:
                    count = 0
                    async def sleep(seconds, *args):
                        nonlocal count
                        count += 1
                        if count == 2:
                            raise asyncio.CancelledError()
                    with patch.object(integrations, "wait_settings", side_effect=sleep):
                        with self.assertRaises(asyncio.CancelledError):
                            await integrations.scheduled_sync()
                    self.assertEqual(sync.call_count, 2)
        asyncio.run(scenario())

    def test_kosync_registration_auth_and_user_isolation(self):
        headers = self.register()
        self.assertEqual(self.client.get("/users/auth", headers=headers).json(), {"authorized": "OK"})
        self.assertEqual(self.client.get("/users/auth").status_code, 401)
        self.assertEqual(self.client.get("/users/auth").json()["code"], 2001)
        self.assertEqual(self.client.post("/users/create", json={"username": "reader", "password": "x"}).status_code, 402)
        other = self.register("other")
        payload = {"document": "abc123", "progress": "/body/p[3]", "percentage": .4, "device": "Kindle", "device_id": "kindle-1"}
        pushed = self.client.put("/syncs/progress", headers=headers, json=payload)
        self.assertEqual(pushed.status_code, 200, pushed.text)
        received = self.client.get("/syncs/progress/abc123", headers=headers).json()
        self.assertEqual(received["progress"], payload["progress"])
        self.assertIsInstance(received["timestamp"], int)
        self.assertEqual(self.client.get("/syncs/progress/abc123", headers=other).json(), {})
        with main.connect() as db:
            row = db.execute("SELECT * FROM kosync_users WHERE username='reader'").fetchone()
            self.assertNotEqual(row["key_hash"], headers["x-auth-key"])
        self.assertEqual(self.client.put("/syncs/progress", headers=headers, json={**payload, "percentage": 5}).status_code, 403)
        with patch.dict(os.environ, {"KOSYNC_ALLOW_REGISTRATION": "false"}):
            self.assertEqual(self.client.post("/users/create", json={"username": "new", "password": "x"}).status_code, 403)

    def test_partial_digest_reference_vectors(self):
        data = bytes(range(256)) * 400
        path = self.root / "checksum.bin"
        path.write_bytes(data)
        # LuaJIT's negative shift count wraps, so the first sample is at byte 0.
        expected = hashlib.md5(b"".join(data[start:start + 1024] for start in (0, 1024, 4096, 16384, 65536))).hexdigest()
        self.assertEqual(kosync.partial_md5(path), expected)
        path.write_bytes(b"short")
        self.assertEqual(kosync.partial_md5(path), hashlib.md5(b"short").hexdigest())

    def test_kosync_library_bridge_both_directions(self):
        book_id = self.book()
        headers = self.register()
        docs = self.client.get(f"/api/books/{book_id}/documents").json()
        document = next(row["document"] for row in docs if row["mode"] == "binary")
        payload = {"document": document, "progress": "/body/p[7]", "percentage": .7, "device": "Kindle"}
        self.assertEqual(self.client.put("/syncs/progress", headers=headers, json=payload).status_code, 200)
        local = self.client.get(f"/api/books/{book_id}/progress").json()
        self.assertEqual((local["progress"], local["position"]), (.7, "/body/p[7]"))
        with main.connect() as db:
            state = db.execute("SELECT * FROM book_device_state WHERE book_id=?", (book_id,)).fetchone()
        self.assertIsNotNone(state["reader_seen_at"])
        self.assertIsNone(state["kindle_confirmed_at"])
        self.client.put(f"/api/books/{book_id}/progress", json={"progress": .8, "position": "/body/p[8]"})
        self.assertEqual(self.client.get(f"/syncs/progress/{document}", headers=headers).json()["progress"], "/body/p[8]")
        other = self.register("other")
        self.client.put("/syncs/progress", headers=other, json={**payload, "percentage": .1})
        self.assertEqual(self.client.get(f"/api/books/{book_id}/progress").json()["progress"], .8)

    def test_integration_forms_escape_html(self):
        response = self.client.get("/integrations", params={"author": '<script>alert(1)</script>'})
        self.assertEqual(response.status_code, 200)
        self.assertIn("&lt;script&gt;", response.text)
        self.assertNotIn("<script>", response.text)
