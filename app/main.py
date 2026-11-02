"""Local Calibre importer and OPDS 1.2 feed for KOReader."""
from __future__ import annotations

import hashlib
import asyncio
import logging
import html
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET
from contextlib import contextmanager, asynccontextmanager, suppress
from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from . import __version__, ui
from .build import fingerprint

BUILD_ID = fingerprint()

BASE = Path(os.environ.get("LIBRARY_DIR", "/data/calibre"))
DB = Path(os.environ.get("DATABASE_PATH", "/data/app.db"))
INCOMING = Path(os.environ.get("INCOMING_DIR", "/incoming"))
SUPPORTED = {".epub", ".fb2", ".pdf", ".mobi", ".azw3", ".txt", ".mp3", ".m4b"}
LOCK = threading.Lock()
MAX_FILE_SIZE = 500 * 1024 * 1024
@asynccontextmanager
async def lifespan(app):
    from .setup import ensure_key
    await asyncio.to_thread(ensure_key)
    from .integrations import scheduled_sync
    from .library_state import reconcile_library
    await asyncio.to_thread(reconcile_library)
    logging.getLogger("uvicorn.error").info("Yandex2Kindle build=%s remote_connect_timeout=%ss remote_trust_env=%s", BUILD_ID,
        os.environ.get("REMOTE_CONNECT_TIMEOUT_SECONDS", "60"), os.environ.get("REMOTE_TRUST_ENV", "true"))
    task = asyncio.create_task(scheduled_sync())
    from .watcher import watch_incoming_loop
    watcher_task = asyncio.create_task(watch_incoming_loop())
    from .telegram_bot import run_telegram_bot
    telegram_task = asyncio.create_task(run_telegram_bot())
    try:
        yield
    finally:
        task.cancel()
        watcher_task.cancel()
        telegram_task.cancel()
        with suppress(asyncio.CancelledError):
            await asyncio.gather(task, watcher_task, telegram_task, return_exceptions=True)


