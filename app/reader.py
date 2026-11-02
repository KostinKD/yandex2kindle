"""Web EPUB Reader powered by Foliate-js."""
import html
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

router = APIRouter()


def esc(value):
    return html.escape(str(value or ""), quote=True)


@router.get("/reader/{book_id}", response_class=HTMLResponse)
def read_book(book_id: int, request: Request):
    from . import main
    with main.connect() as db:
        book = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        raise HTTPException(status_code=404, detail="Книга не найдена")

    title = esc(book["title"])
    author = esc(book["author"] or "Неизвестный автор")

    page_html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} · Читалка · Yandex2Kindle</title>
  <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
  <link rel="stylesheet" href="/static/reader.css?v=3">
  <script type="module" src="/static/foliate/view.js"></script>
</head>
<body class="theme-light">
  <header class="reader-header" id="header">
    <div class="header-left">
      <a href="/" class="btn-nav" title="Вернуться в библиотеку">← Библиотека</a>
      <div class="book-titles">
        <h1 class="book-title">{title}</h1>
        <span class="book-author">{author}</span>
      </div>
    </div>
    <div class="header-center">
      <span id="chapter-label" class="chapter-label">Загрузка книги...</span>
    </div>
    <div class="header-right">
      <button id="btn-toc" class="btn-icon" title="Содержание">☰</button>
      <button id="btn-settings" class="btn-icon" title="Настройки">⚙</button>
      <button id="btn-fullscreen" class="btn-icon" title="Во весь экран">⛶</button>
    </div>
  </header>

  <!-- Sidebar: TOC -->
  <aside id="toc-drawer" class="drawer drawer-left" hidden>
    <div class="drawer-header">
      <h2>Содержание</h2>
      <button id="btn-close-toc" class="drawer-close" aria-label="Закрыть">✕</button>
    </div>
    <nav class="toc-content">
      <ul id="toc-list" class="toc-list"></ul>
    </nav>
  </aside>

  <!-- Sidebar: Settings -->
  <aside id="settings-drawer" class="drawer drawer-right" hidden>
    <div class="drawer-header">
      <h2>Настройки чтения</h2>
      <button id="btn-close-settings" class="drawer-close" aria-label="Закрыть">✕</button>
    </div>
    <div class="settings-body">
      <div class="setting-group">
        <span class="setting-label">Тема</span>
        <div class="theme-options">
          <button type="button" class="theme-btn active" data-theme="light">Светлая</button>
          <button type="button" class="theme-btn" data-theme="sepia">Сепия</button>
          <button type="button" class="theme-btn" data-theme="dark">Тёмная</button>
          <button type="button" class="theme-btn" data-theme="eink">E-Ink</button>
        </div>
      </div>

      <div class="setting-group">
        <span class="setting-label">Шрифт</span>
        <div class="font-options">
          <button type="button" class="font-btn active" data-font="serif">Serif</button>
          <button type="button" class="font-btn" data-font="sans">Sans-serif</button>
          <button type="button" class="font-btn" data-font="system">System</button>
        </div>
      </div>

      <div class="setting-group">
        <span class="setting-label">Размер шрифта</span>
        <div class="size-controls">
          <button type="button" id="btn-font-dec" class="ctrl-btn">A−</button>
          <span id="font-size-display" class="size-display">100%</span>
          <button type="button" id="btn-font-inc" class="ctrl-btn">A+</button>
        </div>
      </div>

      <div class="setting-group">
        <span class="setting-label">Поля страницы</span>
        <div class="size-controls">
          <button type="button" id="btn-margin-dec" class="ctrl-btn">Уже</button>
          <span id="margin-display" class="size-display">Стандарт</span>
          <button type="button" id="btn-margin-inc" class="ctrl-btn">Шире</button>
        </div>
      </div>
    </div>
  </aside>

  <!-- Highlight creation modal / popover -->
  <div id="highlight-bar" class="highlight-bar" hidden>
    <button type="button" id="btn-create-highlight" class="hl-btn">🖍 Сохранить цитату</button>
  </div>

  <!-- Main reading viewport -->
  <main id="reader-view-container" class="reader-viewport">
    <div id="loading-spinner" class="loading-state">
      <div class="spinner"></div>
      <p>Открываем книгу...</p>
    </div>
    <foliate-view id="view"></foliate-view>
  </main>
  <button type="button" id="tap-prev" class="reader-page reader-page-prev" aria-label="Предыдущая страница">‹</button>
  <button type="button" id="tap-next" class="reader-page reader-page-next" aria-label="Следующая страница">›</button>

  <footer class="reader-footer" id="footer">
    <div class="progress-track" id="progress-track">
      <div class="progress-bar" id="progress-bar" style="width: 0%;"></div>
    </div>
    <div class="footer-meta">
      <span id="progress-percent">0%</span>
      <span id="relocate-info"></span>
    </div>
  </footer>

  <script>
    window.Y2K_BOOK_ID = {book_id};
    window.Y2K_BOOK_URL = "/download/{book_id}/epub";
  </script>
  <div id="reader-status" class="reader-status" role="status" aria-live="polite" hidden></div>
  <script type="module" src="/static/reader.js?v=3"></script>
</body>
</html>"""
    return HTMLResponse(page_html)
