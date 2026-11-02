"""Fingerprint packaged source files so stale containers can be identified."""
import hashlib
from pathlib import Path


def fingerprint():
    digest = hashlib.sha256()
    root = Path(__file__).parent
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in (".py", ".css", ".js", ".svg"):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]
