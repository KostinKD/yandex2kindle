"""Shared, dependency-free HTML views for the local library."""
from . import __version__, configuration
import html
import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlencode

from .catalogs import ZLIB_MIRRORS


def esc(value):
    return html.escape(str(value), quote=True)


def icon(name):
    paths = {"book": '<path d="M12 6c-3-2-7-2-10-1v14c3-1 7-1 10 1 3-2 7-2 10-1V5c-3-1-7-1-10 1Zm0 0v14"/>',
             "plus": '<path d="M12 5v14M5 12h14"/>',
             "up": '<path d="M12 19V5m-6 6 6-6 6 6"/>',
             "arrow": '<path d="M5 12h14m-6-6 6 6-6 6"/>',
             "download": '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
             "search": '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
             "read": '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
             "quote": '<path d="M3 21c3 0 7-1 7-8V5c0-1.25-.75-2-2-2H4c-1.25 0-2 .75-2 2v6c0 1.25.75 2 2 2 0 4-1 6-1 8zm14 0c3 0 7-1 7-8V5c0-1.25-.75-2-2-2h-4c-1.25 0-2 .75-2 2v6c0 1.25.75 2 2 2 0 4-1 6-1 8z"/>',
             "device": '<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M10 18h4"/>'}
    return f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths.get(name, paths["book"])}</svg>'