app = FastAPI(title="Yandex2Kindle", version=__version__, docs_url="/docs", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/manifest.webmanifest")
def pwa_manifest():
    return FileResponse(Path(__file__).parent / "static" / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(Path(__file__).parent / "static" / "sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})


@contextmanager
def connect():
    path = library_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=30)
    c.row_factory = sqlite3.Row
    from .schema import migrate
    migrate(c)
    c.execute("""CREATE TABLE IF NOT EXISTS books (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '',
        filename TEXT NOT NULL, sha256 TEXT UNIQUE NOT NULL,
        calibre_id INTEGER, created_at TEXT NOT NULL,
        series TEXT NOT NULL DEFAULT '', series_index REAL NOT NULL DEFAULT 0,
        description TEXT NOT NULL DEFAULT '', genres TEXT NOT NULL DEFAULT '',
        year INTEGER, isbn TEXT NOT NULL DEFAULT '',
        rating INTEGER NOT NULL DEFAULT 0, review TEXT NOT NULL DEFAULT ''
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS wishlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'manual',
        UNIQUE(title, author, source)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS reading_progress (
        book_id INTEGER PRIMARY KEY REFERENCES books(id),
        progress REAL NOT NULL CHECK(progress BETWEEN 0 AND 1),
        position TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
    )""")
    c.commit()
    try:
        with c:
            yield c
    finally:
        c.close()


def clean_name(name: str) -> str:
    return Path(name).name.replace("\x00", "")[:200]


def library_db():
    from .auth import current_user
    user = current_user.get()
    return DB.parent / "users" / str(user["id"]) / "app.db" if user and not user["owner"] else DB


def library_base():
    return BASE if library_db() == DB else library_db().parent / "calibre"


def library_incoming():
    return INCOMING if library_db() == DB else library_db().parent / "incoming"


def add_file(source: Path, title: str = "", author: str = "", series: str = "", series_index: float = 0.0,
             description: str = "", genres: str = "", year: int | None = None, isbn: str = ""):
    """Serialize local calibredb writes and store original for direct OPDS download."""
    if source.suffix.lower() not in SUPPORTED:
        raise ValueError("Неподдерживаемый формат")
    if not 0 < source.stat().st_size <= MAX_FILE_SIZE:
        raise ValueError("Файл должен быть непустым и не превышать 500 МБ")
    with LOCK:
        digest_hash = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest_hash.update(chunk)
        digest = digest_hash.hexdigest()
        with connect() as db:
            existing = db.execute("SELECT id FROM books WHERE sha256=?", (digest,)).fetchone()
            if existing:
                from .library_state import reconcile
                reconcile(db)
                return {"status": "duplicate", "id": existing["id"]}
        library_base().mkdir(parents=True, exist_ok=True)
        (library_db().parent / "originals").mkdir(exist_ok=True, parents=True)
        from .metadata import extract_book_metadata
        meta = extract_book_metadata(source)
        title = title.strip() or meta.get("title") or source.stem
        series = series.strip() or meta.get("series") or ""
        try:
            series_index = float(series_index or meta.get("series_index") or 0.0)
        except (ValueError, TypeError):
            series_index = 0.0
        author = author.strip() or meta.get("author") or ""
        description = description.strip() or meta.get("description") or ""
        genres = genres.strip() or meta.get("genres") or ""
        year = year if year is not None else meta.get("year")
        isbn = isbn.strip() or meta.get("isbn") or ""
        cmd = ["calibredb", "add", "--with-library", str(library_base()), "--duplicates", str(source)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except FileNotFoundError as exc:
            raise RuntimeError("Не найден calibredb. Установите Calibre или запустите сервис через Docker.") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Calibre не завершил импорт за 120 секунд") from exc
        if result.returncode != 0:
            raise RuntimeError("Calibre: " + (result.stderr or result.stdout)[-500:])
        # Parse Calibre's localized CLI output only as optional metadata.
        import re
        match = re.search(r"(?:Added book ids:|Added book ids|Добавлены книги:)\s*([0-9, ]+)", result.stdout)
        calibre_id = int(match.group(1).split(",")[0].strip()) if match else None
        suffix = source.suffix.lower()
        original = library_db().parent / "originals" / f"{digest}{suffix}"
        shutil.copyfile(source, original)
        from .covers import save_cover_if_present
        save_cover_if_present(original, digest)
        try:
            with connect() as db:
                cursor = db.execute(
                    "INSERT INTO books(title,author,filename,sha256,calibre_id,created_at,series,series_index,description,genres,year,isbn) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (title, author, str(original), digest, calibre_id, datetime.now(timezone.utc).isoformat(), series, series_index, description, genres, year, isbn),
                )
                from .library_state import reconcile
                reconcile(db)
                db.commit()
                return {"status": "added", "id": cursor.lastrowid, "calibre_id": calibre_id}
        except Exception:
            original.unlink(missing_ok=True)
            raise


def esc(s):
    return html.escape(str(s), quote=True)


def book_filter(q: str):
    # casefold handles Cyrillic, unlike SQLite's default LIKE/NOCASE.
    needle = q.strip().casefold()
    return (" WHERE instr(casefold(title), ?) > 0 OR instr(casefold(author), ?) > 0 OR instr(casefold(series), ?) > 0 OR instr(casefold(genres), ?) > 0 OR instr(casefold(description), ?) > 0",
            (needle, needle, needle, needle, needle)) if needle else ("", ())


def list_books(db, q: str, limit: int, offset: int, shelf_id: int | None = None, **filters):
    from .catalog import list_books as query_books
    return query_books(db, q, limit, offset, shelf_id, **filters)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, q: str = Query("", max_length=500), page: int = Query(1, ge=1),
         wish_status: str = Query("all", pattern="^(all|wanted|in_library|on_kindle|in_koreader|reading|finished)$"),
         shelf: int | None = Query(None, ge=1),
         format: str = Query('', pattern='^(|EPUB|FB2|PDF|MOBI|AZW3|TXT|MP3|M4B)$'),
         status: str = Query('all', pattern='^(all|reading|finished|unread)$'),
         sort: str = Query('newest', pattern='^(newest|title|author|series|progress)$'),
         view: str = Query('grid', pattern='^(grid|list)$'),
         author: str = Query('', max_length=500), series: str = Query('', max_length=500)):
    from . import catalog, library_ui
    if 'wish_status' in request.query_params:
        return RedirectResponse('/wishlist?' + ui.urlencode({'status': wish_status}), status_code=303)
    filters = dict(q=q, shelf=shelf, format=format, status=status, sort=sort, view=view, author=author, series=series)
    with connect() as db:
        books, total = list_books(db, q, 50, (page - 1) * 50, shelf_id=shelf,
                                  format=format, status=status, sort=sort, author=author, series=series)
        count = db.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        from .shelves import get_all_shelves
        return library_ui.catalog_page(books, total, count, page, filters, get_all_shelves(db), catalog.facets(db))


@app.get("/api/connection")
def connection(request: Request):
    return ui.connection_info(request)


@app.post("/upload")
async def upload(file: UploadFile = File(...), title: str = Form(""), author: str = Form("")):
    name = clean_name(file.filename or "")
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(400, "Поддерживаются: " + ", ".join(sorted(SUPPORTED)))
    # Keep incomplete uploads outside the folder scanned by /scan.
    library_db().parent.mkdir(parents=True, exist_ok=True)
    handle, path = tempfile.mkstemp(suffix=suffix, dir=library_db().parent)
    os.close(handle)
    dest = Path(path)
    try:
        size = 0
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    raise HTTPException(413, "Максимум 500 МБ")
                out.write(chunk)
        try:
            await run_in_threadpool(add_file, dest, title or Path(name).stem, author)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc
    finally:
        dest.unlink(missing_ok=True)
        await file.close()
    return RedirectResponse("/", status_code=303)


@app.post("/scan")
def scan():
    """Import files deposited into /incoming; remove after successful import."""
    incoming = library_incoming()
    incoming.mkdir(parents=True, exist_ok=True)
    outcome = []
    for p in sorted(incoming.iterdir()):
        if p.is_file() and p.suffix.lower() in SUPPORTED:
            try:
                result = add_file(p)
                p.unlink()
                outcome.append({"file": p.name, **result})
            except Exception as exc:
                outcome.append({"file": p.name, "status": "error", "message": str(exc)})
    return outcome


@app.post("/wishlist")
def wishlist(title: str = Form(...), author: str = Form(""), source: str = Form("manual")):
    if not title.strip():
        raise HTTPException(422, "Название не может быть пустым")
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO wishlist(title,author,source) VALUES(?,?,?)", (title.strip(), author.strip(), source.strip()))
        from .library_state import reconcile
        reconcile(db)
        db.commit()
    return RedirectResponse("/wishlist", status_code=303)


@app.get("/api/books")
def api_books(q: str = Query("", max_length=500), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
              shelf: int | None = Query(None, ge=1),
              format: str = Query('', pattern='^(|EPUB|FB2|PDF|MOBI|AZW3|TXT|MP3|M4B)$'),
              status: str = Query('all', pattern='^(all|reading|finished|unread)$'),
              sort: str = Query('newest', pattern='^(newest|title|author|series|progress)$'),
              author: str = Query('', max_length=500), series: str = Query('', max_length=500)):
    with connect() as db:
        rows, _ = list_books(db, q, limit, offset, shelf, format=format, status=status,
                              sort=sort, author=author, series=series)
        return [{key: row[key] for key in ("id", "title", "author", "created_at", "series", "series_index")} for row in rows]


class WishUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=1000)
    author: str = Field(default="", max_length=1000)


def edit_wish(wish_id: int, title: str, author: str):
    if not title.strip():
        raise HTTPException(422, "Название не может быть пустым")
    with connect() as db:
        try:
            cursor = db.execute("UPDATE wishlist SET title=?,author=? WHERE id=?", (title.strip(), author.strip(), wish_id))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "Такая книга уже есть в списке") from exc
        if not cursor.rowcount:
            raise HTTPException(404, "Не найдено")
        db.execute("DELETE FROM wishlist_books WHERE wish_id=? AND method='automatic'", (wish_id,))
        from .library_state import reconcile
        reconcile(db)


