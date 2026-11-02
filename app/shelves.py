"""Ratings, reviews, and custom shelves for books."""
from datetime import datetime, timezone
import html
from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator

router = APIRouter()


class RatingUpdate(BaseModel):
    rating: int = Field(default=0, ge=0, le=5)
    review: str = Field(default="", max_length=5000)


class ShelfCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = Field(default="#2563eb", max_length=30)

    @field_validator('name')
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError('Введите название полки')
        return value.strip()


def get_book_shelves(db, book_id: int):
    rows = db.execute("""
        SELECT s.id, s.name, s.color
        FROM shelves s
        JOIN book_shelf_links l ON l.shelf_id = s.id
        WHERE l.book_id = ?
        ORDER BY s.name ASC
    """, (book_id,)).fetchall()
    return [dict(r) for r in rows]


def get_all_shelves(db):
    rows = db.execute("""
        SELECT s.id, s.name, s.color, COUNT(l.book_id) as book_count
        FROM shelves s
        LEFT JOIN book_shelf_links l ON l.shelf_id = s.id
        GROUP BY s.id
        ORDER BY s.name ASC
    """).fetchall()
    return [dict(r) for r in rows]


@router.get("/api/shelves")
def list_shelves():
    from . import main
    with main.connect() as db:
        return get_all_shelves(db)


@router.post("/api/shelves")
def create_shelf(payload: ShelfCreate):
    from . import main
    name = payload.name.strip()
    color = payload.color.strip() or "#2563eb"
    with main.connect() as db:
        existing = db.execute("SELECT id FROM shelves WHERE name=?", (name,)).fetchone()
        if existing:
            return {"id": existing["id"], "name": name, "color": color, "status": "exists"}
        now = datetime.now(timezone.utc).isoformat()
        cursor = db.execute(
            "INSERT INTO shelves(name, color, created_at) VALUES(?, ?, ?)",
            (name, color, now)
        )
        db.commit()
        return {"id": cursor.lastrowid, "name": name, "color": color, "status": "created"}


@router.post("/shelves/create")
def form_create_shelf(name: str = Form(..., min_length=1, max_length=100), color: str = Form("#2563eb")):
    if not name.strip():
        raise HTTPException(422, 'Введите название полки')
    create_shelf(ShelfCreate(name=name, color=color))
    return RedirectResponse("/#shelves", status_code=303)


@router.delete("/api/shelves/{shelf_id}")
def delete_shelf(shelf_id: int):
    from . import main
    with main.connect() as db:
        db.execute("DELETE FROM book_shelf_links WHERE shelf_id=?", (shelf_id,))
        db.execute("DELETE FROM shelves WHERE id=?", (shelf_id,))
        db.commit()
    return {"status": "deleted", "id": shelf_id}


@router.post("/shelves/{shelf_id}/delete")
def form_delete_shelf(shelf_id: int):
    delete_shelf(shelf_id)
    return RedirectResponse("/#shelves", status_code=303)


@router.post("/api/shelves/{shelf_id}/books/{book_id}")
def add_book_to_shelf(shelf_id: int, book_id: int):
    from . import main
    with main.connect() as db:
        if not db.execute("SELECT id FROM books WHERE id=?", (book_id,)).fetchone():
            raise HTTPException(404, "Книга не найдена")
        if not db.execute("SELECT id FROM shelves WHERE id=?", (shelf_id,)).fetchone():
            raise HTTPException(404, "Полка не найдена")
        db.execute(
            "INSERT OR IGNORE INTO book_shelf_links(book_id, shelf_id) VALUES(?, ?)",
            (book_id, shelf_id)
        )
        db.commit()
    return {"status": "linked", "shelf_id": shelf_id, "book_id": book_id}


@router.delete("/api/shelves/{shelf_id}/books/{book_id}")
def remove_book_from_shelf(shelf_id: int, book_id: int):
    from . import main
    with main.connect() as db:
        db.execute(
            "DELETE FROM book_shelf_links WHERE book_id=? AND shelf_id=?",
            (book_id, shelf_id)
        )
        db.commit()
    return {"status": "unlinked", "shelf_id": shelf_id, "book_id": book_id}


@router.post("/books/{book_id}/shelf")
def form_toggle_shelf(book_id: int, shelf_id: int = Form(...), action: str = Form("add")):
    if action == "remove":
        remove_book_from_shelf(shelf_id, book_id)
    else:
        add_book_to_shelf(shelf_id, book_id)
    return RedirectResponse(f"/books/{book_id}", status_code=303)


@router.get("/api/books/{book_id}/rating")
def get_book_rating(book_id: int):
    from . import main
    with main.connect() as db:
        row = db.execute("SELECT rating, review FROM books WHERE id=?", (book_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Книга не найдена")
        return {"book_id": book_id, "rating": row["rating"], "review": row["review"]}


@router.post("/api/books/{book_id}/rating")
def set_book_rating(book_id: int, payload: RatingUpdate):
    from . import main
    with main.connect() as db:
        if not db.execute("SELECT id FROM books WHERE id=?", (book_id,)).fetchone():
            raise HTTPException(404, "Книга не найдена")
        db.execute(
            "UPDATE books SET rating=?, review=? WHERE id=?",
            (payload.rating, payload.review.strip(), book_id)
        )
        db.commit()
    return {"book_id": book_id, "rating": payload.rating, "review": payload.review.strip()}


@router.post("/books/{book_id}/rating")
def form_set_book_rating(book_id: int, rating: int = Form(0, ge=0, le=5), review: str = Form("")):
    set_book_rating(book_id, RatingUpdate(rating=rating, review=review))
    return RedirectResponse(f"/books/{book_id}", status_code=303)
