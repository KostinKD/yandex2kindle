"""Owner-only guided configuration, live checks and Yandex sign-in assistance."""
import ipaddress
import secrets
import smtplib
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import configuration as config


def owner():
    from .auth import current_user
    user = current_user.get()
    if not user or not user['owner']:
        raise HTTPException(403, 'Настройки сервера доступны только владельцу')
    return user


router = APIRouter(dependencies=[Depends(owner)])
DEFAULTS = {'SMTP_PORT': '587', 'SMTP_SSL': 'false', 'SMTP_TLS': 'true', 'YANDEX_SYNC_INTERVAL_SECONDS': '0',
            'TELEGRAM_AUTO_SEND_TO_KINDLE': 'false'}
LABELS = {
    'YANDEX_BOOK_TOKEN': 'Токен Яндекс Книг', 'YANDEX_SYNC_INTERVAL_SECONDS': 'Обновлять список автоматически',
    'SMTP_HOST': 'Сервер исходящей почты', 'SMTP_PORT': 'Порт', 'SMTP_USER': 'Логин почты', 'SMTP_PASSWORD': 'Пароль приложения для почты',
    'SMTP_FROM': 'Адрес отправителя', 'SMTP_SSL': 'Шифрование',
    'FLIBUSTA_SEARCH_URL': 'Поиск по каталогу Flibusta', 'OPDS_SEARCH_URL': 'Другой каталог OPDS',
    'REMOTE_ALLOWED_ORIGINS': 'Дополнительные адреса скачивания', 'ZLIB_REMIX_USERID': 'Z-Lib: remix_userid', 'ZLIB_REMIX_USERKEY': 'Z-Lib: remix_userkey',
    'TELEGRAM_BOT_TOKEN': 'Токен бота от BotFather', 'TELEGRAM_ALLOWED_USERS': 'Ваш Telegram ID или имя пользователя',
    'TELEGRAM_AUTO_SEND_TO_KINDLE': 'Отправлять полученные книги на Kindle', 'APP_HOST_IP': 'Адрес компьютера в домашней сети',
    'KINDLE_EMAIL': 'Адрес Kindle по умолчанию',
}
TITLES = {'yandex': 'Яндекс Книги', 'smtp': 'Отправка книг на Kindle', 'catalogs': 'Внешние каталоги',
          'telegram': 'Telegram', 'device': 'Подключение устройства'}
HELP = {
    'yandex': 'Перенесите свой список чтения. Книги можно добавлять с компьютера и без подключения Яндекса.',
    'smtp': 'Для отправки на Kindle нужна ваша почта. В настройках почтового сервиса создайте отдельный пароль приложения и укажите данные исходящей почты. Обычный пароль от почты вводить не нужно. В аккаунте Amazon добавьте адрес отправителя в список разрешённых адресов Send to Kindle.',
    'catalogs': 'Настройте только каталоги, которыми пользуетесь. В адресе поиска место названия книги обозначается {searchTerms}. Необязательные настройки можно оставить пустыми.',
    'telegram': 'Откройте @BotFather в Telegram, отправьте /newbot и выполните его подсказки. Скопируйте выданный токен сюда. Укажите свой ID или имя без @: только эти пользователи смогут работать с библиотекой через бота. Новые настройки применяются в течение 30 секунд.',
    'device': 'Kindle и компьютер должны быть подключены к одной домашней сети. Адрес обычно определяется при запуске приложения. Почту Kindle можно найти в настройках устройства или аккаунта Amazon.',
}


def configured(section):
    if section == 'smtp':
        return bool(config.get('SMTP_HOST') and config.get('SMTP_FROM'))
    if section == 'catalogs':
        return bool(config.get('FLIBUSTA_SEARCH_URL') or config.get('OPDS_SEARCH_URL'))
    key = {'yandex': 'YANDEX_BOOK_TOKEN', 'telegram': 'TELEGRAM_BOT_TOKEN', 'device': 'APP_HOST_IP'}[section]
    return bool(config.get(key))


def public_state():
    keys = [key for group in config.GROUPS.values() for key in group]
    return {'values': {key: config.get(key, DEFAULTS.get(key, '')) for key in keys if key not in config.SECRETS},
            'secrets_configured': {key: bool(config.get(key)) for key in config.SECRETS},
            'sections': {s: configured(s) for s in config.GROUPS}, 'checks': config.checks()}


