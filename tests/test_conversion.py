import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core_client import TestClient
from app import main, conversion


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(main, "DB", self.root / "app.db")
        self.patch.start()
        self.source = self.root / "source.fb2"
        self.source.write_bytes(b"FB2 source")
        with main.connect() as db:
            db.execute("INSERT INTO books(title,author,filename,sha256,created_at) VALUES(?,?,?,?,?)",
                       ("Книга", "Автор", str(self.source), "digest", "2026-01-01T00:00:00Z"))
        self.client = TestClient(main.app)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_conversion_is_cached_and_original_remains_unchanged(self):
        def convert(args, **kwargs):
            self.assertEqual(args[0], "ebook-convert")
            Path(args[2]).write_bytes(b"converted EPUB")
            return Mock(returncode=0)
        with patch.object(conversion.subprocess, "run", side_effect=convert) as run:
            first = self.client.get("/download/1/epub")
            second = self.client.get("/download/1/epub")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.headers["content-type"], "application/epub+zip")
        self.assertEqual(first.content, second.content)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(self.source.read_bytes(), b"FB2 source")

    def test_failure_does_not_cache_partial_output(self):
        def convert(args, **kwargs):
            Path(args[2]).write_bytes(b"partial")
            return Mock(returncode=1)
        with patch.object(conversion.subprocess, "run", side_effect=convert):
            self.assertEqual(self.client.get("/download/1/epub").status_code, 422)
        self.assertFalse(list((self.root / "converted").glob("*.epub")))
        self.assertFalse(list((self.root / "converted").glob("epub-*")))

    def test_converter_absent_or_timed_out(self):
        for error, status in ((FileNotFoundError(), 503), (subprocess.TimeoutExpired("ebook-convert", 180), 504)):
            with patch.object(conversion.subprocess, "run", side_effect=error):
                self.assertEqual(self.client.get("/download/1/epub").status_code, status)

    def test_native_epub_is_returned_without_converter(self):
        source = self.root / "source.epub"
        source.write_bytes(b"original EPUB")
        with main.connect() as db:
            db.execute("UPDATE books SET filename=? WHERE id=1", (str(source),))
        with patch.object(conversion.subprocess, "run") as run:
            self.assertEqual(self.client.get("/download/1/epub").content, b"original EPUB")
        run.assert_not_called()
