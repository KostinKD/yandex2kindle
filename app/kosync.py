"""KOReader wire protocol and mapping between document hashes and local books."""
import hashlib
import hmac
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from . import main

router = APIRouter(tags=["KOReader sync"])


class SyncError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


async def sync_error_handler(request, exc):
    return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status)


def export_name(book):
    name = re.sub(r'[\\/\x00-\x1f\x7f]', '_', book["title"]).strip()[:180] or "book"
    return name + Path(book["filename"]).suffix


def partial_md5(path):
    """KOReader LuaJIT bit.lshift uses 32-bit masked shift counts (including -2)."""
    digest = hashlib.md5()
    with Path(path).open("rb") as stream:
        for i in range(-1, 11):
            stream.seek((1024 << ((2 * i) & 31)) & 0xffffffff)
            data = stream.read(1024)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()


def index_books():
    with main.connect() as db:
        books = db.execute("""SELECT * FROM books WHERE id NOT IN
            (SELECT book_id FROM book_documents WHERE mode='binary')""").fetchall()
    for book in books:
        try:
            binary = partial_md5(book["filename"])
        except OSError:
            continue
        with main.connect() as db:
            for mode, document in (("binary", binary), ("filename", hashlib.md5(export_name(book).encode()).hexdigest())):
                db.execute("INSERT OR REPLACE INTO book_documents VALUES(?,?,?)", (book["id"], mode, document))


def password_hash(key, salt):
    return hashlib.pbkdf2_hmac("sha256", key.encode(), bytes.fromhex(salt), 260000).hex()


def authorize_db(db, username, key):
    row = db.execute("SELECT * FROM kosync_users WHERE username=?", (username,)).fetchone()
    if not row or not key or not hmac.compare_digest(row["key_hash"], password_hash(key, row["salt"])):
        raise SyncError(401, 2001, "Unauthorized")


def authorize(request: Request):
    username = request.headers.get("x-auth-user", "")
    key = request.headers.get("x-auth-key", "")
    if len(username) > 100 or len(key) > 1000:
        raise SyncError(401, 2001, "Unauthorized")
    with main.connect() as db:
        authorize_db(db, username, key)
    return username


async def body_as(request, model):
    try:
        return model.model_validate(await request.json())
    except (ValueError, ValidationError):
        raise SyncError(403, 2003, "Invalid request") from None


class Registration(BaseModel):
    username: str = Field(min_length=1, max_length=100, pattern=r"^[^:]+$")
    password: str = Field(min_length=1, max_length=1000)


@router.post("/users/create", status_code=201)
async def register(request: Request):
    if os.environ.get("KOSYNC_ALLOW_REGISTRATION", "true").lower() != "true":
        raise SyncError(403, 2005, "Registration disabled")
    value = await body_as(request, Registration)
    # The client supplies MD5(password); retain only a salted hash of that key.
    salt = os.urandom(16).hex()
    hashed = await main.run_in_threadpool(password_hash, value.password, salt)
    with main.connect() as db:
        try:
            db.execute("INSERT INTO kosync_users VALUES(?,?,?)", (value.username, salt, hashed))
        except sqlite3.IntegrityError:
            raise SyncError(402, 2002, "User already exists") from None
    return {"username": value.username}


@router.get("/users/auth")
def auth(username=Depends(authorize)):
    return {"authorized": "OK"}


class SyncProgress(BaseModel):
    document: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_]+$")
    progress: str = Field(max_length=10000)
    percentage: float = Field(ge=0, le=1, allow_inf_nan=False)
    device: str = Field(min_length=1, max_length=500)
    device_id: str = Field(default="", max_length=500)


@router.put("/syncs/progress")
async def update_progress(request: Request, username=Depends(authorize)):
    value = await body_as(request, SyncProgress)
    await main.run_in_threadpool(index_books)
    return await main.run_in_threadpool(store_progress, request, username, value)


def store_progress(request, username, value):
    stamp = int(time.time())
    with main.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        authorize_db(db, username, request.headers.get("x-auth-key", ""))
        db.execute("""INSERT INTO kosync_progress VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(username,document) DO UPDATE SET progress=excluded.progress,
            percentage=excluded.percentage, device=excluded.device,
            device_id=excluded.device_id, timestamp=excluded.timestamp""",
            (username, value.document, value.progress, value.percentage, value.device, value.device_id, stamp))
        from .auth import sync_username
        if username == sync_username():
            matches = db.execute("SELECT DISTINCT book_id FROM book_documents WHERE document=?", (value.document,)).fetchall()
            # Never guess when different local files share a filename/partial digest.
            if len(matches) == 1:
                db.execute("""INSERT INTO book_device_state(book_id,reader_seen_at) VALUES(?,?)
                    ON CONFLICT(book_id) DO UPDATE SET reader_seen_at=excluded.reader_seen_at""",
                    (matches[0][0], datetime.now(timezone.utc).isoformat()))
                db.execute("""INSERT INTO reading_progress VALUES(?,?,?,?)
                    ON CONFLICT(book_id) DO UPDATE SET progress=excluded.progress,
                    position=excluded.position, updated_at=excluded.updated_at""",
                    (matches[0][0], value.percentage, value.progress, datetime.now(timezone.utc).isoformat()))
    return {"document": value.document, "timestamp": stamp}


@router.get("/syncs/progress/{document}")
def read_progress(document: str, username=Depends(authorize)):
    with main.connect() as db:
        row = db.execute("SELECT * FROM kosync_progress WHERE username=? AND document=?", (username, document)).fetchone()
    return {key: row[key] for key in ("document", "progress", "percentage", "device", "device_id", "timestamp")} if row else {}


def publish_local(db, book_id, value):
    """A percentage alone cannot locate a page in KOReader: publish only real positions."""
    if not value.position.startswith('/'):
        return
    from .auth import sync_username
    username = sync_username()
    if not db.execute("SELECT 1 FROM kosync_users WHERE username=?", (username,)).fetchone():
        return
    documents = db.execute("SELECT document FROM book_documents WHERE book_id=?", (book_id,)).fetchall()
    for row in documents:
        if db.execute("SELECT COUNT(DISTINCT book_id) FROM book_documents WHERE document=?", (row[0],)).fetchone()[0] != 1:
            continue
        db.execute("""INSERT INTO kosync_progress VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(username,document) DO UPDATE SET progress=excluded.progress,
            percentage=excluded.percentage, device=excluded.device,
            device_id=excluded.device_id, timestamp=excluded.timestamp""",
            (username, row[0], value.position, value.progress, "Yandex2Kindle", "web", int(time.time())))


@router.get("/api/books/{book_id}/documents")
def document_ids(book_id: int):
    main.get_progress(book_id)
    index_books()
    with main.connect() as db:
        return [dict(row) for row in db.execute("SELECT mode,document FROM book_documents WHERE book_id=?", (book_id,))]


@router.get("/healthcheck")
def healthcheck():
    return {"state": "OK"}
