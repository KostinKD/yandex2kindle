"""Reading home, wishlist and device setup pages."""
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get('/dashboard', response_class=HTMLResponse)
def dashboard():
    from . import main, catalog, library_ui
    with main.connect() as db:
        reading, _ = catalog.list_books(db, limit=4, status='reading', sort='last_read')
        recent, total = catalog.list_books(db, limit=8)
        counts = db.execute('SELECT COUNT(CASE WHEN progress>0 AND progress<1 THEN 1 END),'
                            'COUNT(CASE WHEN progress=1 THEN 1 END) FROM reading_progress').fetchone()
        return library_ui.dashboard_page(reading, recent, (total, *counts))


@router.get('/wishlist', response_class=HTMLResponse)
def wishlist(status: str = Query('all', pattern='^(all|wanted|in_library|on_kindle|in_koreader|reading|finished)$'),
             page: int = Query(1, ge=1)):
    from . import main, library_state, library_ui
    with main.connect() as db:
        wishes = library_state.wishlist_rows(db, status, limit=50, offset=(page-1)*50)
        total = library_state.wishlist_count(db, status)
        choices = db.execute('SELECT id,title,author FROM books ORDER BY id DESC LIMIT 500').fetchall()
        return library_ui.wishlist_page(wishes, choices, status, page, total)


@router.get('/devices', response_class=HTMLResponse)
def devices(request: Request):
    from . import ui, library_ui
    body = library_ui.heading('Устройства', 'Kindle, OPDS и синхронизация KOReader')
    body += ui.kindle_panel(ui.connection_info(request))
    body += '''<section class="workspace-section"><h2>Чтение на Kindle</h2><p>Для встроенного браузера используйте облегчённую версию. Для KOReader добавьте личный OPDS-каталог и настройте встроенную синхронизацию прогресса.</p>
<div class="device-actions"><a class="button secondary" href="/kindle">Версия для Kindle</a><a class="text-link" href="/accounts">Учётные данные →</a><a class="text-link" href="/plugins/yandex2kindle.koplugin.zip">Скачать плагин KOReader 1.1</a></div>
<p class="muted">Плагин отправляет цитаты из открытой книги на сайт и загружает общий список цитат в меню «Цитаты книги с сайта». Укажите в нём URL сервера, логин и пароль синхронизации со страницы «Аккаунт». Синхронизацию прогресса включает встроенный модуль KOReader.</p>
<p class="muted">Общий список цитат доступен на устройстве отдельно от выделений в тексте: преобразование web-CFI в XPointer плагин не выполняет. Для штатной читалки Kindle используйте импорт My Clippings.txt в разделе «Цитаты».</p></section>'''
    return ui.layout('Устройства', body, 'devices')
