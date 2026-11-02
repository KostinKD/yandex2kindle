"""Highlights & Annotations Hub: manage and export book quotes and notes."""
from datetime import datetime, timezone
import html
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

router = APIRouter()


class HighlightCreate(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    comment: str = Field(default="", max_length=5000)
    cfi: str = Field(default="", max_length=1000)
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    color: str = Field(default="yellow", max_length=30)


def esc(value):
    return html.escape(str(value or ""), quote=True)


@router.get("/api/books/{book_id}/highlights")
def get_book_highlights(book_id: int):
    from . import main
    with main.connect() as db:
        if not db.execute("SELECT id FROM books WHERE id=?", (book_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Книга не найдена")
        rows = db.execute(
            "SELECT * FROM book_highlights WHERE book_id=? ORDER BY id DESC",
            (book_id,)
        ).fetchall()
        return [dict(r) for r in rows]


@router.post("/api/books/{book_id}/highlights")
def create_book_highlight(book_id: int, payload: HighlightCreate):
    from . import main
    with main.connect() as db:
        if not db.execute("SELECT id FROM books WHERE id=?", (book_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Книга не найдена")
        now = datetime.now(timezone.utc).isoformat()
        cursor = db.execute(
            """INSERT INTO book_highlights(book_id, text, comment, cfi, progress, color, created_at)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (book_id, payload.text.strip(), payload.comment.strip(), payload.cfi, payload.progress, payload.color, now)
        )
        db.commit()
        return {
            "id": cursor.lastrowid,
            "book_id": book_id,
            "text": payload.text.strip(),
            "comment": payload.comment.strip(),
            "cfi": payload.cfi,
            "progress": payload.progress,
            "color": payload.color,
            "created_at": now
        }


@router.delete("/api/highlights/{highlight_id}")
def delete_highlight(highlight_id: int):
    from . import main
    with main.connect() as db:
        row = db.execute("SELECT id FROM book_highlights WHERE id=?", (highlight_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Цитата не найдена")
        db.execute('UPDATE highlight_imports SET deleted=1,highlight_id=NULL WHERE highlight_id=?', (highlight_id,))
        db.execute("DELETE FROM book_highlights WHERE id=?", (highlight_id,))
        db.commit()
    return {"status": "deleted", "id": highlight_id}


@router.post("/highlights/{highlight_id}/delete")
def delete_highlight_form(highlight_id: int):
    from fastapi.responses import RedirectResponse
    delete_highlight(highlight_id)
    return RedirectResponse("/highlights", status_code=303)


@router.get("/highlights/export")
def export_highlights_markdown():
    from . import main
    with main.connect() as db:
        books = db.execute("""
            SELECT b.id, b.title, b.author, h.id as hl_id, h.text, h.comment, h.progress, h.created_at
            FROM book_highlights h
            JOIN books b ON b.id = h.book_id
            ORDER BY b.title, h.id ASC
        """).fetchall()

    if not books:
        md = "# Мои цитаты и заметки\n\n*Цитат пока нет.*\n"
    else:
        current_book_id = None
        lines = ["# Мои цитаты и заметки\n"]
        for row in books:
            if row["id"] != current_book_id:
                current_book_id = row["id"]
                lines.append(f"\n## {row['title']}\n**Автор**: {row['author'] or 'Неизвестен'}\n")
            pct = int(row["progress"] * 100) if row["progress"] else 0
            lines.append(f"> {row['text']}\n")
            if row["comment"]:
                lines.append(f"*{row['comment']}*\n")
            lines.append(f"— *Прогресс: {pct}%* · _{row['created_at'][:10]}_\n")
        md = "\n".join(lines)

    headers = {"Content-Disposition": "attachment; filename=library_highlights.md"}
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8", headers=headers)


@router.get("/highlights", response_class=HTMLResponse)
def highlights_page(request: Request, q: str = Query("", max_length=200),
                    imported: int | None = Query(None, ge=0), unmatched: int = Query(0, ge=0)):
    from . import main, ui
    with main.connect() as db:
        query = """
            SELECT h.*, b.title as book_title, b.author as book_author,
                   COALESCE(i.source,'web') AS source
            FROM book_highlights h
            JOIN books b ON b.id = h.book_id
            LEFT JOIN highlight_imports i ON i.highlight_id=h.id
        """
        params = []
        if q.strip():
            needle = f"%{q.strip().lower()}%"
            query += " WHERE lower(h.text) LIKE ? OR lower(h.comment) LIKE ? OR lower(b.title) LIKE ?"
            params.extend([needle, needle, needle])
        query += " ORDER BY h.id DESC"
        highlights = db.execute(query, params).fetchall()

    body = f"""
    <section class="page-header">
      <div class="page-title-row">
        <div>
          <h2>Цитаты и заметки</h2>
          <p class="section-lead">Сохранённые фрагменты из ваших книг с синхронизацией прогресса.</p>
        </div>
        <div class="actions">
          <a href="/highlights/export" class="button secondary">{ui.icon('download')} Экспорт в Markdown</a>
        </div>
      </div>
      <form class="search-form" action="/highlights" method="get">
        <label for="q" class="visually-hidden">Поиск по цитатам</label>
        <div class="search-field">
          <input id="q" name="q" value="{esc(q)}" placeholder="Поиск по цитатам, заметкам или книгам...">
          <button type="submit" class="button">{ui.icon('search')} Найти</button>
        </div>
      </form>
    </section>
    """
    if imported is not None:
        body += f'<p class="quote-import-result" role="status">Добавлено цитат: {imported}. Не сопоставлено с книгами: {unmatched}. Повторные цитаты пропущены.</p>'
    body += '''<details class="quote-import"><summary>Цитаты с Kindle и KOReader</summary>
<p>В KOReader установите обновлённый плагин Yandex2Kindle, укажите сервер и логин синхронизации. Выделения отправляются при открытии книги и после изменений, если доступна сеть.</p>
<p>Для штатной читалки Kindle подключите устройство по USB и выберите <strong>documents/My Clippings.txt</strong>. Импортируются выделения на русском и английском. Название и автор должны совпадать с книгой в библиотеке.</p>
<form action="/highlights/import" method="post" enctype="multipart/form-data" data-async><label>Файл цитат Kindle<input type="file" name="file" accept=".txt,text/plain" required></label><button class="button secondary" type="submit">Импортировать цитаты</button><p class="form-message" role="status"></p></form>
<a class="text-link" href="/plugins/yandex2kindle.koplugin.zip">Скачать плагин KOReader →</a></details>'''

    if not highlights:
      body += f"""
      <div class="empty-state">
        {ui.icon('book')}
        <h3>{'Ничего не найдено' if q else 'Цитат пока нет'}</h3>
        <p>{'Попробуйте другой поисковый запрос.' if q else 'Выделяйте текст во встроенной читалке, чтобы сохранять важные мысли и цитаты.'}</p>
        <a class="button" href="/">Перейти в библиотеку</a>
      </div>
      """
    else:
      cards = []
      for hl in highlights:
          pct = int(hl["progress"] * 100) if hl["progress"] else 0
          date_str = hl["created_at"][:10] if hl["created_at"] else ""
          comment_html = f'<div class="highlight-comment">💬 <em>{esc(hl["comment"])}</em></div>' if hl["comment"] else ""
          source_label = {'koreader': 'KOReader', 'kindle': 'Kindle', 'web': 'Веб-ридер'}.get(hl['source'], 'Импорт')
          cards.append(f"""
          <article class="highlight-card">
            <div class="highlight-meta">
              <strong class="highlight-book"><a href="/reader/{hl['book_id']}" target="_blank">{esc(hl['book_title'])}</a></strong>
              <span class="highlight-author">{esc(hl['book_author'] or 'Неизвестный автор')}</span>
              <span class="meta-tag">{pct}%</span>
              <span class="meta-tag">{source_label}</span>
              <span class="highlight-date">{esc(date_str)}</span>
            </div>
            <blockquote class="highlight-text">{esc(hl['text'])}</blockquote>
            {comment_html}
            <div class="highlight-actions">
              <a href="/reader/{hl['book_id']}" target="_blank" class="text-link">Открыть в читалке</a>
              <form action="/highlights/{hl['id']}/delete" method="post" style="display:inline" onsubmit="return confirm('Удалить эту цитату?')">
                <button type="submit" class="delete-button" style="padding:0;background:none;border:none;cursor:pointer">Удалить</button>
              </form>
            </div>
          </article>
          """)
      body += f'<div class="highlights-list">{"".join(cards)}</div>'

    return HTMLResponse(ui.layout("Цитаты и заметки", body, active="highlights"))