@router.get('/api/settings')
def settings_state():
    return public_state()


def input_field(key):
    from .ui import esc
    value = '' if key in config.SECRETS else config.get(key, DEFAULTS.get(key, ''))
    if key in ('SMTP_SSL', 'TELEGRAM_AUTO_SEND_TO_KINDLE', 'YANDEX_SYNC_INTERVAL_SECONDS'):
        options = [('false', 'Нет'), ('true', 'Да')]
        if key == 'SMTP_SSL':
            options = [('false', 'STARTTLS · обычно порт 587'), ('true', 'SSL · обычно порт 465')]
        elif key == 'YANDEX_SYNC_INTERVAL_SECONDS':
            options = [('0', 'Только по кнопке'), ('3600', 'Раз в час'), ('21600', 'Раз в 6 часов'), ('86400', 'Раз в день')]
        if value not in dict(options):
            options.append((value, f'Текущее значение: {value} с'))
        control = '<select name="' + key + '">' + ''.join(f'<option value="{esc(k)}" {"selected" if value == k else ""}>{esc(v)}</option>' for k, v in options) + '</select>'
    else:
        kind = 'password' if key in config.SECRETS else 'number' if key == 'SMTP_PORT' else 'email' if key in ('SMTP_FROM','KINDLE_EMAIL') else 'text'
        placeholder = 'Сохранён. Оставьте поле пустым, чтобы не менять.' if key in config.SECRETS and config.get(key) else ''
        control = f'<input name="{key}" type="{kind}" value="{esc(value)}" placeholder="{placeholder}" maxlength="8000" autocomplete="{"new-password" if key in config.SECRETS else "off"}" {"min=1 max=65535" if key == "SMTP_PORT" else ""}>'
    return f'<label>{LABELS[key]}{control}</label>'


