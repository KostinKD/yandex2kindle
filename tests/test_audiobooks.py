import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
from core_client import TestClient
from app import main, metadata


class AudiobookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(main, "DB", self.root / "app.db")
        self.patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_audio_metadata_extraction_fallback(self):
        audio_file = self.root / "Arthur Conan Doyle - A Study in Scarlet.mp3"
        audio_file.write_bytes(b"dummy mp3 data" * 100)
        meta = metadata.extract_book_metadata(audio_file)
        self.assertEqual(meta["title"], "A Study in Scarlet")
        self.assertEqual(meta["author"], "Arthur Conan Doyle")
        self.assertEqual(meta["genres"], "Аудиокнига")

    def test_audiobook_player_and_streaming(self):
        audio_file = self.root / "sample.mp3"
        # 10 KB dummy audio content
        content = b"0123456789" * 1000
        audio_file.write_bytes(content)

        with patch("subprocess.run") as mock_calibre:
            mock_calibre.return_value = Mock(returncode=0, stdout="Added book ids: 1\n", stderr="")
            res = main.add_file(audio_file, title="Шерлок Холмс", author="Конан Дойл")
            book_id = res["id"]

        # Player page
        player_res = self.client.get(f"/audiobook/{book_id}")
        self.assertEqual(player_res.status_code, 200)
        self.assertIn("Шерлок Холмс", player_res.text)
        self.assertIn("seek-bar", player_res.text)
        self.assertIn("audiobook/1/stream", player_res.text)

        # Full stream
        stream_res = self.client.get(f"/audiobook/{book_id}/stream")
        self.assertEqual(stream_res.status_code, 200)
        self.assertEqual(stream_res.headers.get("accept-ranges"), "bytes")
        self.assertEqual(len(stream_res.content), len(content))

        # Range request (HTTP 206)
        range_res = self.client.get(
            f"/audiobook/{book_id}/stream",
            headers={"Range": "bytes=0-99"}
        )
        self.assertEqual(range_res.status_code, 206)
        self.assertEqual(len(range_res.content), 100)
        self.assertEqual(range_res.headers.get("content-range"), f"bytes 0-99/{len(content)}")
