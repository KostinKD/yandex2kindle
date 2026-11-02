"""On-demand EPUB export using the local Calibre installation."""
import os
from pathlib import Path
import subprocess
import tempfile
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

LOCK = threading.Lock()
router = APIRouter(tags=["EPUB export"])


def ensure_epub_file(book) -> tuple[Path, str]:
    from . import main
    from .kosync import export_name
    if not book or not Path(book["filename"]).is_file():
        raise HTTPException(404, "Книга или исходный файл не найдены")
    filename = str(Path(export_name(book)).with_suffix(".epub"))
    source = Path(book["filename"])
    if source.suffix.lower() == ".epub":
        return source, filename
    # Imported originals are immutable and their SHA-256 identifies cached output.
    key = main.hashlib.sha256(book["sha256"].encode()).hexdigest()
    folder = main.library_db().parent / "converted"
    target = folder / (key + ".epub")
    with LOCK:
        if not target.is_file():
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="epub-", dir=folder) as temporary:
                output = Path(temporary) / "book.epub"
                try:
                    result = subprocess.run(["ebook-convert", str(source), str(output),
                        "--title", book["title"], "--authors", book["author"] or "Неизвестный автор"],
                        capture_output=True, text=True, timeout=180)
                except FileNotFoundError:
                    raise HTTPException(503, "Не найден ebook-convert: установите Calibre или используйте Docker-образ проекта") from None
                except subprocess.TimeoutExpired:
                    raise HTTPException(504, "Локальная конвертация EPUB не завершилась за 180 секунд") from None
                if result.returncode or not output.is_file() or not output.stat().st_size:
                    raise HTTPException(422, "Calibre не смог преобразовать этот файл в EPUB")
                if output.stat().st_size > main.MAX_FILE_SIZE:
                    raise HTTPException(413, "Полученный EPUB превышает 500 МБ")
                os.replace(output, target)
    from .kosync import partial_md5
    with main.connect() as db:
        db.execute("INSERT OR REPLACE INTO book_documents(book_id,mode,document) VALUES(?,'epub',?)",
                   (book['id'], partial_md5(target)))
    return target, filename


@router.get("/download/{book_id}/epub")
def download_epub(book_id: int):
    from . import main
    with main.connect() as db:
        book = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        raise HTTPException(404, "Книга или исходный файл не найдены")
    path, filename = ensure_epub_file(book)
    return FileResponse(path, filename=filename, media_type="application/epub+zip")
