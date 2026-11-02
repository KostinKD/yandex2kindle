import asyncio
import os
import sys
import tempfile
import unittest
from contextlib import suppress
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
from app import main, watcher


class WatcherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch_db = patch.object(main, "DB", self.root / "app.db")
        self.patch_incoming = patch.object(main, "INCOMING", self.root / "incoming")
        self.patch_db.start()
        self.patch_incoming.start()
        main.INCOMING.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.patch_db.stop()
        self.patch_incoming.stop()
        self.temp.cleanup()

    async def test_scan_incoming_once_imports_and_deletes_file(self):
        book_file = main.INCOMING / "test.epub"
        book_file.write_bytes(b"sample epub data")

        with patch("app.main.subprocess.run", return_value=Mock(returncode=0, stdout="Added book ids: 1")):
            results = await watcher.scan_incoming_once()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "added")
        self.assertFalse(book_file.exists())  # Successfully imported file must be removed

        with main.connect() as db:
            count = db.execute("SELECT count(*) FROM books").fetchone()[0]
            self.assertEqual(count, 1)

    async def test_watch_incoming_loop_waits_for_stabilization(self):
        # We simulate the loop iteration
        book_file = main.INCOMING / "copying.epub"
        book_file.write_bytes(b"first chunk")

        added_calls = []
        def mock_add(path):
            added_calls.append(path.name)
            return {"status": "added", "id": 1}

        env = {"INCOMING_WATCH_INTERVAL_SECONDS": "1"}
        with patch.dict(os.environ, env):
            with patch("app.main.add_file", side_effect=mock_add):
                # Run watcher task briefly
                task = asyncio.create_task(watcher.watch_incoming_loop())
                
                # Check 1: loop sees file, records initial size
                await asyncio.sleep(0.1)
                self.assertEqual(len(added_calls), 0)

                # Still copy: size changes
                book_file.write_bytes(b"first chunk + second chunk")
                await asyncio.sleep(1.1)
                self.assertEqual(len(added_calls), 0)

                # Stabilized: size stays the same over interval
                await asyncio.sleep(1.2)
                self.assertEqual(len(added_calls), 1)
                self.assertEqual(added_calls[0], "copying.epub")

                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
