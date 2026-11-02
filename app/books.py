"""Book detail and manual metadata editing; originals remain immutable."""
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

router = APIRouter(tags=['Books'])
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class MetadataUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: Title = None
    author: str = Field(default=None, max_length=500)
    series: str = Field(default=None, max_length=500)
    series_index: float = Field(default=None, ge=0, le=100000, allow_inf_nan=False)
    description: str = Field(default=None, max_length=30000)
    genres: str = Field(default=None, max_length=1000)
    year: int | None = Field(default=None, ge=1, le=9999)
    isbn: str = Field(default=None, max_length=50)


def get_book(db, book_id):
    row = db.execute('SELECT * FROM books WHERE id=?', (book_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Книга не найдена')
    return dict(row)


@router.patch('/api/books/{book_id}')
def update_metadata(book_id: int, value: MetadataUpdate):
    from . import main
    changes = value.model_dump(exclude_unset=True)
    with main.connect() as db:
        get_book(db, book_id)
        if changes:
            db.execute('UPDATE books SET ' + ','.join(f'{key}=?' for key in changes) + ' WHERE id=?',
                       (*changes.values(), book_id))
            if 'title' in changes or 'author' in changes:
                db.execute("DELETE FROM wishlist_books WHERE book_id=? AND method='automatic'", (book_id,))
                from .library_state import reconcile
                reconcile(db)
        return get_book(db, book_id)


@router.post('/books/{book_id}/metadata')
def metadata_form(book_id: int, title: Title = Form(...), author: str = Form('', max_length=500),
                  series: str = Form('', max_length=500), series_index: float = Form(0, ge=0, le=100000),
                  description: str = Form('', max_length=30000), genres: str = Form('', max_length=1000),
                  year: str = Form('', max_length=4), isbn: str = Form('', max_length=50)):
    from pydantic import ValidationError
    try:
        value = MetadataUpdate(title=title, author=author, series=series, series_index=series_index,
                               description=description, genres=genres, isbn=isbn,
                               year=int(year) if year.strip() else None)
    except (ValueError, ValidationError):
        raise HTTPException(422, 'Проверьте название, номер в серии и год издания') from None
    update_metadata(book_id, value)
    return RedirectResponse(f'/books/{book_id}', status_code=303)


@router.get('/books/{book_id}', response_class=HTMLResponse)
def book_detail(book_id: int):
    from . import main, library_ui
    from .shelves import get_all_shelves, get_book_shelves
    with main.connect() as db:
        book = get_book(db, book_id)
        progress = db.execute('SELECT * FROM reading_progress WHERE book_id=?', (book_id,)).fetchone()
        device = db.execute('SELECT * FROM book_device_state WHERE book_id=?', (book_id,)).fetchone()
        highlights = db.execute('SELECT * FROM book_highlights WHERE book_id=? ORDER BY id DESC LIMIT 30',
                                (book_id,)).fetchall()
        return library_ui.book_page(book, dict(progress) if progress else {}, dict(device) if device else {},
                                    get_all_shelves(db), get_book_shelves(db, book_id), highlights)
