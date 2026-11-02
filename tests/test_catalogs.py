import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from app import catalogs
from app.ui import sources_page, catalog_mirror_links

BOOK = {"id": 123, "hash": "abc123", "title": "Dune", "author": "Frank Herbert", "extension": "epub"}


class ZLibTests(unittest.TestCase):
    def test_manual_mirrors_available_even_when_search_fails(self):
        status = {"yandex_configured": False, "flibusta_configured": True,
                  "opds_configured": False, "states": []}
        for source, mirrors in (("zlib", catalogs.ZLIB_MIRRORS),
                                ("flibusta", ("https://flibusta.is", "https://flibusta.site"))):
            with patch("app.ui.layout", side_effect=lambda title, body, active: body):
                page = sources_page("Dune", "", source, 0, [], "Источник недоступен", status)
            self.assertIn("Источник недоступен", page)
            self.assertIn("Зеркала каталога", page)
            for mirror in mirrors:
                self.assertIn(f'href="{mirror}"', page)
            self.assertEqual(page.count('<option value="zlib"'), 1)
        self.assertEqual(catalog_mirror_links("opds"), "")

    def transport(self, handler):
        client_type = httpx.Client
        return patch.object(catalogs.httpx, "Client", side_effect=lambda **kw:
                            client_type(transport=httpx.MockTransport(handler), **kw))

    def test_search_fallback_parameters_and_metadata(self):
        requests = []
        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                raise httpx.ConnectError("DNS failed", request=request)
            if len(requests) == 2:
                return httpx.Response(503)
            return httpx.Response(200, json={"success": 1, "books": [BOOK]})
        with self.transport(handler):
            books = catalogs.search_books("Dune", "Frank Herbert", 2)
        self.assertEqual([r.url.host for r in requests], ["z-zibrary.bz", "z-lib.mx", "z-library.im"])
        self.assertTrue(all(r.method == "POST" and r.url.path == "/eapi/book/search" for r in requests))
        self.assertIn(b"message=Dune+Frank+Herbert", requests[-1].content)
        self.assertIn(b"page=3", requests[-1].content)
        self.assertEqual(books[0]["title"], "Dune")
        self.assertEqual(books[0]["suffix"], ".epub")
        self.assertEqual(books[0]["url"], "https://z-library.im/eapi/book/123/abc123/file")

    def test_search_reaches_last_mirror(self):
        requests = []
        def handler(request):
            requests.append(request)
            if request.url.host != "libb.la":
                return httpx.Response(503)
            return httpx.Response(200, json={"success": 1, "books": [BOOK]})
        with self.transport(handler):
            books = catalogs.search_books("Dune", "", 0)
        self.assertEqual(len(requests), len(catalogs.ZLIB_MIRRORS))
        self.assertEqual(books[0]["url"], "https://libb.la/eapi/book/123/abc123/file")

    def test_empty_success_does_not_fallback(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"success": 1, "books": []})
        with self.transport(handler):
            self.assertEqual(catalogs.search_books("absent", "", 0), [])
        self.assertEqual(len(requests), 1)

    def test_bad_html_and_schema_fallback(self):
        requests = []
        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(200, text="<html>login</html>")
            if len(requests) == 2:
                return httpx.Response(200, json={"success": 1, "books": None})
            return httpx.Response(200, json={"success": 1, "books": [BOOK]})
        with self.transport(handler):
            self.assertEqual(len(catalogs.search_books("Dune", "", 0)), 1)
        self.assertEqual(len(requests), 3)

    def test_auth_error_and_no_external_redirect(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(302, headers={"location": "https://other.test/login"})
        with self.transport(handler), patch.dict(os.environ, {"ZLIB_REMIX_USERID": "123", "ZLIB_REMIX_USERKEY": "secret"}):
            with self.assertRaises(HTTPException):
                catalogs.search_books("Dune", "", 0)
        self.assertEqual(len(requests), len(catalogs.ZLIB_MIRRORS))
        self.assertEqual([str(r.url).split("/eapi/")[0] for r in requests], list(catalogs.ZLIB_MIRRORS))
        self.assertTrue(all("remix_userkey=secret" in r.headers["cookie"] for r in requests))
        with self.transport(lambda r: httpx.Response(200, json={"success": 0, "error": "secret"})):
            with self.assertRaises(HTTPException) as caught:
                catalogs.search_books("Dune", "", 0)
        self.assertNotIn("secret", str(caught.exception.detail))
        self.assertIn("ZLIB_REMIX_USERKEY", str(caught.exception.detail))

    def test_download_resolution_falls_back(self):
        requests = []
        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(503)
            return httpx.Response(200, json={"success": 1, "file": {"downloadLink": "https://z-lib.mx/dl/123"}})
        with self.transport(handler):
            url = catalogs.download_url("https://z-library.im/eapi/book/123/abc123/file")
        self.assertEqual(url, "https://z-lib.mx/dl/123")
        self.assertEqual(requests[0].url.host, "z-library.im")
        with self.assertRaises(HTTPException):
            catalogs.download_url("https://other.test/eapi/book/123/abc123/file")
