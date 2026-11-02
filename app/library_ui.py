"""Compact catalog and book workspace HTML."""
from pathlib import Path
from urllib.parse import urlencode

from .ui import esc, layout, cover, kindle_confirmation, wish_tracking


def url(filters, **changes):
    values = {**filters, **changes}
    return '/?' + urlencode({k: v for k, v in values.items() if v is not None and v != ''})


def select(name, label, choices, selected):
    options = ''.join(f'<option value="{esc(value)}" {"selected" if str(value) == str(selected) else ""}>{esc(text)}</option>'
                      for value, text in choices)
    return f'<label>{label}<select name="{name}">{options}</select></label>'


def heading(title, subtitle='', actions=''):
    return f'<header class="workspace-heading"><div><h1>{esc(title)}</h1>{f"<p>{esc(subtitle)}</p>" if subtitle else ""}</div><div class="heading-actions">{actions}</div></header>'


def read_link(book):
    return f'/audiobook/{book["id"]}' if Path(book['filename']).suffix.lower() in ('.mp3', '.m4b') else f'/reader/{book["id"]}'


def book_card(raw):
    book = dict(raw)
    percent = book.get('progress', 0) * 100
    suffix = Path(book['filename']).suffix[1:].upper()
    state = 'Прочитано' if percent >= 100 else 'Читаю' if percent else 'Не начато'
    series_number = f' · {book["series_index"]:g}' if book.get('series_index') else ''
    series = f'<span class="catalog-series">{esc(book["series"])}{series_number}</span>' if book.get('series') else ''
    rating = f'<span class="catalog-rating" aria-label="Оценка {book["rating"]} из 5">{"★" * book["rating"]}</span>' if book.get('rating') else ''
    return f'''<article class="catalog-book">
<a class="catalog-cover" href="/books/{book['id']}" aria-label="{esc(book['title'])}: сведения о книге">{cover(book['title'], book['author'], book['id'], book['id'])}</a>
<div class="catalog-book-info"><div class="catalog-book-meta"><span class="format">{suffix}</span><span>{state}</span>{rating}</div>
<h2><a href="/books/{book['id']}">{esc(book['title'])}</a></h2><p class="book-author">{esc(book['author'] or 'Неизвестный автор')}</p>{series}
<div class="reading-meter"><progress max="100" value="{percent:.1f}" aria-label="Прочитано {percent:.0f}%"></progress><span>{percent:.0f}%</span></div>
<a class="catalog-read" href="{read_link(book)}">{'Слушать' if suffix in ('MP3', 'M4B') else 'Продолжить' if percent and percent < 100 else 'Читать'} <span aria-hidden="true">↗</span></a></div></article>'''


def upload_panel():
    return '''<details class="upload-panel compact-upload" id="add-book"><summary>Добавить книгу</summary>
<form action="/upload" method="post" enctype="multipart/form-data" data-async>
<label class="drop-zone" for="book-file"><strong data-file-label>Выберите или перетащите файл</strong><span>EPUB, FB2, PDF, MOBI, AZW3, TXT, MP3, M4B · до 500 МБ</span>
<input id="book-file" type="file" name="file" accept=".epub,.fb2,.pdf,.mobi,.azw3,.txt,.mp3,.m4b" required></label>
<div class="form-columns"><label>Название<input name="title" placeholder="Из файла"></label><label>Автор<input name="author" placeholder="Из файла"></label></div>
<button class="button">Добавить в библиотеку</button><p class="form-message" role="status"></p></form></details>'''


def shelf_create():
    return '''<details class="shelf-create"><summary>+ Новая полка</summary><form action="/shelves/create" method="post">
<label>Название<input name="name" maxlength="100" required placeholder="Например, На выходные"></label>
<button class="button small">Создать</button></form></details>'''