@app.patch("/api/wishlist/{wish_id}")
def update_wish(wish_id: int, value: WishUpdate):
    edit_wish(wish_id, value.title, value.author)
    return {"status": "updated", "id": wish_id}


@app.post("/wishlist/{wish_id}/edit")
def edit_wish_form(wish_id: int, title: str = Form(...), author: str = Form("")):
    edit_wish(wish_id, title, author)
    return RedirectResponse("/wishlist", status_code=303)


@app.delete("/api/wishlist/{wish_id}", status_code=204)
def delete_wish(wish_id: int):
    with connect() as db:
        if not db.execute("DELETE FROM wishlist WHERE id=?", (wish_id,)).rowcount:
            raise HTTPException(404, "Не найдено")
        db.execute("DELETE FROM wishlist_books WHERE wish_id=?", (wish_id,))
        db.execute("DELETE FROM wishlist_match_exclusions WHERE wish_id=?", (wish_id,))
    return Response(status_code=204)


@app.post("/wishlist/{wish_id}/delete")
def delete_wish_form(wish_id: int):
    delete_wish(wish_id)
    return RedirectResponse("/wishlist", status_code=303)


class ProgressUpdate(BaseModel):
    progress: float = Field(ge=0, le=1, allow_inf_nan=False)
    position: str = Field(default="", max_length=10000)


