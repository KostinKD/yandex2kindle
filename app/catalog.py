"""Bounded library queries shared by HTML and JSON catalogs."""
from pathlib import Path

SORTS = {
    'newest': 'b.id DESC',
    'title': 'casefold(b.title), b.id',
    'author': 'casefold(b.author), casefold(b.title), b.id',
    'series': 'casefold(b.series), b.series_index, b.id',
    'progress': 'COALESCE(p.progress,0) DESC, b.id DESC',
    'last_read': 'p.updated_at DESC, b.id DESC',
}


def list_books(db, q='', limit=50, offset=0, shelf_id=None, format='', status='all',
               sort='newest', author='', series=''):
    db.create_function('casefold', 1, lambda value: (value or '').casefold())
    db.create_function('file_format', 1, lambda value: Path(value).suffix[1:].upper())
    clauses, params = [], []
    if q.strip():
        fields = ('title', 'author', 'series', 'genres', 'description')
        clauses.append('(' + ' OR '.join(f'instr(casefold(b.{field}),?)>0' for field in fields) + ')')
        params.extend([q.strip().casefold()] * len(fields))
    if shelf_id:
        clauses.append('EXISTS(SELECT 1 FROM book_shelf_links l WHERE l.book_id=b.id AND l.shelf_id=?)')
        params.append(shelf_id)
    for field, value in [('author', author), ('series', series)]:
        if value:
            clauses.append(f'casefold(b.{field})=casefold(?)')
            params.append(value)
    if format:
        clauses.append('file_format(b.filename)=?')
        params.append(format.upper())
    if status == 'reading':
        clauses.append('p.progress>0 AND p.progress<1')
    elif status == 'finished':
        clauses.append('p.progress=1')
    elif status == 'unread':
        clauses.append('COALESCE(p.progress,0)=0')
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    source = ' FROM books b LEFT JOIN reading_progress p ON p.book_id=b.id'
    total = db.execute('SELECT COUNT(*)' + source + where, params).fetchone()[0]
    rows = db.execute('SELECT b.*,COALESCE(p.progress,0) AS progress,p.updated_at AS last_read'
                      + source + where + ' ORDER BY ' + SORTS.get(sort, SORTS['newest']) + ' LIMIT ? OFFSET ?',
                      (*params, limit, offset)).fetchall()
    return rows, total


def facets(db):
    return {field: [r[0] for r in db.execute(
        f"SELECT DISTINCT {field} FROM books WHERE {field}<>'' ORDER BY {field} LIMIT 200")]
        for field in ('author', 'series')}
