"""Small server-rendered pages for e-ink browsers; minimal scripting for clearing input fields."""
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .ui import esc, catalog_mirror_links

router = APIRouter()


def page(title, body):
    from .auth import current_user
    user = current_user.get()
    catalogs_link = '<a href="/kindle/integrations">Каталоги</a>' if not user or user['owner'] else ''
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<link rel="stylesheet" href="/static/catalog-search.css?v=2"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} — Yandex2Kindle</title><style>
body{{font-family:serif;font-size:20px;line-height:1.5;background:white;color:black;margin:16px;max-width:760px}}
a{{color:black;text-decoration:underline}} h1{{font-size:28px}} h2{{font-size:23px;margin-bottom:4px}}
input,button,select{{font:inherit;background:white;color:black;border:2px solid black;padding:8px;max-width:95%;box-sizing:border-box}}
label{{display:block;margin-top:12px}} button{{margin:8px 0}} li{{padding:12px 0;border-bottom:1px solid black}}
ul{{list-style:none;padding:0}} nav a{{display:inline-block;padding:8px}} p{{margin:8px 0}}
body{{padding-bottom:64px}} .scroll-to-top{{position:fixed;right:16px;bottom:16px;display:block;padding:10px 16px;border:2px solid black;background:white;color:black;font-family:serif;font-size:20px}}
</style></head><body id="top"><nav><a href="/kindle">Книги</a><a href="/kindle/wishlist">Список чтения</a>{catalogs_link}</nav>{body}<a class="scroll-to-top" href="#top">↑ Наверх</a><script src="/static/clear-fields.js"></script></body></html>'''


def login_page(message=""):
    return page("Вход для Kindle", f'''<h1>Личная библиотека</h1><p>Версия для Kindle</p>
<p role="status">{esc(message)}</p><form action="/login" method="post">
<input type="hidden" name="next_path" value="/kindle">
<label for="username">Логин</label><input id="username" name="username" required maxlength="64" autocomplete="username">
<label for="password">Пароль</label><input id="password" name="password" type="password" required maxlength="256" autocomplete="current-password">
<p><button type="submit">Войти</button></p></form><p><a href="/login">Обычная версия</a></p>''')


@router.get("/kindle/login", response_class=HTMLResponse)
def login_form():
    return login_page()


@router.get("/kindle", response_class=HTMLResponse)
def library(q: str = Query("", max_length=500), page_number: int = Query(1, ge=1)):
    from . import main
    from .auth import current_user
    size = 15
    with main.connect() as db:
        books, total = main.list_books(db, q, size, (page_number - 1) * size)
    user = current_user.get()
    items = []
    for book in books:
        suffix = Path(book["filename"]).suffix.lower()
        links = f'<a href="/download/{book["id"]}">Скачать {esc(suffix.lstrip(".").upper())}</a>'
        if suffix != ".epub":
            links += f' · <a href="/download/{book["id"]}/epub">EPUB (конвертация)</a>'
        from .smtp_delivery import is_smtp_configured
        if is_smtp_configured() and user and user.get("kindle_email"):
            links += f' · <form action="/books/{book["id"]}/send-to-kindle" method="post" style="display:inline"><input type="hidden" name="next_path" value="/kindle"><button type="submit" style="padding:2px 6px;font-size:16px">На @kindle.com</button></form>'
        s_part = f" · Цикл: {esc(book['series'])}" if ('series' in book.keys() and book['series']) else ""
        items.append(f'<li><h2>{esc(book["title"])}</h2><p>{esc(book["author"] or "Без автора")}{s_part}</p><p>{links}</p></li>')
    navigation = []
    for label, number in (("Назад", page_number - 1), ("Далее", page_number + 1)):
        if number >= 1 and (number < page_number or page_number * size < total):
            navigation.append(f'<a href="/kindle?{esc(urlencode({"q": q, "page_number": number}))}">{label}</a>')
    body = f'''<h1>Мои книги</h1><p>{esc(user["username"] if user else "")} · <a href="/kindle">Все книги</a></p>