@app.get("/api/books/{book_id}/progress")
def get_progress(book_id: int):
    with connect() as db:
        if not db.execute("SELECT id FROM books WHERE id=?", (book_id,)).fetchone():
            raise HTTPException(404, "Не найдено")
        row = db.execute("SELECT * FROM reading_progress WHERE book_id=?", (book_id,)).fetchone()
    return dict(row) if row else {"book_id": book_id, "progress": 0, "position": "", "updated_at": None}


@app.put("/api/books/{book_id}/progress")
def put_progress(book_id: int, value: ProgressUpdate):
    from .kosync import index_books, publish_local
    index_books()
    with connect() as db:
        if not db.execute("SELECT id FROM books WHERE id=?", (book_id,)).fetchone():
            raise HTTPException(404, "Не найдено")
        db.execute("""INSERT INTO reading_progress(book_id,progress,position,updated_at) VALUES(?,?,?,?)
            ON CONFLICT(book_id) DO UPDATE SET progress=excluded.progress, position=excluded.position, updated_at=excluded.updated_at""",
            (book_id, value.progress, value.position, datetime.now(timezone.utc).isoformat()))
        publish_local(db, book_id, value)
        from .stats import record_reading_activity
        record_reading_activity(db, book_id, value.progress)
    return get_progress(book_id)


@app.post("/books/{book_id}/progress")
def progress_form(book_id: int, percent: float = Form(..., ge=0, le=100)):
    current = get_progress(book_id)
    put_progress(book_id, ProgressUpdate(progress=percent / 100, position=current["position"]))
    return RedirectResponse(f"/books/{book_id}", status_code=303)


@app.get("/api/wishlist")
def api_wishlist():
    with connect() as db:
        from .library_state import wishlist_rows
        return wishlist_rows(db)


@app.get("/download/{book_id}")
def download(book_id: int):
    with connect() as db:
        b = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not b:
        raise HTTPException(404, "Не найдено")
    p = Path(b["filename"])
    if not p.is_file():
        raise HTTPException(404, "Файл отсутствует")
    from .kosync import export_name
    return FileResponse(p, filename=export_name(b), media_type="application/octet-stream")


ATOM = "http://www.w3.org/2005/Atom"
OPDS = "http://opds-spec.org/2010/catalog"
ET.register_namespace("", ATOM)
ET.register_namespace("opds", OPDS)


