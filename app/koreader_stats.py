"""Receive KOReader's statistics database without executing uploaded SQL/views."""
import hashlib
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
import xml.etree.ElementTree as ET

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

router = APIRouter(tags=["KOReader statistics"])
MAX_UPLOAD = 64 * 1024 * 1024
MAX_RECORDS = 1_000_000
# Leave room for duration, timezone conversion and next-midnight calculations.
MAX_TIMESTAMP = int(datetime(9998, 1, 1, tzinfo=timezone.utc).timestamp())
FILE = "statistics.sqlite3"
METHODS = "OPTIONS, PROPFIND, GET, HEAD, PUT, DELETE"


def invalid():
    return HTTPException(422, "Нужна корректная база статистики KOReader: statistics.sqlite3")


def integer(value, low, high):
    if not isinstance(value, int) or not low <= value <= high:
        raise invalid()
    return value


def parse_database(content):
    if not content.startswith(b"SQLite format 3\x00"):
        raise invalid()
    # An immutable, read-only temporary snapshot cannot reference adjacent WALs.
    with tempfile.TemporaryDirectory(prefix="y2k-statistics-") as directory:
        path = Path(directory) / FILE
        path.write_bytes(content)
        db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
        # Bound values inside SQLite, before expressions/schema checks allocate them.
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 65536)
        deadline = time.monotonic() + 10
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        try:
            db.execute("PRAGMA trusted_schema=OFF")
            objects = dict(db.execute("SELECT name,type FROM sqlite_master WHERE name IN ('book','page_stat_data')"))
            if objects != {"book": "table", "page_stat_data": "table"}:
                raise invalid()
            for table in ("book", "page_stat_data"):
                if any(column[6] for column in db.execute(f"PRAGMA table_xinfo({table})")):
                    raise invalid()
            if db.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
                raise invalid()
            books = {}
            for source_id, title, author, document in db.execute("SELECT id,title,authors,md5 FROM book LIMIT 10001"):
                if len(books) >= 10000 or not isinstance(source_id, int):
                    raise invalid()
                if not isinstance(title, str) or not title.strip() or len(title) > 4000:
                    raise invalid()
                if author is None:
                    author = ""
                if document is None:
                    document = ""
                if not isinstance(author, str) or len(author) > 4000 or not isinstance(document, str) or len(document) > 128:
                    raise invalid()
                # Match KOReader's identity: title + authors + md5, independent of local id.
                import json
                identity = hashlib.sha256(json.dumps([title, author, document], ensure_ascii=False).encode()).hexdigest()
                books[source_id] = (identity, title, author, document)
            records = []
            for count, (source_id, page, stamp, duration, total) in enumerate(db.execute(
                    "SELECT id_book,page,start_time,duration,total_pages FROM page_stat_data LIMIT 1000001")):
                if count >= MAX_RECORDS or source_id not in books:
                    raise invalid()
                integer(page, 0, 10_000_000)
                integer(stamp, 0, MAX_TIMESTAMP)
                integer(duration, 0, 86400)
                integer(total, 0, 10_000_000)
                if duration:
                    records.append((books[source_id][0], page, stamp, duration, total))
            return list(books.values()), records
        except (sqlite3.Error, ValueError, OverflowError):
            raise invalid() from None
        finally:
            db.close()


def match_books(db):
    from .library_state import normalize, authors_match
    library = db.execute("SELECT id,title,author FROM books").fetchall()
    documents = {}
    for row in db.execute("SELECT d.book_id,d.document FROM book_documents d JOIN books b ON b.id=d.book_id WHERE mode='binary'"):
        documents.setdefault(row["document"], set()).add(row["book_id"])
    by_title = {}
    for book in library:
        by_title.setdefault(normalize(book["title"]), []).append(book)
    for row in db.execute("SELECT * FROM koreader_stat_books").fetchall():
        candidates = documents.get(row["document"], set())
        if not candidates:
            candidates = {book["id"] for book in by_title.get(normalize(row["title"]), [])
                          if authors_match(row["author"], book["author"])}
        linked = next(iter(candidates)) if len(candidates) == 1 else None
        db.execute("UPDATE koreader_stat_books SET book_id=? WHERE identity=?", (linked, row["identity"]))


def check_preconditions(headers, snapshot):
    etag = snapshot["etag"] if snapshot else None
    if_match = headers.get("if-match")
    if if_match is not None:
        values = [value.strip() for value in if_match.split(",")]
        if not etag or ("*" not in values and etag not in values):
            raise HTTPException(412, "Версия базы изменилась. Повторите синхронизацию.")
    none_match = headers.get("if-none-match")
    if none_match is not None and etag:
        values = [value.strip().removeprefix("W/") for value in none_match.split(",")]
        if "*" in values or etag in values:
            raise HTTPException(412, "База уже существует")