<form action="/kindle" method="get"><label for="q">Название или автор</label>
<input id="q" name="q" value="{esc(q)}" maxlength="500"> <button type="submit">Найти</button></form>
<p>Найдено: {total}. Страница {page_number} из {max(1, (total + size - 1) // size)}.</p>
<ul>{''.join(items) if items else '<li>Книг пока нет. Добавьте их с компьютера или измените поиск.</li>'}</ul>
<nav aria-label="Страницы">{' '.join(navigation)}</nav>
<p>Скачивание сохраняет файл; открывайте его в доступной на устройстве читалке. Поддержка форматов зависит от устройства.</p>
<p><a href="/">Обычная версия</a> · <a href="/opds">OPDS</a></p>
<form action="/logout" method="post"><input type="hidden" name="next_path" value="/kindle/login"><button type="submit">Выйти</button></form>'''
    return page("Мои книги", body)


@router.get("/kindle/integrations", response_class=HTMLResponse)
@router.get("/kindle/integrations/results", response_class=HTMLResponse)
def catalogs(request: Request, q: str = Query("", max_length=500), author: str = Query("", max_length=500),
             source: str = Query("flibusta", pattern="^(flibusta|opds|zlib)$"),
             page_number: int = Query(0, ge=0, le=1000), wish_id: int | None = Query(None, ge=1)):
    from . import integrations
    items, message = [], ""
    if request.url.path == "/kindle/integrations/results":
        try:
            items = integrations.search(source, q, author, page_number)["items"]
            if not items:
                message = "На этой странице нет доступных изданий. Попробуйте другой запрос, каталог или страницу."
        except HTTPException as exc:
            message = str(exc.detail)
    status = integrations.integration_status()
    options = ''.join(f'<option value="{key}" {"selected" if source == key else ""}>{label}{" · не настроен" if not status.get(key + "_configured", True) else ""}</option>'
                      for key, label in (("flibusta", "Flibusta"), ("zlib", "Z-Lib"), ("opds", "Другой OPDS")))
    wish = f'<input type="hidden" name="wish_id" value="{wish_id}">' if wish_id else ""
    results = ''.join(f'''<li><h2>{esc(item['title'])}</h2><p>{esc(item['author'] or 'Без автора')} · {esc(item['suffix'][1:].upper())}</p>
<form action="/kindle/integrations/import" method="post"><input type="hidden" name="candidate_id" value="{esc(item['id'])}">{wish}<button type="submit">Добавить в библиотеку</button></form></li>''' for item in items)
    links = []
    if request.url.path == "/kindle/integrations/results":
        for label, number in (("Предыдущая страница", page_number - 1), ("Следующая страница", page_number + 1)):
            if 0 <= number <= 1000:
                params = dict(q=q, author=author, source=source, page_number=number)
                if wish_id:
                    params['wish_id'] = wish_id
                links.append(f'<a href="/kindle/integrations?{esc(urlencode(params))}">{label}</a>')
    return page("Каталоги", f'''<h1>Поиск по каталогам</h1><form method="get" action="/kindle/integrations">{wish}
<label for="q">Название книги</label><input id="q" name="q" value="{esc(q)}" maxlength="500">
<label for="author">Автор</label><input id="author" name="author" value="{esc(author)}" maxlength="500">
<label for="source">Каталог</label><select id="source" name="source">{options}</select>
<label for="page-number">Страница каталога (с 0)</label><input id="page-number" name="page_number" type="number" min="0" max="1000" value="{page_number}">
<noscript><p><button formaction="/kindle/integrations/results">Найти без JavaScript</button></p></noscript><p><button type="submit">Найти</button> <a href="/kindle/integrations">Сбросить поиск</a></p></form>
<div id="catalog-results" aria-live="polite"><p role="status">{esc(message)}</p><ul>{results}</ul><nav>{' '.join(links)}</nav></div>{catalog_mirror_links(source)}<script src="/static/catalog-search.js?v=2" defer></script>
<h2>Яндекс Книги</h2><form action="/kindle/integrations/sync" method="post"><button type="submit">Синхронизировать список чтения</button></form>''')


@router.post("/kindle/integrations/import", response_class=HTMLResponse)
def import_book(candidate_id: str = Form(..., max_length=64), wish_id: int | None = Form(None, ge=1)):
    from .integrations import Acquisition, acquire
    try:
        result = acquire(Acquisition(candidate_id=candidate_id, wish_id=wish_id))
    except HTTPException as exc:
        return HTMLResponse(page("Ошибка импорта", f'<h1>Не удалось добавить книгу</h1><p role="alert">{esc(exc.detail)}</p><a href="/kindle/integrations">Повторить поиск</a>'), status_code=exc.status_code)
    return page("Книга добавлена", f'<h1>Книга в библиотеке</h1><p><a href="/download/{int(result["id"])}">Скачать файл</a> · <a href="/download/{int(result["id"])}/epub">Скачать EPUB</a></p><a href="/kindle">Мои книги</a>')


@router.post("/kindle/integrations/sync", response_class=HTMLResponse)
def sync_books():
    from .integrations import sync_yandex
    try:
        sync_yandex()
    except HTTPException as exc:
        return HTMLResponse(page("Ошибка синхронизации", f'<p role="alert">{esc(exc.detail)}</p><a href="/kindle/integrations">К каталогам</a>'), status_code=exc.status_code)
    return RedirectResponse("/kindle/wishlist", status_code=303)


@router.get("/kindle/wishlist", response_class=HTMLResponse)
def reading_list():
    from . import main
    from .library_state import wishlist_rows
    from .auth import current_user
    user = current_user.get()
    owner = not user or user['owner']
    with main.connect() as db:
        wishes = wishlist_rows(db)
    items = []
    for wish in wishes:
        if wish['book_id']:
            action = f'<a href="/download/{wish["book_id"]}">Скачать файл</a> · <a href="/download/{wish["book_id"]}/epub">EPUB</a>'
        elif owner:
            params = urlencode(dict(q=wish['title'], author=wish['author'], wish_id=wish['id']))
            action = f'<a href="/kindle/integrations?{esc(params)}">Найти в каталогах</a>'
        else:
            action = 'Файла пока нет в библиотеке'
        items.append(f'<li><h2>{esc(wish["title"])}</h2><p>{esc(wish["author"])} · {esc(wish["status_label"])}</p><p>{action}</p></li>')
    return page("Список чтения", f'<h1>Список чтения</h1><ul>{"".join(items) or "<li>Список пока пуст.</li>"}</ul>')
