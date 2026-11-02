import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
from core_client import TestClient
from app import main, metadata


class ExtendedMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(main, "DB", self.root / "app.db")
        self.patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_extract_epub_extended_metadata(self):
        epub_path = self.root / "extended_test.epub"
        with zipfile.ZipFile(epub_path, "w") as z:
            z.writestr("content.opf", """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Гиперион</dc:title>
    <dc:creator>Дэн Симмонс</dc:creator>
    <dc:description>&lt;p&gt;Великолепная научно-фантастическая сага.&lt;/p&gt;</dc:description>
    <dc:subject>Научная фантастика</dc:subject>
    <dc:subject>Космоопера</dc:subject>
    <dc:date>1989-05-26</dc:date>
    <dc:identifier id="isbn-id">978-0-553-28368-6</dc:identifier>
  </metadata>
</package>""")
        meta = metadata.extract_book_metadata(epub_path)
        self.assertEqual(meta["title"], "Гиперион")
        self.assertEqual(meta["author"], "Дэн Симмонс")
        self.assertEqual(meta["description"], "Великолепная научно-фантастическая сага.")
        self.assertIn("Научная фантастика", meta["genres"])
        self.assertIn("Космоопера", meta["genres"])
        self.assertEqual(meta["year"], 1989)
        self.assertEqual(meta["isbn"], "978-0-553-28368-6")

    def test_extract_fb2_extended_metadata(self):
        fb2_path = self.root / "extended_test.fb2"
        fb2_path.write_bytes("""<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
  <description>
    <title-info>
      <genre>sf_space</genre>
      <genre>sf_epic</genre>
      <author><first-name>Станислав</first-name><last-name>Лем</last-name></author>
      <book-title>Солярис</book-title>
      <annotation><p>Психологический роман о контакте с Океаном.</p></annotation>
      <date value="1961">1961</date>
    </title-info>
    <publish-info>
      <isbn>5-17-005663-8</isbn>
    </publish-info>
  </description>
</FictionBook>""".encode("utf-8"))
        meta = metadata.extract_book_metadata(fb2_path)
        self.assertEqual(meta["title"], "Солярис")
        self.assertEqual(meta["author"], "Станислав Лем")
        self.assertEqual(meta["description"], "Психологический роман о контакте с Океаном.")
        self.assertIn("sf_space", meta["genres"])
        self.assertEqual(meta["year"], 1961)
        self.assertEqual(meta["isbn"], "5-17-005663-8")

    def test_search_and_display_extended_metadata(self):
        book_file = self.root / "lem.fb2"
        book_file.write_bytes(b"dummy")
        with patch("subprocess.run") as mock_calibre:
            mock_calibre.return_value = Mock(returncode=0, stdout="Added book ids: 10\n", stderr="")
            res = main.add_file(
                book_file,
                title="Солярис",
                author="Станислав Лем",
                description="Роман про мыслящий Океан",
                genres="Космос, Классика",
                year=1961,
                isbn="5-17-005663-8"
            )
            self.assertEqual(res["status"], "added")

        # Search by description
        res_search_desc = self.client.get("/?q=мыслящий")
        self.assertIn("Солярис", res_search_desc.text)

        # Search by genre
        res_search_genre = self.client.get("/?q=Классика")
        self.assertIn("Солярис", res_search_genre.text)

        # Verify synopsis details and tags in HTML
        home_res = self.client.get(f"/books/{res['id']}")
        self.assertIn("Аннотация", home_res.text)
        self.assertIn("Роман про мыслящий Океан", home_res.text)
        self.assertIn("1961", home_res.text)
        self.assertIn("Космос", home_res.text)
        self.assertIn("/reader/1", home_res.text)