@app.get("/opds")
def opds(request: Request, q: str = Query("", max_length=500), limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    from .auth import current_user
    user = current_user.get()
    with connect() as db:
        books, total = list_books(db, q, limit, offset)
    feed = ET.Element(f"{{{ATOM}}}feed")
    ET.SubElement(feed, f"{{{ATOM}}}id").text = f'urn:yandex2kindle:library:{user["id"] if user else "local"}'
    ET.SubElement(feed, f"{{{ATOM}}}title").text = f'Личная библиотека · {user["username"] if user else "Yandex2Kindle"}'
    ET.SubElement(feed, f"{{{ATOM}}}updated").text = datetime.now(timezone.utc).isoformat()
    ET.SubElement(feed, f"{{{ATOM}}}link", {"rel": "self", "href": str(request.url), "type": "application/atom+xml;profile=opds-catalog;kind=acquisition"})
    for rel, start in (("first", 0), ("previous", offset - limit), ("next", offset + limit)):
        if rel == "first" or (rel == "previous" and offset > 0) or (rel == "next" and start < total):
            url = request.url.replace_query_params(q=q, limit=limit, offset=max(0, start))
            ET.SubElement(feed, f"{{{ATOM}}}link", {"rel": rel, "href": str(url), "type": "application/atom+xml;profile=opds-catalog;kind=acquisition"})
    ET.SubElement(feed, f"{{{ATOM}}}link", {"rel": "search", "href": str(request.url_for("opensearch")), "type": "application/opensearchdescription+xml"})
    ET.SubElement(feed, f"{{{ATOM}}}link", {"rel": "start", "href": str(request.url_for("opds")), "type": "application/atom+xml;profile=opds-catalog;kind=acquisition"})
    mimes = {".epub":"application/epub+zip", ".fb2":"application/x-fictionbook+xml", ".pdf":"application/pdf", ".mobi":"application/x-mobipocket-ebook", ".azw3":"application/vnd.amazon.ebook", ".txt":"text/plain"}
    for b in books:
        entry = ET.SubElement(feed, f"{{{ATOM}}}entry")
        ET.SubElement(entry, f"{{{ATOM}}}id").text = f'urn:yandex2kindle:library:{user["id"] if user else "local"}:book:{b["id"]}'
        ET.SubElement(entry, f"{{{ATOM}}}title").text = b["title"]
        ET.SubElement(entry, f"{{{ATOM}}}updated").text = b["created_at"]
        author = ET.SubElement(entry, f"{{{ATOM}}}author")
        ET.SubElement(author, f"{{{ATOM}}}name").text = b["author"] or "Неизвестный автор"
        desc = b["description"] if "description" in b.keys() and b["description"] else ""
        if desc:
            ET.SubElement(entry, f"{{{ATOM}}}summary").text = desc
        ET.SubElement(entry, f"{{{ATOM}}}link", {"rel":"http://opds-spec.org/acquisition", "href": str(request.url_for("download", book_id=b["id"])), "type":mimes.get(Path(b["filename"]).suffix.lower(), "application/octet-stream")})
        ET.SubElement(entry, f"{{{ATOM}}}link", {"rel": "http://opds-spec.org/image", "href": str(request.url_for("serve_cover", book_id=b["id"])), "type": "image/jpeg"})
        ET.SubElement(entry, f"{{{ATOM}}}link", {"rel": "http://opds-spec.org/image/thumbnail", "href": str(request.url_for("serve_cover", book_id=b["id"])), "type": "image/jpeg"})
        if Path(b["filename"]).suffix.lower() != ".epub":
            ET.SubElement(entry, f"{{{ATOM}}}link", {"rel": "http://opds-spec.org/acquisition", "href": str(request.url_for("download_epub", book_id=b["id"])), "type": "application/epub+zip", "title": "EPUB (конвертация)"})
    return Response(ET.tostring(feed, encoding="utf-8", xml_declaration=True), media_type="application/atom+xml;profile=opds-catalog;kind=acquisition")


@app.get("/opds/search.xml")
def opensearch(request: Request):
    namespace = "http://a9.com/-/spec/opensearch/1.1/"
    root = ET.Element(f"{{{namespace}}}OpenSearchDescription")
    ET.SubElement(root, f"{{{namespace}}}ShortName").text = "Yandex2Kindle"
    ET.SubElement(root, f"{{{namespace}}}Description").text = "Поиск по названию и автору"
    ET.SubElement(root, f"{{{namespace}}}Url", {"type": "application/atom+xml;profile=opds-catalog;kind=acquisition", "template": str(request.url_for("opds")) + "?q={searchTerms}"})
    return Response(ET.tostring(root, encoding="utf-8", xml_declaration=True), media_type="application/opensearchdescription+xml")


@app.get("/health")
def health():
    return {"ok": True, "calibredb": bool(shutil.which("calibredb")), "build": BUILD_ID, "version": __version__}


@app.get("/plugins/yandex2kindle.koplugin.zip")
def download_koreader_plugin():
    import io
    import zipfile
    plugin_dir = Path(__file__).parent.parent / "plugins" / "yandex2kindle.koplugin"
    if not plugin_dir.is_dir():
        raise HTTPException(404, "Плагин не найден")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for file_path in plugin_dir.rglob("*"):
            if file_path.is_file():
                arcname = f"yandex2kindle.koplugin/{file_path.relative_to(plugin_dir)}"
                z.write(file_path, arcname)
    buffer.seek(0)
    headers = {"Content-Disposition": "attachment; filename=yandex2kindle.koplugin.zip"}
    return Response(buffer.getvalue(), media_type="application/zip", headers=headers)


from . import integrations, kosync, library_state, conversion, smtp_delivery, covers, reader, highlights, stats, shelves, audiobooks, koreader_stats

app.include_router(integrations.router)
app.include_router(kosync.router)
app.include_router(koreader_stats.router)
app.include_router(library_state.router)
app.include_router(conversion.router)
app.include_router(smtp_delivery.router)
app.include_router(covers.router)
app.include_router(reader.router)
app.include_router(highlights.router)
app.include_router(stats.router)
app.include_router(shelves.router)
app.include_router(audiobooks.router)
from . import setup, settings
app.include_router(setup.router)
app.include_router(settings.router)
from . import books, workspace
from . import annotation_sync
app.include_router(annotation_sync.router)
app.include_router(books.router)
app.include_router(workspace.router)
app.add_exception_handler(kosync.SyncError, kosync.sync_error_handler)

from . import auth
app.include_router(auth.router)
from . import kindle
app.include_router(kindle.router)
app.add_middleware(auth.PrivateLibraryMiddleware)
