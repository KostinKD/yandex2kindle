import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
from core_client import TestClient
from app import main, metadata


class SeriesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(main, "DB", self.root / "app.db")
        self.patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_extract_epub_series(self):
        epub_path = self.root / "series_test.epub"
        with zipfile.ZipFile(epub_path, "w") as z:
            z.writestr("content.opf", """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Дюна</dc:title>
    <dc:creator>Фрэнк Герберт</dc:creator>
    <meta name="calibre:series" content="Хроники Дюны"/>
    <meta name="calibre:series_index" content="1"/>
  </metadata>
</package>""")
        meta = metadata.extract_book_metadata(epub_path)
        self.assertEqual(meta["series"], "Хроники Дюны")
        self.assertEqual(meta["series_index"], 1.0)
        self.assertEqual(meta["title"], "Дюна")
        self.assertEqual(meta["author"], "Фрэнк Герберт")

    def test_extract_fb2_series(self):
        fb2_path = self.root / "series_test.fb2"
        fb2_path.write_bytes("""<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
  <description>
    <title-info>
      <book-title>Мессия Дюны</book-title>
      <author><first-name>Фрэнк</first-name><last-name>Герберт</last-name></author>
      <sequence name="Хроники Дюны" number="2"/>
    </title-info>
  </description>
  <body><p>Текст</p></body>
</FictionBook>""".encode("utf-8"))
        meta = metadata.extract_book_metadata(fb2_path)
        self.assertEqual(meta["series"], "Хроники Дюны")
        self.assertEqual(meta["series_index"], 2.0)
        self.assertEqual(meta["title"], "Мессия Дюны")
        self.assertEqual(meta["author"], "Фрэнк Герберт")

    def test_add_file_preserves_series_and_allows_search(self):
        epub_path = self.root / "book1.epub"
        with zipfile.ZipFile(epub_path, "w") as z:
            z.writestr("content.opf", """<package><metadata>
              <dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">Основание</dc:title>
              <meta name="calibre:series" content="Галактическая история"/>
              <meta name="calibre:series_index" content="3"/>
            </metadata></package>""")

        with patch("app.main.subprocess.run", return_value=Mock(returncode=0, stdout="Added book ids: 1")):
            result = main.add_file(epub_path)
            self.assertEqual(result["status"], "added")

        with main.connect() as db:
            book = db.execute("SELECT * FROM books WHERE id=?", (result["id"],)).fetchone()
            self.assertEqual(book["series"], "Галактическая история")
            self.assertEqual(book["series_index"], 3.0)

        # Search by series name
        res = self.client.get("/api/books", params={"q": "Галактическая"})
        self.assertEqual(res.status_code, 200)
        items = res.json()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["series"], "Галактическая история")
        self.assertEqual(items[0]["series_index"], 3.0)
