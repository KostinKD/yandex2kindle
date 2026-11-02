"""Extract, cache, and serve real book covers from EPUB and FB2 files."""
import base64
import logging
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from fastapi import APIRouter, HTTPException, Response

router = APIRouter(tags=["Book covers"])
logger = logging.getLogger("uvicorn.error")

MIME_SIGNATURES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}


def detect_image_type(data: bytes) -> str | None:
    for signature, mime in MIME_SIGNATURES.items():
        if data.startswith(signature):
            return mime
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def extract_cover_epub(file_path: Path) -> bytes | None:
    try:
        with zipfile.ZipFile(file_path) as z:
            opf_path = None
            try:
                container = z.read("META-INF/container.xml")
                c_root = ET.fromstring(container)
                ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
                elem = c_root.find(".//c:rootfile", ns)
                if elem is not None:
                    opf_path = elem.get("full-path")
            except Exception:
                pass

            if not opf_path:
                opfs = [name for name in z.namelist() if name.lower().endswith(".opf")]
                opf_path = opfs[0] if opfs else None

            if not opf_path or opf_path not in z.namelist():
                return None

            opf_dir = Path(opf_path).parent.as_posix()
            if opf_dir == ".":
                opf_dir = ""

            opf_root = ET.fromstring(z.read(opf_path))

            cover_id = None
            for meta in opf_root.findall(".//{*}meta"):
                if meta.get("name") == "cover":
                    cover_id = meta.get("content")
                    break

            cover_href = None
            if cover_id:
                for item in opf_root.findall(".//{*}item"):
                    if item.get("id") == cover_id:
                        cover_href = item.get("href")
                        break

            if not cover_href:
                for item in opf_root.findall(".//{*}item"):
                    if "cover-image" in (item.get("properties") or "").split():
                        cover_href = item.get("href")
                        break

            if not cover_href:
                for item in opf_root.findall(".//{*}item"):
                    media = (item.get("media-type") or "").lower()
                    item_id = (item.get("id") or "").lower()
                    href = (item.get("href") or "").lower()
                    if media.startswith("image/") and ("cover" in item_id or "cover" in href):
                        cover_href = item.get("href")
                        break

            if not cover_href:
                return None

            target_path = f"{opf_dir}/{cover_href}".lstrip("/") if opf_dir else cover_href
            normalized_target = None
            for name in z.namelist():
                if name.lower() == target_path.lower():
                    normalized_target = name
                    break

            if not normalized_target:
                target_name = Path(cover_href).name.lower()
                for name in z.namelist():
                    if Path(name).name.lower() == target_name:
                        normalized_target = name
                        break

            if not normalized_target:
                return None

            data = z.read(normalized_target)
            return data if detect_image_type(data) else None
    except Exception as exc:
        logger.debug("Could not extract EPUB cover: %s", exc)
        return None


def extract_cover_fb2(file_path: Path) -> bytes | None:
    try:
        with open(file_path, "rb") as f:
            content = f.read(15 * 1024 * 1024)

        match = re.search(rb'<binary[^>]*id=["\'](?:cover|cover\.jpg|cover\.png|cover\.jpeg)["\'][^>]*>(.*?)</binary>', content, re.DOTALL | re.IGNORECASE)
        if not match:
            match = re.search(rb'<binary[^>]*>(.*?)</binary>', content, re.DOTALL | re.IGNORECASE)
        if match:
            clean_b64 = re.sub(rb'\s+', b'', match.group(1).strip())
            data = base64.b64decode(clean_b64)
            return data if detect_image_type(data) else None
    except Exception as exc:
        logger.debug("Could not extract FB2 cover: %s", exc)
        return None


def extract_cover_bytes(file_path: Path) -> bytes | None:
    suffix = file_path.suffix.lower()
    if suffix == ".epub":
        return extract_cover_epub(file_path)
    if suffix == ".fb2":
        return extract_cover_fb2(file_path)
    return None


def covers_dir() -> Path:
    from . import main
    cdir = main.library_db().parent / "covers"
    cdir.mkdir(parents=True, exist_ok=True)
    return cdir


def save_cover_if_present(source_path: Path, digest: str) -> bool:
    try:
        data = extract_cover_bytes(source_path)
        if data:
            target = covers_dir() / f"{digest}.img"
            target.write_bytes(data)
            return True
    except Exception as exc:
        logger.debug("Error saving cover for %s: %s", digest, exc)
    return False


def get_cover_data(book) -> tuple[bytes, str] | None:
    digest = book["sha256"]
    cache_path = covers_dir() / f"{digest}.img"
    if cache_path.is_file():
        data = cache_path.read_bytes()
        mime = detect_image_type(data) or "image/jpeg"
        return data, mime

    # Try on-demand extraction from book original file
    source = Path(book["filename"])
    if source.is_file():
        data = extract_cover_bytes(source)
        if data:
            cache_path.write_bytes(data)
            mime = detect_image_type(data) or "image/jpeg"
            return data, mime
    return None


@router.get("/covers/{book_id}")
def serve_cover(book_id: int):
    from . import main
    with main.connect() as db:
        book = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        raise HTTPException(404, "Книга не найдена")

    result = get_cover_data(book)
    if not result:
        raise HTTPException(404, "Обложка отсутствует")

    data, mime = result
    return Response(content=data, media_type=mime, headers={
        "Cache-Control": "public, max-age=604800, immutable",
        "Content-Disposition": f'inline; filename="cover_{book_id}.jpg"',
    })