def save_database(content, headers=None):
    from . import main, kosync
    books, records = parse_database(content)
    kosync.index_books()
    stamp = datetime.now(timezone.utc).isoformat()
    etag = '"' + hashlib.sha256(content).hexdigest() + '"'
    with main.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        snapshot = db.execute("SELECT etag FROM koreader_stat_snapshot WHERE id=1").fetchone()
        check_preconditions(headers or {}, snapshot)
        db.executemany("""INSERT INTO koreader_stat_books(identity,title,author,document) VALUES(?,?,?,?)
            ON CONFLICT(identity) DO UPDATE SET title=excluded.title,author=excluded.author,document=excluded.document""", books)
        db.executemany("""INSERT INTO koreader_page_stats VALUES(?,?,?,?,?)
            ON CONFLICT(identity,page,start_time) DO UPDATE SET
            duration=MAX(duration,excluded.duration),total_pages=excluded.total_pages""", records)
        match_books(db)
        db.execute("INSERT OR REPLACE INTO koreader_stat_snapshot VALUES(1,?,?,?)", (content, etag, stamp))
        db.execute("INSERT OR REPLACE INTO koreader_stat_import VALUES(1,?,?,?)", (stamp, len(books), len(records)))
    return {"books": len(books), "records": len(records), "etag": etag, "created": snapshot is None}


async def read_bounded(chunks):
    result = bytearray()
    async for chunk in chunks:
        result.extend(chunk)
        if len(result) > MAX_UPLOAD:
            raise HTTPException(413, "База статистики не должна превышать 64 МБ")
    return bytes(result)


@router.post("/api/stats/koreader/import")
async def import_statistics(request: Request, file: UploadFile = File(...)):
    async def chunks():
        while chunk := await file.read(1024 * 1024):
            yield chunk
    try:
        result = await run_in_threadpool(save_database, await read_bounded(chunks()))
    finally:
        await file.close()
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/stats?imported=1", status_code=303)
    return JSONResponse({"status": "ok", **result})


def properties(path, snapshot, collection=False):
    response = ET.Element("{DAV:}response")
    ET.SubElement(response, "{DAV:}href").text = path
    propstat = ET.SubElement(response, "{DAV:}propstat")
    prop = ET.SubElement(propstat, "{DAV:}prop")
    resource = ET.SubElement(prop, "{DAV:}resourcetype")
    if collection:
        ET.SubElement(resource, "{DAV:}collection")
    else:
        ET.SubElement(prop, "{DAV:}getcontentlength").text = str(len(snapshot["content"]))
        ET.SubElement(prop, "{DAV:}getetag").text = snapshot["etag"]
        ET.SubElement(prop, "{DAV:}getlastmodified").text = modified(snapshot)
        ET.SubElement(prop, "{DAV:}getcontenttype").text = "application/octet-stream"
    ET.SubElement(propstat, "{DAV:}status").text = "HTTP/1.1 200 OK"
    return response


def modified(snapshot):
    return format_datetime(datetime.fromisoformat(snapshot["updated_at"]), usegmt=True)


def dav_response(method, path, headers, body=None):
    from . import main
    root = path in ("/webdav", "/webdav/")
    if not root and path != "/webdav/" + FILE:
        raise HTTPException(404, "Доступен только файл statistics.sqlite3 в корне WebDAV")
    dav_headers = {"DAV": "1", "Allow": METHODS}
    if method == "OPTIONS":
        return Response(headers=dav_headers)
    if method == "PUT":
        if root:
            raise HTTPException(405, "Выберите файл statistics.sqlite3")
        result = save_database(body, headers)
        return Response(status_code=201 if result["created"] else 204,
                        headers={**dav_headers, "ETag": result["etag"]})
    with main.connect() as db:
        if method == "DELETE":
            db.execute("BEGIN IMMEDIATE")
        snapshot = db.execute("SELECT * FROM koreader_stat_snapshot WHERE id=1").fetchone()
        if not root and snapshot is None:
            raise HTTPException(404, "База ещё не загружена")
        if method == "PROPFIND":
            depth = headers.get("depth", "1")
            if depth not in ("0", "1"):
                raise HTTPException(403, "Поддерживается Depth: 0 или 1")
            multistatus = ET.Element("{DAV:}multistatus")
            multistatus.append(properties("/webdav/" if root else path, snapshot, root))
            if root and depth == "1" and snapshot:
                multistatus.append(properties("/webdav/" + FILE, snapshot))
            return Response(ET.tostring(multistatus, encoding="utf-8", xml_declaration=True),
                            status_code=207, media_type="application/xml", headers=dav_headers)
        if method in ("GET", "HEAD") and not root:
            response_headers = {**dav_headers, "ETag": snapshot["etag"], "Last-Modified": modified(snapshot),
                                "Content-Length": str(len(snapshot["content"]))}
            return Response(snapshot["content"] if method == "GET" else b"", media_type="application/octet-stream",
                            headers=response_headers)
        if method == "DELETE" and not root:
            check_preconditions(headers, snapshot)
            db.execute("DELETE FROM koreader_stat_snapshot WHERE id=1")
            return Response(status_code=204, headers=dav_headers)
    raise HTTPException(405, "Метод недоступен", headers={"Allow": METHODS})


@router.api_route("/webdav", methods=METHODS.split(", "))
@router.api_route("/webdav/{resource:path}", methods=METHODS.split(", "))
async def webdav(request: Request, resource: str = ""):
    body = await read_bounded(request.stream()) if request.method == "PUT" else None
    return await run_in_threadpool(dav_response, request.method, request.url.path, request.headers, body)
