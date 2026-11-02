"""Private libraries: server-side sessions, reader Basic auth, isolated storage."""
import base64
from contextlib import contextmanager
from contextvars import ContextVar
import getpass
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

current_user = ContextVar("library_user", default=None)
router = APIRouter()
COOKIE = "y2k_session"


@contextmanager
def accounts():
    from . import main
    main.DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(main.DB.parent / "accounts.sqlite", timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
          id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
          salt TEXT NOT NULL, password_hash TEXT NOT NULL, owner INTEGER NOT NULL DEFAULT 0,
          kindle_email TEXT NOT NULL DEFAULT '');
        CREATE UNIQUE INDEX IF NOT EXISTS one_owner ON accounts(owner) WHERE owner=1;
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS login_attempts (
          client TEXT PRIMARY KEY, count INTEGER NOT NULL, until REAL NOT NULL);
    """)
    cols = [r[1] for r in db.execute("PRAGMA table_info(accounts)").fetchall()]
    if "kindle_email" not in cols:
        db.execute("ALTER TABLE accounts ADD COLUMN kindle_email TEXT NOT NULL DEFAULT ''")
        db.commit()
    try:
        with db:
            yield db
    finally:
        db.close()


def digest(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


def create_account(username, password, owner=False):
    import os
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username) or not 12 <= len(password) <= 256:
        raise ValueError("Логин: 3–64 латинских символа, цифры, _, . или -. Пароль: 12–256 символов.")
    if not owner and username == os.environ.get("KOSYNC_LIBRARY_USER", "reader"):
        raise ValueError("Этот логин зарезервирован для синхронизации библиотеки владельца")
    salt = secrets.token_hex(16)
    with accounts() as db:
        db.execute("INSERT INTO accounts(username,salt,password_hash,owner) VALUES(?,?,?,?)",
                   (username, salt, digest(password, salt), int(owner)))
        user = dict(db.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone())
    from . import main, kosync
    from .auth import current_user as context_user
    context = context_user.set(user)
    try:
        with main.connect() as db:
            sync_salt = secrets.token_hex(16)
            db.execute("INSERT OR IGNORE INTO kosync_users VALUES(?,?,?)",
                       (sync_username(user), sync_salt, kosync.password_hash(hashlib.md5(password.encode()).hexdigest(), sync_salt)))
    finally:
        context_user.reset(context)


def authenticate(username, password):
    if len(password) > 256:
        return None
    with accounts() as db:
        row = db.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
    hashed = digest(password, row["salt"] if row else "00" * 16)
    return dict(row) if row and hmac.compare_digest(hashed, row["password_hash"]) else None


def limited(client):
    with accounts() as db:
        row = db.execute("SELECT * FROM login_attempts WHERE client=?", (client,)).fetchone()
        return bool(row and row["until"] > time.time() and row["count"] >= 10)


def record_attempt(client, success):
    with accounts() as db:
        if success:
            db.execute("DELETE FROM login_attempts WHERE client=?", (client,))
        else:
            db.execute("""INSERT INTO login_attempts VALUES(?,1,?) ON CONFLICT(client) DO UPDATE SET
                count=CASE WHEN until<? THEN 1 ELSE count+1 END,
                until=CASE WHEN until<? THEN excluded.until ELSE until END""",
                (client, time.time()+900, time.time(), time.time()))


class PrivateLibraryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        from . import main
        path = request.url.path
        # Protocol endpoints authenticate inside their own isolated library.
        public = path in ("/login", "/kindle/login", "/health", "/healthcheck", "/forgot-password", "/setup", "/manifest.webmanifest", "/sw.js") or path.startswith("/static/")
        if public:
            return await call_next(request)
        client = request.client.host if request.client else "unknown"
        user = None
        token = request.cookies.get(COOKIE, "")
        if token and len(token) <= 100:
            with accounts() as db:
                row = db.execute("""SELECT a.* FROM accounts a JOIN sessions s ON a.id=s.user_id
                    WHERE s.token_hash=? AND s.expires>?""",
                    (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
                user = dict(row) if row else None
        basic = False
        webdav = path == "/webdav" or path.startswith("/webdav/")
        sync = path.startswith("/syncs/") or path == "/users/auth"
        if sync:
            if limited(client):
                return JSONResponse({"detail": "Слишком много попыток"}, status_code=429)
            name = request.headers.get("x-auth-user", "")
            with accounts() as db:
                row = db.execute("SELECT * FROM accounts WHERE username=?", (name,)).fetchone()
                if not row:
                    import os
                    if name == os.environ.get("KOSYNC_LIBRARY_USER", "reader"):
                        row = db.execute("SELECT * FROM accounts WHERE owner=1").fetchone()
            user = dict(row) if row else None
        elif not user and request.headers.get("authorization", "").lower().startswith("basic "):
            if limited(client):
                return JSONResponse({"detail": "Слишком много попыток. Подождите 15 минут."}, status_code=429)
            try:
                raw = base64.b64decode(request.headers["authorization"][6:], validate=True).decode()
                username, password = raw.split(":", 1)
                user = await main.run_in_threadpool(authenticate, username, password)
            except (ValueError, UnicodeError):
                user = None
            record_attempt(client, bool(user))
            basic = bool(user)
        if not user:
            if path == "/kindle" or path.startswith("/kindle/"):
                return RedirectResponse("/kindle/login", status_code=303)
            if path == "/" or path == "/accounts":
                return RedirectResponse("/login", status_code=303)
            return JSONResponse({"detail": "Требуется вход"}, status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="Personal library", charset="UTF-8"'})
        if request.method not in ("GET", "HEAD", "OPTIONS") and not sync:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.url.netloc:
                return JSONResponse({"detail": "Недопустимый источник запроса"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Недопустимый источник запроса"}, status_code=403)
            if basic and not webdav:
                return JSONResponse({"detail": "Для изменений войдите через сайт"}, status_code=403)
        if not user["owner"] and (path.startswith("/kindle/integrations") or path.startswith("/integrations") or path.startswith("/api/integrations") or path.startswith("/api/sources")):
            return JSONResponse({"detail": "Интеграции сервера доступны только владельцу"}, status_code=403)
        if path == "/users/create":
            import json
            try:
                value = json.loads(await request.body())
                if value.get("username") != sync_username(user):
                    return JSONResponse({"detail": "Используйте свой логин синхронизации"}, status_code=403)
            except (ValueError, AttributeError):
                return JSONResponse({"detail": "Некорректный запрос"}, status_code=400)
        context = current_user.set(user)
        try:
            response = await call_next(request)
            if sync and response.status_code == 401:
                record_attempt(client, False)
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        finally:
            current_user.reset(context)


def sync_username(user=None):
    import os
    user = user or current_user.get()
    return user["username"] if user and not user["owner"] else os.environ.get("KOSYNC_LIBRARY_USER", "reader")


def login_page(message=""):
    from .ui import layout, esc
    return layout("Вход", f'''<section class="section"><h1>Личная библиотека</h1>
      <p>Войдите в свой аккаунт. Для OPDS используйте тот же логин и пароль.</p>
      <p role="status">{esc(message)}</p><form action="/login" method="post">
      <label>Логин<input name="username" autocomplete="username" required maxlength="64"></label>
      <label>Пароль<input name="password" type="password" autocomplete="current-password" required maxlength="256"></label>
      <button class="button">Войти</button></form>
      <p><a class="text-link" href="/forgot-password">Забыли пароль?</a></p><p>Используйте логин и пароль, которые вы создали при первом запуске.</p></section>''')


@router.get("/login", response_class=HTMLResponse)
def login_form():
    from .setup import has_owner
    return login_page() if has_owner() else RedirectResponse('/setup', status_code=303)


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password():
    from .ui import layout
    return layout("Восстановление доступа", '''<section class="setup-welcome"><h1>Забыли пароль?</h1>
<p>Если вы владелец библиотеки, откройте её папку на компьютере и запустите <strong>Восстановить пароль.command</strong> на Mac или <strong>Восстановить пароль.bat</strong> на Windows. Введите новый пароль два раза. Книги и настройки сохранятся.</p>
<p>При вводе пароль не отображается — это нормально. После смены вернитесь на страницу входа.</p><p>Если аккаунт создал другой человек, обратитесь к владельцу сервера. Эта процедура меняет пароль владельца, а не других пользователей.</p>
<details><summary>Для запуска через терминал</summary><pre>docker compose exec yandex2kindle python -m app.auth --reset-owner</pre></details><a class="button secondary" href="/login">Вернуться ко входу</a></section>''', 'accounts')


@router.post("/login")
def login(request: Request, username: str = Form(..., max_length=64), password: str = Form(..., max_length=256), next_path: str = Form("/", max_length=100)):
    from .kindle import login_page as kindle_login_page
    render_login = kindle_login_page if next_path == "/kindle" else login_page
    client = request.client.host if request.client else "unknown"
    origin = request.headers.get("origin")
    if (origin and urlsplit(origin).netloc != request.url.netloc) or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Недопустимый источник запроса")
    if limited(client):
        return HTMLResponse(render_login("Слишком много попыток. Подождите 15 минут."), status_code=429)
    user = authenticate(username, password)
    record_attempt(client, bool(user))
    if not user:
        return HTMLResponse(render_login("Неверный логин или пароль"), status_code=401)
    return session_response(request, user, "/kindle" if next_path == "/kindle" else "/")


def session_response(request, user, destination):
    token = secrets.token_urlsafe(32)
    with accounts() as db:
        db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        db.execute("INSERT INTO sessions VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user["id"], time.time()+604800))
    response = RedirectResponse(destination, status_code=303)
    response.set_cookie(COOKIE, token, max_age=604800, httponly=True, samesite="strict", secure=request.url.scheme == "https")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/logout")
def logout(request: Request, next_path: str = Form("/login", max_length=100)):
    with accounts() as db:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(request.cookies.get(COOKIE, "").encode()).hexdigest(),))
    response = RedirectResponse("/kindle/login" if next_path == "/kindle/login" else "/login", status_code=303)
    response.delete_cookie(COOKIE)
    return response


@router.get("/accounts", response_class=HTMLResponse)
def account_page():
    from .ui import layout, esc
    user = current_user.get()
    body = f'<h1>Аккаунт {esc(user["username"])}</h1><p>OPDS: <code>/opds</code>. Используйте логин и пароль от сайта.</p><p>Логин синхронизации KOReader: <strong>{esc(sync_username())}</strong>.</p>'
    from .smtp_delivery import is_smtp_configured
    smtp_hint = '<span class="status-badge ok">SMTP настроен</span>' if is_smtp_configured() else '<span class="status-badge warning">SMTP не настроен</span>'
    kindle_email_val = esc(user.get("kindle_email") or "")
    body += f'''<h2>Доставка на Kindle (Send-to-Kindle) {smtp_hint}</h2>
      <p class="muted">Укажите адрес вашего устройства Kindle (например, <code>username@kindle.com</code>). Отправка выполняется по почте через SMTP.</p>
      <form action="/accounts/kindle-email" method="post">
        <label>Почта Kindle (@kindle.com)<input name="kindle_email" type="email" value="{kindle_email_val}" placeholder="username@kindle.com" maxlength="128"></label>
        <button class="button">Сохранить адрес Kindle</button></form>'''
    if user["owner"]:
        with accounts() as db:
            names = ", ".join(esc(row[0]) for row in db.execute("SELECT username FROM accounts ORDER BY id"))
        body += f'''<h2>Пользователи</h2><p>{names}</p><form action="/accounts" method="post">
          <label>Логин<input name="username" required minlength="3" maxlength="64" autocomplete="off"></label>
          <label>Пароль<input name="password" type="password" required minlength="12" maxlength="256" autocomplete="new-password"></label>
          <button class="button">Создать личную библиотеку</button></form>'''
    body += '<p><a class="text-link" href="/settings">Настройки и подключения →</a></p>' if user["owner"] else ''
    body += '<form action="/logout" method="post"><button class="button secondary">Выйти</button></form>'
    return layout("Аккаунт", body)


@router.post("/accounts")
def add_account(username: str = Form(...), password: str = Form(...)):
    if not current_user.get()["owner"]:
        raise HTTPException(403, "Только для владельца")
    try:
        create_account(username, password)
    except (ValueError, sqlite3.IntegrityError):
        raise HTTPException(400, "Логин занят или некорректен. Пароль должен содержать 12–256 символов.") from None
    return RedirectResponse("/accounts", status_code=303)


@router.post("/accounts/kindle-email")
def update_kindle_email(kindle_email: str = Form("")):
    user = current_user.get()
    email = kindle_email.strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(422, "Некорректный адрес электронной почты")
    with accounts() as db:
        db.execute("UPDATE accounts SET kindle_email=? WHERE id=?", (email, user["id"]))
    return RedirectResponse("/accounts", status_code=303)


@router.put("/api/account/kindle-email")
def api_update_kindle_email(kindle_email: str = Form(...)):
    user = current_user.get()
    email = kindle_email.strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(422, "Некорректный адрес электронной почты")
    with accounts() as db:
        db.execute("UPDATE accounts SET kindle_email=? WHERE id=?", (email, user["id"]))
    return {"ok": True, "kindle_email": email}


def reset_owner(password):
    if not 12 <= len(password) <= 256:
        raise ValueError("Пароль должен содержать от 12 до 256 символов")
    salt = secrets.token_hex(16)
    with accounts() as db:
        row = db.execute("SELECT * FROM accounts WHERE owner=1").fetchone()
        if not row:
            raise ValueError("Владелец ещё не создан. Запустите команду без --reset-owner.")
        db.execute("UPDATE accounts SET salt=?,password_hash=? WHERE id=?", (salt, digest(password, salt), row["id"]))
        db.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
        db.execute("DELETE FROM login_attempts")
    return row["username"]


def cli():
    import argparse
    parser = argparse.ArgumentParser(description="Создание владельца и восстановление доступа без удаления книг")
    parser.add_argument("--reset-owner", action="store_true", help="Задать новый пароль владельца сайта; пароль KOReader сохраняется")
    args = parser.parse_args()
    if args.reset_owner:
        password = getpass.getpass("Новый пароль владельца (от 12 символов): ")
        if password != getpass.getpass("Повторите пароль: "):
            raise SystemExit("Пароли не совпадают")
        try:
            username = reset_owner(password)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        print(f"Пароль владельца {username} обновлён. Книги и пароль синхронизации KOReader сохранены.")
        return
    with accounts() as db:
        owner = db.execute("SELECT username FROM accounts WHERE owner=1").fetchone()
    if owner:
        raise SystemExit(f"Владелец уже создан: {owner['username']}. Для восстановления доступа: python -m app.auth --reset-owner")
    username = input("Логин владельца: ").strip()
    password = getpass.getpass("Пароль (от 12 символов): ")
    if password != getpass.getpass("Повторите пароль: "):
        raise SystemExit("Пароли не совпадают")
    try:
        create_account(username, password, owner=True)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    except sqlite3.IntegrityError:
        raise SystemExit("Логин занят или владелец уже создан. Для восстановления: python -m app.auth --reset-owner") from None
    print("Владелец создан. Существующая библиотека сохранена за ним.")


if __name__ == "__main__":
    cli()