def layout(title, body, active="library"):
    from .auth import current_user
    user = current_user.get()
    entries = [('dashboard', '/dashboard', 'Моё чтение'), ('library', '/', 'Библиотека'),
               ('wishlist', '/wishlist', 'Список чтения'), ('highlights', '/highlights', 'Цитаты'),
               ('stats', '/stats', 'Статистика')]
    if user and user['owner']:
        entries.append(('sources', '/integrations', 'Источники'))
        entries.append(('settings', '/settings', 'Настройки'))
    entries.extend([('devices', '/devices', 'Устройства'), ('accounts', '/accounts', 'Аккаунт')])
    navigation = ''.join(f'<a href="{href}" {"aria-current=page" if key == active else ""}><span class="nav-index">{index:02d}</span>{label}</a>'
                         for index, (key, href, label) in enumerate(entries, 1))
    guidance = ''
    if user and user['owner'] and active in ('library','dashboard') and not configuration.get('YANDEX_BOOK_TOKEN') and not configuration.get('SMTP_HOST'):
        guidance = '<aside class="setup-notice">Уже можно добавлять и читать книги. <a href="/settings">Подключить Яндекс или Kindle →</a></aside>'
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Личная библиотека, список чтения и книги для Kindle."><title>{esc(title)} · Yandex2Kindle</title>
<meta name="color-scheme" content="light dark"><link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#a64a31"><meta name="apple-mobile-web-app-capable" content="yes">
<script src="/static/theme.js"></script><link rel="icon" type="image/svg+xml" href="/static/favicon.svg"><link rel="stylesheet" href="/static/app.css?v=compact-2"><link rel="stylesheet" href="/static/catalog-search.css?v=2"><link rel="stylesheet" href="/static/workspace.css?v=6">
<script src="/static/clear-fields.js" defer></script><script src="/static/catalog-search.js?v=2" defer></script><script src="/static/app.js?v=compact-4" defer></script></head>
<body><a class="skip" href="#main">К содержимому</a><header class="topbar"><a class="brand" href="/"><span class="brand-mark" aria-hidden="true">y<span>2</span>k.</span><span class="brand-name">yandex2kindle<span class="brand-caption">Личная библиотека</span></span></a>
<div class="header-actions"><a class="kindle-version-link" href="/kindle">Версия для Kindle ↗</a><label class="theme-control"><span>Тема</span><select id="theme-select" aria-label="Цветовая тема"><option value="system">Как в системе</option><option value="light">Светлая</option><option value="dark">Тёмная</option></select></label><button class="button secondary menu-toggle" type="button" aria-controls="app-navigation" aria-expanded="false" data-menu>Меню</button></div></header>
<div class="app-shell"><nav class="app-navigation" id="app-navigation" aria-label="Основная навигация"><span class="nav-caption">Каталог и чтение</span>{navigation}<div class="nav-footer"><span>Ваши книги.<br>На вашем сервере.</span><a href="/opds">Каталог OPDS ↗</a><a class="github-link" href="https://github.com/KostinKD/yandex2kindle" target="_blank" rel="noopener noreferrer" aria-label="Проект на GitHub — откроется в новой вкладке">GitHub проекта ↗</a><a class="boosty-badge" href="https://boosty.to/kostin_kd/donate" target="_blank" rel="noopener noreferrer" aria-label="Поддержать проект на Boosty — откроется в новой вкладке"><span class="boosty-symbol" aria-hidden="true">♥</span><span>Поддержать на <strong>Boosty</strong></span><span aria-hidden="true">↗</span></a></div></nav>
<main id="main" class="container">{guidance}{body}</main></div><footer class="footer"><span>yandex2kindle · v{__version__} · личная библиотека</span><span><a href="/kindle">Версия для Kindle</a> / <a href="/docs">API</a> / <a href="/opds">OPDS</a></span></footer>
<button type="button" class="scroll-to-top" aria-label="Наверх" title="Наверх" hidden>{icon('up')}</button><div id="notification" class="notification" role="status" aria-live="polite" hidden></div></body></html>'''


def connection_info(request):
    host = configuration.get("APP_HOST_IP", "").strip()
    candidate = request.url.hostname or ""
    if not host:
        try:
            address = ipaddress.ip_address(candidate)
            if not address.is_loopback and not address.is_unspecified:
                host = candidate
        except ValueError:
            pass
    if not host and not Path("/.dockerenv").exists():
        try:
            # Routing lookup only: UDP connect sends no packets.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("192.0.2.1", 80))
                host = sock.getsockname()[0]
        except OSError:
            pass
    try:
        address = ipaddress.ip_address(host)
        if address.is_loopback or address.is_unspecified:
            host = ""
    except ValueError:
        host = ""
    port = configuration.get("APP_HOST_PORT", "").strip() or str(request.url.port or (443 if request.url.scheme == "https" else 80))
    authority = f"[{host}]" if ":" in host else host
    base = f"{request.url.scheme}://{authority}:{port}" if host else None
    return {"host_ip": host or None, "opds_url": base + "/opds" if base else None, "sync_url": base}


def kindle_panel(connection):
    if connection["host_ip"]:
        addresses = "".join(f'''<label class="copy-label">{label}<span class="copy-field"><input readonly aria-label="{label}" value="{esc(connection[key])}"><button type="button" class="button small secondary" data-copy>Копировать</button></span></label>'''
                            for label, key in (("Каталог OPDS", "opds_url"), ("Сервер синхронизации", "sync_url")))
        content = f'<span class="host-ip">IP компьютера <strong>{esc(connection["host_ip"])}</strong></span>{addresses}'
    else:
        content = '<p>Чтобы показать IP компьютера, запустите сервис через <code>./scripts/start.sh</code> или задайте <code>HOST_IP</code> на странице «Настройки».</p>'
    return f'''<section class="kindle-panel" id="kindle"><div><span class="eyebrow">С компьютера — на ридер</span><h2>{icon('device')} Подключить Kindle</h2><p>Откройте KOReader → OPDS-каталоги и добавьте адрес каталога, логин и пароль от сайта. Каталог и файлы доступны после входа в ваш аккаунт.</p><p class="muted">Для прогресса: настройки синхронизации → собственный сервер. Логин синхронизации указан на странице «Аккаунт». У существующего аккаунта reader сохраняется прежний пароль.</p></div><div class="connection">{content}</div></section>'''


def cover(title, author="", index=0, book_id=None):
    typographic = f'<div class="book-cover cover-{index % 4}" aria-hidden="true"><span class="cover-line">Личная библиотека</span><span class="cover-title">{esc(title)}</span><span class="cover-author">{esc(author or "Без автора")}</span><span class="cover-symbol">y<span>2</span>k.</span></div>'
    if not book_id:
        return typographic
    return f'<div class="book-cover-wrapper"><img class="book-cover-img" src="/covers/{book_id}" alt="{esc(title)}" loading="lazy" onerror="this.remove()">{typographic}</div>'


def kindle_confirmation(book_id, state):
    confirmed = bool(state.get('kindle_confirmed_at'))
    label = 'Убрать отметку Kindle' if confirmed else 'Уже на Kindle'
    info = '<span class="tracking-badge">На Kindle</span>' if confirmed else ''
    if state.get('email_sent_at'):
        info += '<span class="tracking-badge">Отправлено по email</span>'
    if state.get('reader_seen_at'):
        info += '<span class="tracking-badge">Открыто в KOReader</span>'
    from .smtp_delivery import is_smtp_configured
    smtp_block = ''
    from .auth import current_user
    user = current_user.get()
    if not is_smtp_configured() and user and user['owner']:
        smtp_block = '<p><a class="text-link" href="/settings#smtp">Настроить отправку на Kindle →</a></p>'
    if is_smtp_configured():
        from .auth import current_user
        u = current_user.get()
        k_email = (u.get("kindle_email") if u else "") or configuration.get("KINDLE_EMAIL", "")
        btn_text = f'Отправить на {esc(k_email)}' if k_email else 'Отправить на Kindle'
        smtp_block = f'<form action="/books/{book_id}/send-to-kindle" method="post" data-async style="margin-bottom:8px"><button class="button small secondary" title="Отправить EPUB на Kindle">{btn_text}</button><p class="form-message" role="status"></p></form>'
    return f'''<div class="device-tracking">{info}<details><summary>Учёт и доставка Kindle</summary>{smtp_block}<p class="muted">Письмо отправляется через SMTP. Наличие книги на устройстве подтвердите вручную.</p><form action="/books/{book_id}/kindle" method="post" data-async><input type="hidden" name="on_device" value="{'false' if confirmed else 'true'}"><button class="button secondary small">{label}</button><p class="form-message" role="status"></p></form></details></div>'''


def wish_tracking(wish, book_choices):
    book_id = wish.get('book_id')
    badge = f'<span class="tracking-badge">{esc(wish.get("status_label", "Хочу прочитать"))}</span>'
    names = {'pending': 'хочу прочитать', 'reading': 'читаю', 'finished': 'прочитано'}
    for state in wish.get('yandex_states', []):
        badge += f'<span class="remote-state">В Яндексе: {esc(names.get(state, state))}</span>'
    options = '<option value="">Выберите книгу из библиотеки</option>'
    choices = list(book_choices)
    if book_id and not any(book['id'] == book_id for book in choices):
        choices.append({'id': book_id, 'title': wish.get('book_title') or wish['title'], 'author': wish.get('book_author') or ''})
    for book in choices:
        options += f'<option value="{book["id"]}" {"selected" if book["id"] == book_id else ""}>{esc(book["title"])} — {esc(book["author"])} · #{book["id"]}</option>'
    link = f'''<details class="wish-linker"><summary>{'Изменить привязку' if book_id else 'Связать с книгой в библиотеке'}</summary><form action="/wishlist/{wish['id']}/book" method="post" data-async><label>Книга<select name="book_id" required>{options}</select></label><button class="button secondary small">Подтвердить связь</button><p class="form-message" role="status"></p></form></details>''' if choices else ''
    unlink = f'<form action="/wishlist/{wish["id"]}/unlink" method="post" data-async><button class="delete-button">Убрать неверную связь</button><p class="form-message" role="status"></p></form>' if book_id else ''
    return f'<div class="wish-tracking">{badge}{link}{unlink}{kindle_confirmation(book_id, wish) if book_id else ""}</div>'


def catalog_mirror_links(source):
    mirrors = {"flibusta": ("https://flibusta.is", "https://flibusta.site"),
               "zlib": ZLIB_MIRRORS}.get(source, ())
    if not mirrors:
        return ""
    links = ' · '.join(f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(url.split("//")[1])} ↗</a>'
                       for url in mirrors)
    return f'<details class="catalog-note"><summary>Зеркала каталога — открыть вручную</summary><p>{links}</p></details>'


def sources_page(q, author, source, page, items, error, status, wish_id=None):
    configured = status['yandex_configured']
    state = next((row for row in status['states'] if row['name'] == 'yandex'), None)
    state_text = 'Можно синхронизировать библиотеку' if configured else 'Подключите Яндекс в настройках — сайт поможет получить доступ к списку книг.'
    if state:
        if state['status'] == 'error':
            state_text = state['message']
        elif state['status'] == 'running':
            state_text = 'Синхронизация выполняется…'
        else:
            state_text = 'Библиотека синхронизирована'
    results = []
    for i, item in enumerate(items):
        results.append(f'''<article class="result-row"><div class="result-icon">{icon('book')}</div><div class="result-info"><span class="format">{esc(item['suffix'][1:].upper())}</span><h3>{esc(item['title'])}</h3><p>{esc(item['author'] or 'Неизвестный автор')}</p><span class="match-score">Совпадение {item['score']:.0%}</span></div><form action="/integrations/import" method="post" data-async><input type="hidden" name="candidate_id" value="{esc(item['id'])}">{f'<input type="hidden" name="wish_id" value="{int(wish_id)}">' if wish_id else ''}<button class="button secondary small">{icon('download')} В библиотеку</button><p class="form-message" role="status"></p></form></article>''')
    if not results:
        results.append(f'<div class="empty-state">{icon("search")}<h3>{"Ничего не нашлось" if q or author else "Какая книга у вас на примете?"}</h3><p>{"Попробуйте уточнить название, автора или выбрать другой каталог." if q or author else "Введите название или автора, либо оставьте поля пустыми. Покажем доступные издания, а вы выберете подходящее."}</p></div>')
    body = f'''<section class="page-intro sources-intro"><div><span class="eyebrow">За пределами вашей полки</span><h1>Новые книги.<br><em>Знакомое место.</em></h1><p>Перенесите свой список чтения и найдите книги для библиотеки.</p></div><span class="intro-mark" aria-hidden="true">↗</span></section>
