"""First-owner bootstrap. The launcher transfers its one-time key in a fragment."""
import os
import secrets
import sqlite3

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()


def has_owner():
    from .auth import accounts
    with accounts() as db:
        return db.execute('SELECT 1 FROM accounts WHERE owner=1').fetchone() is not None


def key_path():
    from . import main
    return main.DB.parent / 'setup.key'


def ensure_key():
    if has_owner():
        return ''
    target = key_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return target.read_text().strip()
    with os.fdopen(fd, 'w') as output:
        key = secrets.token_urlsafe(32)
        output.write(key)
    return key


@router.get('/setup', response_class=HTMLResponse)
def setup_page():
    if has_owner():
        return RedirectResponse('/login', status_code=303)
    from . import ui
    body = '''<section class="setup-welcome"><span class="eyebrow">Первый запуск · 1 из 2</span><h1>Ваша библиотека готова.</h1>
<p>Создайте аккаунт владельца. Затем можно добавить книгу или подключить Яндекс и Kindle — ключи пока не нужны.</p>
<form action="/setup" method="post" class="setup-form"><input name="bootstrap_token" type="hidden" id="bootstrap-token">
<p id="setup-key-help" class="setup-help" hidden>Откройте библиотеку через файл запуска в её папке. Он безопасно передаст право создать первый аккаунт. Если вы открыли адрес вручную, закройте эту вкладку и запустите приложение ещё раз.</p>
<label>Придумайте логин<input name="username" required minlength="3" maxlength="64" pattern="[A-Za-z0-9_.-]{3,64}" autocomplete="username" placeholder="Например, reader_home"><small>Латинские буквы и цифры, от 3 символов.</small></label>
<label>Пароль<input name="password" type="password" required minlength="12" maxlength="256" autocomplete="new-password"><small>Не меньше 12 символов. Сохраните его — он нужен для входа.</small></label>
<label>Повторите пароль<input name="password_confirm" type="password" required minlength="12" maxlength="256" autocomplete="new-password"></label>
<button class="button" type="submit">Создать библиотеку</button><p class="form-message" role="status"></p></form></section>
<script src="/static/setup.js?v=1" defer></script>'''
    response = HTMLResponse(ui.layout('Первый запуск', body, 'setup'))
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@router.post('/setup')
def create_owner(request: Request, bootstrap_token: str = Form(''), username: str = Form(..., max_length=64),
                 password: str = Form(...), password_confirm: str = Form(...)):
    from . import auth
    from urllib.parse import urlsplit
    if has_owner():
        raise HTTPException(409, 'Владелец уже создан. Войдите в существующий аккаунт.')
    origin = request.headers.get('origin')
    if (origin and urlsplit(origin).netloc != request.url.netloc) or request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Откройте страницу настройки из файла запуска приложения.')
    try:
        expected = key_path().read_text().strip()
    except FileNotFoundError:
        expected = ''
    if not expected or not secrets.compare_digest(expected.encode(), bootstrap_token.encode()):
        raise HTTPException(403, 'Откройте библиотеку через файл запуска приложения и повторите настройку.')
    if password != password_confirm:
        raise HTTPException(422, 'Пароли не совпадают. Введите один и тот же пароль в обоих полях.')
    try:
        auth.create_account(username.strip(), password, owner=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except sqlite3.IntegrityError:
        raise HTTPException(409, 'Владелец уже создан или логин занят. Войдите в аккаунт.') from None
    key_path().unlink(missing_ok=True)
    user = auth.authenticate(username.strip(), password)
    return auth.session_response(request, user, '/settings')


def cli():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://localhost:8000')
    args = parser.parse_args()
    key = ensure_key()
    print(args.url.rstrip('/') + ('/setup#' + key if key else '/'))


if __name__ == '__main__':
    cli()
