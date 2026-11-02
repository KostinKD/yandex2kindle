"""Authenticated KOReader quote exchange and explicit Kindle clippings import."""
import hashlib
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import kosync

router = APIRouter(tags=['Quote exchange'])


class DeviceQuote(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    key: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=10000)
    comment: str = Field(default='', max_length=5000)
    cfi: str = Field(default='', max_length=1000)
    progress: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    color: str = Field(default='yellow', max_length=30)

    @field_validator('cfi')
    @classmethod
    def cfi_only(cls, value):
        if value and not value.startswith('epubcfi('):
            raise ValueError('CFI должен относиться к EPUB; XPointer хранится на устройстве')
        return value


class QuoteExchange(BaseModel):
    document: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_]+$')
    annotations: list[DeviceQuote] = Field(default_factory=list, max_length=200)


def import_quote(db, book_id, source, quote):
    previous = db.execute('SELECT * FROM highlight_imports WHERE book_id=? AND source=? AND external_id=?',
                          (book_id, source, quote.key)).fetchone()
    if previous:
        if not previous['deleted']:
            db.execute('UPDATE book_highlights SET text=?,comment=?,cfi=CASE WHEN ?<>\'\' THEN ? ELSE cfi END,'
                       'progress=COALESCE(?,progress),color=? WHERE id=?',
                       (quote.text, quote.comment, quote.cfi, quote.cfi, quote.progress, quote.color, previous['highlight_id']))
        return False
    cursor = db.execute('INSERT INTO book_highlights(book_id,text,comment,cfi,progress,color,created_at) VALUES(?,?,?,?,?,?,?)',
                        (book_id, quote.text, quote.comment, quote.cfi, quote.progress or 0, quote.color,
                         datetime.now(timezone.utc).isoformat()))
    db.execute('INSERT INTO highlight_imports(book_id,source,external_id,highlight_id) VALUES(?,?,?,?)',
               (book_id, source, quote.key, cursor.lastrowid))
    return True


@router.post('/syncs/highlights')
def exchange(value: QuoteExchange, username=Depends(kosync.authorize)):
    from . import main, auth
    if username != auth.sync_username():
        raise HTTPException(403, 'Используйте учётные данные своей библиотеки')
    kosync.index_books()
    with main.connect() as db:
        matches = db.execute('SELECT DISTINCT book_id FROM book_documents WHERE document=?', (value.document,)).fetchall()
        if not matches:
            raise HTTPException(404, 'Файл не сопоставлен с библиотекой. Скачайте его через личный OPDS или экспорт EPUB.')
        if len(matches) != 1:
            raise HTTPException(409, 'Хеш файла соответствует нескольким книгам; автоматический импорт отменён')
        book_id = matches[0][0]
        if not db.execute('SELECT 1 FROM books WHERE id=?', (book_id,)).fetchone():
            raise HTTPException(404, 'Книга не найдена')
        added = sum(import_quote(db, book_id, 'koreader', q) for q in value.annotations)
        rows = db.execute('SELECT id,text,comment,cfi,progress,color FROM book_highlights WHERE book_id=? ORDER BY id DESC LIMIT 2001', (book_id,)).fetchall()
        return {'book_id': book_id, 'added': added, 'highlights': [dict(r) for r in rows[:2000]],
                'truncated': len(rows) > 2000}


def parse_clippings(text):
    for block in re.split(r'^={10,}\s*$', text, flags=re.M):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        title = lines[0].lstrip('\ufeff').strip()
        metadata = lines[1]
        if not re.search(r'highlight|выделени', metadata, re.I):
            continue
        content = '\n'.join(lines[2:]).strip()
        if not content:
            continue
        key = hashlib.sha256((title + '\n' + metadata + '\n' + content).encode()).hexdigest()
        author = ''
        match = re.fullmatch(r'(.*) \(([^()]*)\)', title)
        if match:
            title, author = match.groups()
        yield title.strip(), author.strip(), DeviceQuote(key=key, text=content)


async def import_clippings(file):
    from . import main
    from .library_state import normalize, authors_match
    try:
        data = await file.read(8 * 1024 * 1024 + 1)
    finally:
        await file.close()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, 'Файл цитат должен быть не больше 8 МБ')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeError:
        raise HTTPException(422, 'Нужен My Clippings.txt в кодировке UTF-8') from None
    from pydantic import ValidationError
    try:
        candidates = list(parse_clippings(text))
    except ValidationError:
        raise HTTPException(422, 'Слишком длинная или некорректная цитата в файле') from None
    if not candidates:
        raise HTTPException(422, 'В файле не найдены выделения Kindle на русском или английском языке')
    added, unmatched = 0, 0
    with main.connect() as db:
        books = db.execute('SELECT id,title,author FROM books').fetchall()
        by_title = {}
        for book in books:
            by_title.setdefault(normalize(book['title']), []).append(book)
        for title, author, quote in candidates:
            matches = [b for b in by_title.get(normalize(title), []) if not author or authors_match(author, b['author'])]
            if len(matches) != 1:
                unmatched += 1
            else:
                added += import_quote(db, matches[0]['id'], 'kindle', quote)
    return {'added': added, 'unmatched': unmatched, 'processed': len(candidates)}


@router.post('/api/highlights/import')
async def import_api(file: UploadFile = File(...)):
    return await import_clippings(file)


@router.post('/highlights/import')
async def import_form(file: UploadFile = File(...)):
    result = await import_clippings(file)
    return RedirectResponse(f'/highlights?imported={result["added"]}&unmatched={result["unmatched"]}', status_code=303)
