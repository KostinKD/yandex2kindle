"""Build a shareable source bundle that never includes a personal library or keys."""
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
import argparse

ROOT = Path(__file__).resolve().parent.parent
FILES = ('Восстановить пароль.command', 'Восстановить пароль.bat', 'Запустить.command', 'Запустить.bat', 'НАЧНИТЕ ЗДЕСЬ.html', 'НАЧНИТЕ ЗДЕСЬ.md',
         'Dockerfile', 'docker-compose.yml', 'requirements.txt', '.env.example', 'README.md', 'LICENSE', 'NOTICE')


def build_archive(destination, root=ROOT):
    root, destination = Path(root), Path(destination)
    paths = [root / name for name in FILES if (root / name).is_file()]
    for folder in ('app', 'plugins', 'docs'):
        paths.extend(p for p in (root / folder).rglob('*') if p.is_file() and not p.is_symlink()
                     and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo'))
    paths.extend(root / 'scripts' / name for name in ('launch.sh','launch.ps1','start.sh') if (root / 'scripts' / name).is_file())
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, 'w', compression=ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            if path.is_symlink():
                continue
            relative = path.relative_to(root)
            item = ZipInfo('Yandex2Kindle/' + relative.as_posix(), date_time=(2026,1,1,0,0,0))
            item.create_system = 3
            item.external_attr = (0o100755 if path.suffix in ('.sh','.command') else 0o100644) << 16
            item.compress_type = ZIP_DEFLATED
            archive.writestr(item, path.read_bytes())
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / 'Yandex2Kindle.zip')
    print(build_archive(parser.parse_args().output))