@router.get('/settings', response_class=HTMLResponse)
def settings_page(saved: str = '', error: str = ''):
    from . import ui, library_ui
    body = library_ui.heading('Настройка библиотеки', 'Подключите только то, что вам нужно. Всё можно изменить позже.', '<a class="button secondary" href="/">Перейти к книгам →</a>')
    body += '<ol class="setup-steps"><li>Аккаунт создан</li><li>Добавьте книгу или подключите источник</li><li>Настройте Kindle по желанию</li></ol>'
    body += '<p class="setup-help">Для чтения на сайте ключи не нужны. Начните с загрузки EPUB или FB2 в библиотеке. Для доставки по почте и переноса списка из Яндекса используйте разделы ниже.</p>'
    if saved in TITLES:
        body += f'<p class="settings-result" role="status">{TITLES[saved]}: настройки сохранены. Ключи не нужно вводить заново при каждом запуске.</p>'
    body += '<nav class="settings-jump" aria-label="Разделы настройки">' + ''.join(f'<a href="#{key}">{title}</a>' for key,title in TITLES.items()) + '</nav>'
    checks = config.checks()
    for section, title in TITLES.items():
        status = 'Не подключено · можно пропустить'
        if configured(section):
            status = 'Сохранено · ещё не проверено'
        if section in checks:
            status = checks[section]['message']
        body += f'<section class="settings-section" id="{section}"><div class="section-heading"><h2>{title}</h2><span class="settings-status">{ui.esc(status)}</span></div><p>{HELP[section]}</p>'
        if section == 'yandex':
            body += '''<div class="yandex-signin"><a class="button secondary" href="/settings/yandex/connect" target="_blank" rel="noopener">Войти в Яндекс</a><ol><li>Откройте вход в Яндекс и разрешите доступ к списку книг.</li><li>После перехода скопируйте полный адрес из адресной строки той вкладки.</li><li>Вставьте его в поле ниже. Пароль Яндекса здесь не нужен.</li></ol>
<form action="/settings/yandex/finish" method="post" data-async data-return-anchor="yandex"><label>Адрес страницы после входа<input name="callback" type="password" autocomplete="new-password" required maxlength="12000"></label><button class="button" type="submit">Подключить Яндекс</button><p class="form-message" role="status"></p></form></div>'''
        if section == 'smtp':
            body += '''<label class="smtp-preset">Какая у вас почта?<select id="smtp-provider"><option value="other">Другой сервис / указать вручную</option><option value="yandex">Яндекс Почта</option><option value="mail">Почта Mail.ru</option></select></label><p class="muted">Для Яндекса и Mail.ru адрес сервера и порт заполним автоматически.</p>
<p class="settings-docs"><a href="https://yandex.ru/support/yandex-360/customers/mail/ru/mail-clients/mail-clients-troubleshooting.html" target="_blank" rel="noopener noreferrer">Помощь Яндекс Почты ↗</a> · <a href="https://help.mail.ru/mail/login/mailer/" target="_blank" rel="noopener noreferrer">Пароль приложения Mail.ru ↗</a> · <a href="https://support.google.com/accounts/answer/185833?hl=ru" target="_blank" rel="noopener noreferrer">Пароль приложения Google ↗</a></p>'''
        body += f'<form action="/settings/{section}" method="post" data-async data-return-anchor="{section}" class="settings-form"><div class="form-columns">'
        for key in config.GROUPS[section]:
            if key == 'SMTP_TLS':
                continue
            body += input_field(key)
        body += '</div><p class="muted">Пустое поле ключа сохраняет уже введённый ключ. Для отключения используйте кнопку ниже.</p><button class="button" type="submit">Сохранить</button><p class="form-message" role="status"></p></form>'
        if section in ('yandex', 'smtp', 'telegram', 'catalogs'):
            body += f'<div class="settings-actions"><form action="/settings/check/{section}" method="post" data-async data-return-anchor="{section}"><button class="button secondary" type="submit">Проверить подключение</button><p class="form-message" role="status"></p></form><form action="/settings/{section}/disable" method="post" data-async data-return-anchor="{section}" data-confirm><button class="delete-button" type="submit">Отключить {title}</button><p class="form-message" role="status"></p></form></div>'
        if section == 'device':
            body += '<a class="text-link" href="/devices">Показать адреса подключения и инструкцию →</a>'
        body += '</section>'
    body += '<script src="/static/settings.js?v=1" defer></script>'
    response = HTMLResponse(ui.layout('Настройки', body, 'settings'))
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def validate(section, values):
    def number(key, lower, upper):
        if key in values:
            try:
                parsed = int(values[key])
            except ValueError:
                raise ValueError('Введите целое число в поле «' + LABELS.get(key,key) + '»') from None
            if not lower <= parsed <= upper:
                raise ValueError('Проверьте значение в поле «' + LABELS.get(key,key) + '»')
    number('SMTP_PORT', 1, 65535)
    number('YANDEX_SYNC_INTERVAL_SECONDS', 0, 604800)
    for key in ('SMTP_SSL', 'TELEGRAM_AUTO_SEND_TO_KINDLE'):
        if key in values and values[key] not in ('true','false'):
            raise ValueError('Выберите значение из списка')
    for key in ('FLIBUSTA_SEARCH_URL', 'OPDS_SEARCH_URL'):
        if values.get(key):
            address = urlsplit(values[key])
            if address.scheme not in ('http','https') or not address.hostname or address.username or '{searchTerms}' not in values[key]:
                raise ValueError('Адрес каталога должен начинаться с http:// или https:// и содержать {searchTerms}')
    if values.get('SMTP_HOST') and any(c in values['SMTP_HOST'] for c in '/@\r\n '):
        raise ValueError('Укажите только имя почтового сервера, например smtp.example.com')
    for key in ('SMTP_FROM','KINDLE_EMAIL'):
        if values.get(key):
            # Delivery itself remains the final validator for recipients.
            import re
            if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', values[key]):
                raise ValueError('Проверьте адрес электронной почты')
    if values.get('APP_HOST_IP'):
        try:
            ipaddress.ip_address(values['APP_HOST_IP'])
        except ValueError:
            raise ValueError('Адрес компьютера выглядит как 192.168.1.25. Его можно оставить пустым.') from None
    if section == 'smtp':
        values['SMTP_TLS'] = 'true'
    if section == 'telegram' and (values.get('TELEGRAM_BOT_TOKEN') or config.get('TELEGRAM_BOT_TOKEN')) and not values.get('TELEGRAM_ALLOWED_USERS', config.get('TELEGRAM_ALLOWED_USERS')):
        raise ValueError('Укажите свой Telegram ID или имя пользователя, чтобы бот работал только с вашей библиотекой')