def catalog_page(books, total, count, page, filters, shelves, facets):
    for field in ('author', 'series'):
        if filters[field] and filters[field] not in facets[field]:
            facets[field].append(filters[field])
    actions = '<a class="button" href="#add-book">+ Добавить книгу</a>'
    body = heading('Библиотека', f'{count} книг · личная коллекция', actions)
    body += upload_panel()
    shelf_links = ''.join(f'<a href="{esc(url(filters, shelf=s["id"], page=None))}" {"aria-current=page" if filters.get("shelf") == s["id"] else ""}>{esc(s["name"])} <span>{s["book_count"]}</span></a>' for s in shelves)
    body += f'<div class="shelf-strip" id="shelves"><nav aria-label="Полки"><a href="{esc(url(filters, shelf=None, page=None))}" {"aria-current=page" if not filters.get("shelf") else ""}>Все книги <span>{count}</span></a>{shelf_links}</nav>{shelf_create()}</div>'
    if shelves:
        management = ''.join(f'''<form action="/shelves/{s['id']}/delete" method="post" data-async data-confirm>
<span>{esc(s['name'])} <small>{s['book_count']} книг</small></span><button class="delete-button" type="submit">Удалить полку</button><p class="form-message" role="status"></p></form>''' for s in shelves)
        body += f'<details class="shelf-management"><summary>Управление полками</summary><p class="muted">Удаляется только полка. Книги и прогресс чтения остаются в библиотеке.</p>{management}</details>'
    controls = select('format', 'Формат', [('', 'Все форматы')] + [(f, f) for f in ('EPUB', 'FB2', 'PDF', 'MOBI', 'AZW3', 'TXT', 'MP3', 'M4B')], filters['format'])
    controls += select('status', 'Чтение', [('all', 'Все статусы'), ('unread', 'Не начато'), ('reading', 'Читаю'), ('finished', 'Прочитано')], filters['status'])
    controls += select('sort', 'Порядок', [('newest', 'Сначала новые'), ('title', 'По названию'), ('author', 'По автору'), ('series', 'По серии'), ('progress', 'По прогрессу')], filters['sort'])
    body += f'''<form class="catalog-toolbar" action="/" method="get"><label class="catalog-search-field">Поиск<input type="search" name="q" value="{esc(filters['q'])}" placeholder="Название, автор, серия…"></label>{controls}
<input type="hidden" name="view" value="{filters['view']}">{f'<input type="hidden" name="shelf" value="{filters["shelf"]}">' if filters.get('shelf') else ''}
<button class="button secondary">Найти</button><details class="more-filters" {'open' if filters['author'] or filters['series'] else ''}><summary>Автор и серия</summary><div>
{select('author', 'Автор', [('', 'Все авторы')] + [(v, v) for v in facets['author']], filters['author'])}
{select('series', 'Серия', [('', 'Все серии')] + [(v, v) for v in facets['series']], filters['series'])}</div></details></form>'''
    body += f'''<div class="catalog-results-bar"><span>{total} книг по выборке</span><div class="view-switch" role="group" aria-label="Вид каталога">
<a href="{esc(url(filters, view='grid', page=page))}" {'aria-current="true"' if filters['view'] == 'grid' else ''}>Сетка</a>
<a href="{esc(url(filters, view='list', page=page))}" {'aria-current="true"' if filters['view'] == 'list' else ''}>Список</a></div></div>'''
    cards = ''.join(book_card(b) for b in books)
    body += f'<div class="catalog-grid {"catalog-list" if filters["view"] == "list" else ""}">{cards}</div>' if cards else '<div class="catalog-empty"><h2>Книг не найдено</h2><p>Измените условия поиска или добавьте файл.</p><a class="text-link" href="/">Сбросить фильтры</a></div>'
    pagination = ''.join(f'<a class="button secondary small" href="{esc(url(filters, page=target))}">{label}</a>'
                         for target, label in [(page - 1, '← Назад'), (page + 1, 'Далее →')]
                         if target >= 1 and (target < page or page * 50 < total))
    body += f'<nav class="pagination" aria-label="Страницы библиотеки">{pagination}<span class="muted">Страница {page} из {max(1, (total + 49) // 50)}</span></nav>'
    return layout('Библиотека', body)


