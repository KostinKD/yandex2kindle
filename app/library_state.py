"""Reconcile wishlist metadata with local files and explicit device confirmations."""
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

router = APIRouter(tags=["Library reconciliation"])


def now():
    return datetime.now(timezone.utc).isoformat()


def normalize(text):
    return " ".join(re.findall(r"\w+", text.casefold().replace("ё", "е")))


def authors_match(left, right):
    left, right = set(normalize(left).split()), set(normalize(right).split())
    if not left or not right:
        return False
    return left == right or (min(len(left), len(right)) >= 2 and (left <= right or right <= left))


def reconcile(db):
    """Only link a unique exact title + compatible author; retain manual decisions."""
    books = db.execute("SELECT id,title,author FROM books").fetchall()
    by_title = {}
    for book in books:
        by_title.setdefault(normalize(book["title"]), []).append(book)
    linked = 0
    wishes = db.execute("""SELECT w.* FROM wishlist w LEFT JOIN wishlist_books l
        ON l.wish_id=w.id LEFT JOIN wishlist_match_exclusions x ON x.wish_id=w.id
        WHERE l.wish_id IS NULL AND x.wish_id IS NULL""").fetchall()
    for wish in wishes:
        candidates = [book for book in by_title.get(normalize(wish["title"]), [])
                      if authors_match(wish["author"], book["author"])]
        if len(candidates) == 1:
            db.execute("INSERT INTO wishlist_books VALUES(?,?,?,?)", (wish["id"], candidates[0]["id"], "automatic", now()))
            linked += 1
    return linked


def device_states(db):
    return {row["book_id"]: dict(row) for row in db.execute("SELECT * FROM book_device_state")}


WISHLIST_JOIN = """ FROM wishlist w LEFT JOIN wishlist_books l ON l.wish_id=w.id
    LEFT JOIN books b ON b.id=l.book_id LEFT JOIN reading_progress p ON p.book_id=b.id
    LEFT JOIN book_device_state d ON d.book_id=b.id"""


def wishlist_filter(status):
    clauses = {
        'wanted': 'l.book_id IS NULL',
        'in_library': 'l.book_id IS NOT NULL',
        'on_kindle': 'd.kindle_confirmed_at IS NOT NULL',
        'reading': 'p.progress>0 AND p.progress<1',
        'finished': 'p.progress=1',
        'in_koreader': 'COALESCE(p.progress,0)=0 AND d.kindle_confirmed_at IS NULL AND d.reader_seen_at IS NOT NULL',
    }
    return ' WHERE ' + clauses[status] if status in clauses else ''


def wishlist_count(db, status='all'):
    return db.execute('SELECT COUNT(*)' + WISHLIST_JOIN + wishlist_filter(status)).fetchone()[0]


def wishlist_rows(db, status='all', limit=None, offset=0):
    query = """SELECT w.*, l.book_id, l.method AS link_method, l.linked_at,
        b.title AS book_title, b.author AS book_author,
        p.progress, d.kindle_confirmed_at, d.reader_seen_at, d.email_sent_at"""
    query += WISHLIST_JOIN + wishlist_filter(status) + ' ORDER BY w.id DESC'
    if limit is not None:
        query += ' LIMIT ? OFFSET ?'
    rows = db.execute(query, (limit, offset) if limit is not None else ()).fetchall()
    remote_states = {}
    for remote in db.execute("""SELECT e.wish_id,s.state FROM external_wishes e JOIN external_wish_state s
        ON s.source=e.source AND s.external_id=e.external_id"""):
        if remote["state"]:
            remote_states.setdefault(remote["wish_id"], set()).add(remote["state"])
    result = []
    for raw in rows:
        row = dict(raw)
        if row["book_id"] is None:
            state, label = "wanted", "Хочу прочитать"
        elif (row["progress"] or 0) >= 1:
            state, label = "finished", "Прочитано"
        elif (row["progress"] or 0) > 0:
            state, label = "reading", "Читаю"
        elif row["kindle_confirmed_at"]:
            state, label = "on_kindle", "На Kindle · отмечено вручную"
        elif row["reader_seen_at"]:
            state, label = "in_koreader", "Открыто в KOReader"
        else:
            state, label = "in_library", "В библиотеке"
        row.update(status=state, status_label=label)
        row["yandex_states"] = sorted(remote_states.get(row["id"], set()))
        result.append(row)
    return result


