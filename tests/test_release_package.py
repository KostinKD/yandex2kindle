import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
from scripts.package_release import build_archive


class ReleasePackageTests(unittest.TestCase):
    def test_package_excludes_keys_and_library_and_keeps_launcher_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ['Запустить.command', 'Запустить.bat', '.env.example', 'app/main.py', 'app/__pycache__/main.pyc',
                         '.env', 'data/setup.key', 'data/configuration.sqlite', 'incoming/private.epub']:
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('test')
            target = build_archive(root / 'release.zip', root)
            with ZipFile(target) as archive:
                names = archive.namelist()
                self.assertIn('Yandex2Kindle/app/main.py', names)
                self.assertIn('Yandex2Kindle/.env.example', names)
                for forbidden in ['.env','data/setup.key','data/configuration.sqlite','incoming/private.epub','app/__pycache__/main.pyc']:
                    self.assertNotIn('Yandex2Kindle/' + forbidden, names)
                self.assertTrue(archive.getinfo('Yandex2Kindle/Запустить.command').external_attr >> 16 & 0o111)