def book_page(book, progress, device, shelves, linked_shelves, highlights):
    book_id = book['id']
    percent = progress.get('progress', 0) * 100
    body = '<a class="back-to-catalog" href="/">← Библиотека</a>'
    body += f'''<section class="book-detail"><aside class="detail-cover">{cover(book['title'], book['author'], book_id, book_id)}
<dl class="file-facts"><dt>Формат</dt><dd>{esc(Path(book['filename']).suffix[1:].upper())}</dd><dt>Добавлено</dt><dd>{esc(book['created_at'][:10])}</dd></dl></aside>
<div class="detail-main"><span class="eyebrow">Личная библиотека / книга {book_id:03d}</span><h1>{esc(book['title'])}</h1>
<p class="detail-author">{esc(book['author'] or 'Неизвестный автор')}</p>
{f'<a class="text-link" href="{esc(url({"series": book["series"]}))}">{esc(book["series"])} · {book["series_index"]:g}</a>' if book['series'] else ''}
<div class="book-actions detail-actions"><a class="button" href="{read_link(book)}">{'Слушать' if Path(book['filename']).suffix.lower() in ('.mp3', '.m4b') else 'Продолжить чтение' if 0 < percent < 100 else 'Читать'}</a>
<a class="button secondary" href="/download/{book_id}">Скачать файл</a>
{f'<a class="text-link" href="/download/{book_id}/epub" data-export>Экспорт EPUB</a>' if Path(book['filename']).suffix.lower() not in ('.mp3', '.m4b') else ''}</div>
<div class="detail-progress"><div class="reading-meter"><progress value="{percent:.1f}" max="100" aria-label="Прочитано {percent:.0f}%"></progress><span>{percent:.0f}%</span></div>
<details><summary>Изменить прогресс</summary><form action="/books/{book_id}/progress" method="post" data-async><label>Прочитано, %<input type="number" name="percent" min="0" max="100" step="0.1" value="{percent:.1f}"></label><button class="button small">Сохранить</button><p class="form-message" role="status"></p></form></details></div>
<p class="detail-description">{esc(book['description'] or 'Аннотация пока не добавлена.')}</p>
<dl class="metadata-facts"><dt>Жанры</dt><dd>{esc(book['genres'] or '—')}</dd><dt>Год</dt><dd>{book['year'] or '—'}</dd><dt>ISBN</dt><dd>{esc(book['isbn'] or '—')}</dd></dl></div></section>'''
    fields = ''.join(f'<label>{label}<input name="{key}" value="{esc(book[key] if book[key] is not None else "")}" maxlength="{limit}" {"required" if key == "title" else ""}></label>'
                     for key, label, limit in [('title', 'Название', 500), ('author', 'Автор', 500), ('series', 'Серия', 500), ('genres', 'Жанры через запятую', 1000), ('year', 'Год издания', 4), ('isbn', 'ISBN', 50)])
    body += f'''<div class="detail-sections"><section class="workspace-section"><h2>Сведения о книге</h2><details class="metadata-editor"><summary>Редактировать метаданные</summary>
<form action="/books/{book_id}/metadata" method="post" data-async><div class="form-columns">{fields}<label>Номер в серии<input type="number" name="series_index" value="{book['series_index']:g}" min="0" max="100000" step="any"></label></div>
<label>Аннотация<textarea name="description" rows="5" maxlength="30000">{esc(book['description'])}</textarea></label>
<p class="muted">Изменения сохраняются в библиотеке. Исходный файл остаётся прежним.</p><button class="button">Сохранить сведения</button><p class="form-message" role="status"></p></form></details>
<form class="rating-form" action="/books/{book_id}/rating" method="post" data-async>{select('rating', 'Моя оценка', [(i, 'Без оценки' if not i else '★' * i) for i in range(6)], book['rating'])}
<label>Мой отзыв<textarea name="review" rows="3" maxlength="5000">{esc(book['review'])}</textarea></label><button class="button secondary small">Сохранить оценку</button><p class="form-message" role="status"></p></form></section>'''
    linked = {s['id'] for s in linked_shelves}
    shelf_forms = ''.join(f'''<form action="/books/{book_id}/shelf" method="post"><input type="hidden" name="shelf_id" value="{s['id']}"><input type="hidden" name="action" value="{'remove' if s['id'] in linked else 'add'}"><span>{esc(s['name'])}</span><button class="button secondary small">{'Убрать' if s['id'] in linked else 'Добавить'}</button></form>''' for s in shelves)
    body += f'<aside class="workspace-section"><h2>Полки</h2><div class="book-shelf-forms">{shelf_forms or "<p class=muted>Создайте полку, чтобы собрать книги вместе.</p>"}</div>{shelf_create()}<h2 class="subsection-title">Kindle и KOReader</h2>{kindle_confirmation(book_id, device)}</aside></div>'
    quotes = ''.join('<blockquote><p>' + esc(h['text']) + '</p>' +
                     ('<footer>' + esc(h['comment']) + '</footer>' if h['comment'] else '') + '</blockquote>' for h in highlights)
    body += f'<section class="workspace-section book-quotes"><div class="section-heading"><h2>Цитаты из книги</h2><a class="text-link" href="/highlights">Все цитаты →</a></div>{quotes or "<p class=muted>Сохранённые в веб-ридере цитаты появятся здесь.</p>"}</section>'
    return layout(book['title'], body)