def main_module():
    from . import main
    return main


class LinkRequest(BaseModel):
    book_id: int


def link_book(db, wish_id, book_id):
    if not db.execute("SELECT 1 FROM wishlist WHERE id=?", (wish_id,)).fetchone():
        raise HTTPException(404, "Запись списка чтения не найдена")
    if not db.execute("SELECT 1 FROM books WHERE id=?", (book_id,)).fetchone():
        raise HTTPException(404, "Книга не найдена")
    db.execute("""INSERT INTO wishlist_books VALUES(?,?,?,?) ON CONFLICT(wish_id)
        DO UPDATE SET book_id=excluded.book_id, method=excluded.method, linked_at=excluded.linked_at""",
        (wish_id, book_id, "manual", now()))
    db.execute("DELETE FROM wishlist_match_exclusions WHERE wish_id=?", (wish_id,))


@router.post("/api/library/reconcile")
def reconcile_library():
    with main_module().connect() as db:
        count = reconcile(db)
    return {"linked": count}


@router.post("/library/reconcile")
def reconcile_form():
    reconcile_library()
    return RedirectResponse("/wishlist", status_code=303)


@router.put("/api/wishlist/{wish_id}/book")
def put_link(wish_id: int, value: LinkRequest):
    with main_module().connect() as db:
        link_book(db, wish_id, value.book_id)
    return {"wish_id": wish_id, "book_id": value.book_id, "method": "manual"}


@router.post("/wishlist/{wish_id}/book")
def link_form(wish_id: int, book_id: int = Form(...)):
    put_link(wish_id, LinkRequest(book_id=book_id))
    return RedirectResponse("/wishlist", status_code=303)


@router.delete("/api/wishlist/{wish_id}/book")
def unlink_book(wish_id: int):
    with main_module().connect() as db:
        if not db.execute("SELECT 1 FROM wishlist WHERE id=?", (wish_id,)).fetchone():
            raise HTTPException(404, "Запись списка чтения не найдена")
        db.execute("DELETE FROM wishlist_books WHERE wish_id=?", (wish_id,))
        db.execute("INSERT OR IGNORE INTO wishlist_match_exclusions VALUES(?)", (wish_id,))
    return {"wish_id": wish_id, "book_id": None, "automatic_matching_disabled": True}


@router.post("/wishlist/{wish_id}/unlink")
def unlink_form(wish_id: int):
    unlink_book(wish_id)
    return RedirectResponse("/wishlist", status_code=303)


class KindleConfirmation(BaseModel):
    on_device: bool


@router.put("/api/books/{book_id}/kindle")
def confirm_kindle(book_id: int, value: KindleConfirmation):
    with main_module().connect() as db:
        if not db.execute("SELECT 1 FROM books WHERE id=?", (book_id,)).fetchone():
            raise HTTPException(404, "Книга не найдена")
        db.execute("""INSERT INTO book_device_state(book_id,kindle_confirmed_at) VALUES(?,?)
            ON CONFLICT(book_id) DO UPDATE SET kindle_confirmed_at=excluded.kindle_confirmed_at""",
            (book_id, now() if value.on_device else None))
        row = db.execute("SELECT * FROM book_device_state WHERE book_id=?", (book_id,)).fetchone()
    return {**dict(row), "confirmation_method": "manual", "delivery_verified": False}


@router.post("/books/{book_id}/kindle")
def kindle_form(book_id: int, on_device: bool = Form(...)):
    confirm_kindle(book_id, KindleConfirmation(on_device=on_device))
    return RedirectResponse(f"/books/{book_id}", status_code=303)