@router.post('/settings/{section}/disable')
def disable(section: str):
    if section not in config.GROUPS:
        raise HTTPException(404, 'Раздел не найден')
    primary = {'yandex': 'YANDEX_BOOK_TOKEN', 'smtp': 'SMTP_HOST', 'telegram': 'TELEGRAM_BOT_TOKEN'}
    values = {primary[section]: ''} if section in primary else {key: '' for key in config.GROUPS[section]}
    config.save(section, values, replace_secrets=True)
    return RedirectResponse('/settings#' + section, status_code=303)


@router.post('/settings/{section}')
async def save_settings(section: str, request: Request):
    if section not in config.GROUPS:
        raise HTTPException(404, 'Раздел не найден')
    form = await request.form()
    if any(key not in config.GROUPS[section] for key in form):
        raise HTTPException(422, 'Неизвестное поле настройки')
    values = {key: str(value).strip() for key,value in form.items()}
    if any(len(value) > 8000 for value in values.values()):
        raise HTTPException(422, 'Слишком длинное значение настройки')
    try:
        validate(section, values)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    config.save(section, values)
    return RedirectResponse('/settings?saved=' + section + '#' + section, status_code=303)


@router.get('/settings/yandex/connect')
def yandex_connect(request: Request):
    from .yandex_auth import authorization_url
    state = secrets.token_urlsafe(32)
    response = RedirectResponse(authorization_url(state), status_code=303)
    response.set_cookie('y2k_yandex_state', state, max_age=600, httponly=True, samesite='strict', secure=request.url.scheme == 'https', path='/settings')
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@router.post('/settings/yandex/finish')
def yandex_finish(request: Request, callback: str = Form(...)):
    from .yandex_auth import extract_token
    state = request.cookies.get('y2k_yandex_state', '')
    if not state or len(callback) > 12000:
        raise HTTPException(422, 'Сначала нажмите «Войти в Яндекс». Ссылка действует 10 минут.')
    try:
        token = extract_token(callback, state)
    except ValueError:
        raise HTTPException(422, 'Не удалось подтвердить вход. Откройте «Войти в Яндекс» ещё раз и вставьте полный адрес после входа.') from None
    config.save('yandex', {'YANDEX_BOOK_TOKEN': token})
    response = RedirectResponse('/settings?saved=yandex#yandex', status_code=303)
    response.delete_cookie('y2k_yandex_state', path='/settings')
    return response


@router.post('/settings/check/{section}')
def check_connection(section: str):
    if section not in ('yandex','smtp','telegram','catalogs'):
        raise HTTPException(404, 'Раздел не найден')
    message, ok = 'Сначала сохраните настройки этого подключения.', False
    if configured(section):
        try:
            if section == 'yandex':
                from . import yandex
                with httpx.Client(headers={'Auth-Token': config.get('YANDEX_BOOK_TOKEN'), 'App-Language':'ru'}, timeout=15, trust_env=False) as client:
                    profile = yandex.request_json(client, '/profile')
                    if not isinstance(profile.get('user'),dict) or not profile['user'].get('login'):
                        raise ValueError('unexpected profile')
            elif section == 'smtp':
                from .smtp_delivery import smtp_connection
                with smtp_connection():
                    pass
            elif section == 'telegram':
                with httpx.Client(timeout=15, trust_env=False) as client:
                    result = client.get('https://api.telegram.org/bot' + config.get('TELEGRAM_BOT_TOKEN') + '/getMe')
                    if not result.is_success or not result.json().get('ok'):
                        raise ValueError('bad token')
            else:
                from .integrations import search
                source = 'opds' if config.get('OPDS_SEARCH_URL') else 'flibusta'
                search(source, '', '', 0)
            ok, message = True, 'Подключение проверено. Всё готово.'
        except Exception:
            message = {'yandex':'Яндекс не подтвердил доступ. Повторите вход и проверьте подключение к интернету.',
                       'smtp':'Не удалось войти на почтовый сервер. Проверьте адрес, порт и пароль приложения.',
                       'telegram':'Telegram не подтвердил токен. Скопируйте его заново у BotFather.',
                       'catalogs':'Каталог недоступен. Проверьте его адрес и интернет.'}[section]
    config.checked(section, ok, message)
    return RedirectResponse('/settings#' + section, status_code=303)