def wishlist_page(wishes, book_choices, status, page, total):
    body = heading('Список чтения', f'{total} записей · Яндекс Книги и ваши находки')
    body += '''<details class="wishlist-add"><summary>+ Добавить запись</summary><form action="/wishlist" method="post" data-async><div class="form-columns"><label>Название<input name="title" required maxlength="1000"></label><label>Автор<input name="author" maxlength="1000"></label></div><button class="button">Добавить</button><p class="form-message" role="status"></p></form></details>'''
    body += '<form class="reconcile-form" action="/library/reconcile" method="post" data-async><button class="button secondary small">Сопоставить с библиотекой</button><a class="text-link" href="/integrations">Источники →</a><p class="form-message" role="status"></p></form>'
    body += '<nav class="wish-filters" aria-label="Статусы списка чтения">' + ''.join(f'<a href="/wishlist?status={key}" {"aria-current=page" if key == status else ""}>{label}</a>' for key, label in [('all', 'Все'), ('wanted', 'Хочу прочитать'), ('in_library', 'Есть файл'), ('on_kindle', 'На Kindle'), ('in_koreader', 'KOReader'), ('reading', 'Читаю'), ('finished', 'Прочитано')]) + '</nav>'
    for wish in wishes:
        link = f'/books/{wish["book_id"]}' if wish.get('book_id') else '/integrations?' + urlencode({'q': wish['title'], 'author': wish['author'], 'wish_id': wish['id']})
        body += f'''<article class="wish-row"><span class="wish-number">{wish['id']:02d}</span><div class="wish-info"><h2>{esc(wish['title'])}</h2><p>{esc(wish['author'] or 'Неизвестный автор')}</p>{wish_tracking(wish, book_choices)}<details><summary>Редактировать запись</summary><form class="edit-form" action="/wishlist/{wish['id']}/edit" method="post" data-async><label>Название<input name="title" value="{esc(wish['title'])}" required></label><label>Автор<input name="author" value="{esc(wish['author'])}"></label><button class="button small">Сохранить</button><p class="form-message" role="status"></p></form><form action="/wishlist/{wish['id']}/delete" method="post" data-async data-confirm><button class="delete-button">Удалить запись</button><p class="form-message" role="status"></p></form></details></div><a class="text-link" href="{esc(link)}">{'Открыть книгу' if wish.get('book_id') else 'Найти книгу'} →</a></article>'''
    if not wishes:
        body += '<div class="catalog-empty"><h2>В этой группе пока нет записей</h2><p>Добавьте книгу вручную или перенесите список из Яндекс Книг.</p></div>'
    body += '<nav class="pagination" aria-label="Страницы списка чтения">' + ''.join(f'<a class="button secondary small" href="/wishlist?status={status}&amp;page={p}">{label}</a>' for p, label in [(page-1, '← Назад'), (page+1, 'Далее →')] if p >= 1 and (p < page or page*50 < total)) + '</nav>'
    return layout('Список чтения', body, 'wishlist')


def dashboard_page(reading, recent, counts):
    body = heading('Моё чтение', 'Книги, к которым хочется вернуться', '<a class="button secondary" href="/">Открыть библиотеку →</a>')
    body += '<div class="reading-summary">' + ''.join(f'<span><strong>{value}</strong> {label}</span>' for value, label in zip(counts, ('в библиотеке', 'читаю', 'прочитано'))) + '</div>'
    body += '<section class="workspace-section"><div class="section-heading"><h2>Продолжить чтение</h2><a class="text-link" href="/?status=reading">Все текущие →</a></div>'
    body += '<div class="continue-grid">' + ''.join(book_card(b) for b in reading) + '</div>' if reading else '<p class="muted">Откройте книгу из библиотеки. Здесь появится её прогресс.</p>'
    body += '</section><section class="workspace-section"><div class="section-heading"><h2>Недавно добавлено</h2><a class="text-link" href="/">Весь каталог →</a></div><div class="catalog-grid">' + ''.join(book_card(b) for b in recent) + '</div></section>'
    return layout('Моё чтение', body, 'dashboard')
