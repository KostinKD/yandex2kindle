"""Check the public demo artifact cannot accidentally publish private data."""
import tempfile
import unittest
from pathlib import Path
from scripts.build_demo import build, SOURCES


class DemoBuildTests(unittest.TestCase):
    def test_only_explicit_public_files_are_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'
            for source in SOURCES:
                path = root / source
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('public')
            for private in ('.env', 'data/configuration.sqlite', 'data/app.db', 'incoming/private.epub',
                            'app/auth.py', 'demo/leaked.key'):
                path = root / private
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('secret')
            output = build(Path(tmp) / 'site', root)
            actual = {p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()}
            self.assertEqual(actual, set(SOURCES.values()) | {'.nojekyll'})
            self.assertFalse(any(p.read_text() == 'secret' for p in output.rglob('*') if p.is_file()))

    def test_nonempty_output_is_rejected_instead_of_uploading_leftovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '.env'
            path.write_text('private')
            with self.assertRaises(ValueError):
                build(tmp)
            self.assertEqual(path.read_text(), 'private')

    def test_symlinks_are_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'
            private = Path(tmp) / 'private'
            private.write_text('private')
            for source in SOURCES:
                path = root / source
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(private)
            with self.assertRaises(ValueError):
                build(Path(tmp) / 'site', root)
