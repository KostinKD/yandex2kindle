"""Build the GitHub Pages demo from an explicit allowlist, without app data."""
import argparse
from pathlib import Path
from shutil import copyfile

ROOT = Path(__file__).resolve().parent.parent
SOURCES = {
    'demo/index.html': 'index.html',
    'demo/demo.js': 'demo.js',
    'demo/demo.css': 'demo.css',
    'app/static/app.css': 'assets/app.css',
    'app/static/workspace.css': 'assets/workspace.css',
    'app/static/favicon.svg': 'assets/favicon.svg',
    'LICENSE': 'LICENSE',
}


def build(output, root=ROOT):
    output = Path(output).resolve()
    # Never mix the public artifact with an existing folder or private data.
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output directory must be empty; choose a new directory.')
    output.mkdir(parents=True, exist_ok=True)
    for source, target in SOURCES.items():
        path = Path(root) / source
        if path.is_symlink():
            raise ValueError(f'Symlink is not allowed: {source}')
        destination = output / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        copyfile(path, destination)
    (output / '.nojekyll').touch()
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '_site')
    print(build(parser.parse_args().output))