<section class="yandex-panel"><div class="yandex-logo" aria-hidden="true">Я</div><div class="yandex-info"><h2>Яндекс Книги <span class="status-tag">{'Подключено' if configured else 'Не настроено'}</span></h2><p class="{'error-text' if state and state['status'] == 'error' else 'muted'}">{esc(state_text)}</p><p class="muted">Книги из аккаунта появятся в списке «Хочу прочитать».</p></div><form action="/integrations/yandex/sync" method="post" data-async><button class="button secondary" {'disabled' if not configured or (state and state['status'] == 'running') else ''}>Синхронизировать {icon('arrow')}</button><p class="form-message" role="status"></p></form></section>
<section class="catalog-section"><div class="section-heading"><h2>Поиск по каталогам</h2><span class="muted">Вы выбираете издание</span></div><form class="catalog-search" method="get" action="/integrations"><noscript><p>Для поиска без JavaScript нажмите <button formaction="/integrations/results">Найти без JavaScript</button>.</p></noscript>{f'<input type="hidden" name="wish_id" value="{int(wish_id)}">' if wish_id else ''}<label>Название книги<input name="q" value="{esc(q)}" maxlength="500" placeholder="Необязательно"></label><label>Автор<input name="author" value="{esc(author)}" maxlength="500" placeholder="Необязательно"></label><label>Каталог<select name="source"><option value="flibusta" {'selected' if source == 'flibusta' else ''}>Flibusta{' · не настроена' if not status['flibusta_configured'] else ''}</option><option value="opds" {'selected' if source == 'opds' else ''}>Другой OPDS{' · не настроен' if not status['opds_configured'] else ''}</option><option value="zlib" {'selected' if source == 'zlib' else ''}>Z-Lib</option></select></label><label class="page-field">Страница<input type="number" name="page" min="0" max="1000" value="{page}"></label><button class="button">{icon('search')} Найти</button></form>
<div id="catalog-results" aria-live="polite">{f'<p class="error-banner" role="alert">{esc(error)}</p>' if error else ''}<div class="search-results">{''.join(results)}</div></div>{catalog_mirror_links(source)}<p class="catalog-note">Выберите подходящее издание: приложение скачает файл и добавит его в библиотеку. Используйте книги, на передачу которых у вас есть права.</p></section>'''
    return layout('Источники', body, 'sources')
