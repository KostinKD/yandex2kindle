import base64
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from core_client import TestClient
from app import main, covers


SAMPLE_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00"
SAMPLE_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"


class CoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(main, "DB", self.root / "app.db")
        self.patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_detect_image_type(self):
        self.assertEqual(covers.detect_image_type(SAMPLE_JPEG), "image/jpeg")
        self.assertEqual(covers.detect_image_type(SAMPLE_PNG), "image/png")
        self.assertEqual(covers.detect_image_type(b"RIFF1234WEBPVP8 "), "image/webp")
        self.assertIsNone(covers.detect_image_type(b"NOT_AN_IMAGE"))

    def test_extract_epub_cover(self):
        epub_path = self.root / "test.epub"
        with zipfile.ZipFile(epub_path, "w") as z:
            z.writestr("META-INF/container.xml", """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""")
            z.writestr("OEBPS/content.opf", """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <meta name="cover" content="my-cover-id"/>
  </metadata>
  <manifest>
    <item id="my-cover-id" href="images/cover.jpg" media-type="image/jpeg"/>
  </manifest>
</package>""")
            z.writestr("OEBPS/images/cover.jpg", SAMPLE_JPEG)

        extracted = covers.extract_cover_bytes(epub_path)
        self.assertEqual(extracted, SAMPLE_JPEG)

    def test_extract_fb2_cover(self):
        fb2_path = self.root / "test.fb2"
        b64_cover = base64.b64encode(SAMPLE_JPEG).decode("ascii")
        fb2_content = f"""<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
  <description>
    <title-info><book-title>Тест</book-title></title-info>
  </description>
  <body><p>Текст</p></body>
  <binary id="cover.jpg" content-type="image/jpeg">{b64_cover}</binary>
</FictionBook>""".encode("utf-8")
        fb2_path.write_bytes(fb2_content)

        extracted = covers.extract_cover_bytes(fb2_path)
        self.assertEqual(extracted, SAMPLE_JPEG)

    def test_serve_cover_success_and_cache(self):
        epub_path = self.root / "book.epub"
        with zipfile.ZipFile(epub_path, "w") as z:
            z.writestr("content.opf", """<package><manifest><item id="c" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/></manifest></package>""")
            z.writestr("cover.jpg", SAMPLE_JPEG)

        with main.connect() as db:
            db.execute("INSERT INTO books(id,title,author,filename,sha256,created_at) VALUES(?,?,?,?,?,?)",
                       (10, "Книга с обложкой", "Автор", str(epub_path), "cover_sha_123", "2026-01-01T00:00:00Z"))

        response = self.client.get("/covers/10")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertEqual(response.content, SAMPLE_JPEG)

        # Cache file should now exist
        cached_file = self.root / "covers" / "cover_sha_123.img"
        self.assertTrue(cached_file.is_file())

        # Second request should be served from cache
        second = self.client.get("/covers/10")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.content, SAMPLE_JPEG)

    def test_serve_cover_missing_returns_404(self):
        # Book exists but has no cover
        txt_path = self.root / "book.txt"
        txt_path.write_text("Hello plain text")
        with main.connect() as db:
            db.execute("INSERT INTO books(id,title,author,filename,sha256,created_at) VALUES(?,?,?,?,?,?)",
                       (20, "Без обложки", "Автор", str(txt_path), "no_cover_sha", "2026-01-01T00:00:00Z"))

        response = self.client.get("/covers/20")
        self.assertEqual(response.status_code, 404)

    def test_opds_includes_cover_links(self):
        with main.connect() as db:
            db.execute("INSERT INTO books(id,title,author,filename,sha256,created_at) VALUES(?,?,?,?,?,?)",
                       (30, "OPDS Book", "Автор", "dummy.epub", "sha30", "2026-01-01T00:00:00Z"))
        feed = self.client.get("/opds").text
        self.assertIn('rel="http://opds-spec.org/image"', feed)
        self.assertIn('/covers/30', feed)
