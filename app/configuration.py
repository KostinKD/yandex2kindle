"""Private server configuration with a live .env fallback."""
import os
import sqlite3
import asyncio
from contextlib import contextmanager

SECRETS = {'YANDEX_BOOK_TOKEN', 'SMTP_PASSWORD', 'ZLIB_REMIX_USERID', 'ZLIB_REMIX_USERKEY', 'TELEGRAM_BOT_TOKEN'}
GROUPS = {
    'yandex': ('YANDEX_BOOK_TOKEN', 'YANDEX_SYNC_INTERVAL_SECONDS'),
    'smtp': ('SMTP_HOST', 'SMTP_PORT', 'SMTP_USER', 'SMTP_PASSWORD', 'SMTP_FROM', 'SMTP_SSL', 'SMTP_TLS'),
    'catalogs': ('FLIBUSTA_SEARCH_URL', 'OPDS_SEARCH_URL', 'REMOTE_ALLOWED_ORIGINS', 'ZLIB_REMIX_USERID', 'ZLIB_REMIX_USERKEY'),
    'telegram': ('TELEGRAM_BOT_TOKEN', 'TELEGRAM_ALLOWED_USERS', 'TELEGRAM_AUTO_SEND_TO_KINDLE'),
    'device': ('APP_HOST_IP', 'KINDLE_EMAIL'),
}
_watchers = set()


def path():
    from . import main
    return main.DB.parent / 'configuration.sqlite'


@contextmanager
def database():
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    target.chmod(0o600)
    db = sqlite3.connect(target, timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript('''CREATE TABLE IF NOT EXISTS settings(name TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS checks(section TEXT PRIMARY KEY,ok INTEGER NOT NULL,message TEXT NOT NULL);''')
    try:
        with db:
            yield db
    finally:
        db.close()


def get(name, default=''):
    target = path()
    if target.is_file():
        db = sqlite3.connect(f'file:{target}?mode=ro', uri=True, timeout=30)
        try:
            row = db.execute('SELECT value FROM settings WHERE name=?', (name,)).fetchone()
            if row is not None:
                return row[0]
        except sqlite3.OperationalError as exc:
            # Another request can observe the newly created file before its schema.
            if 'no such table: settings' not in str(exc):
                raise
        finally:
            db.close()
    return os.environ.get(name, default)


def save(section, values, replace_secrets=False):
    if section not in GROUPS or any(key not in GROUPS[section] for key in values):
        raise ValueError('Неизвестная настройка')
    with database() as db:
        for name, value in values.items():
            if name in SECRETS and not value and not replace_secrets:
                continue
            db.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value', (name, value))
        db.execute('DELETE FROM checks WHERE section=?', (section,))
    for loop, event, watched_section in tuple(_watchers):
        if not loop.is_closed() and (watched_section is None or watched_section == section):
            loop.call_soon_threadsafe(event.set)


@contextmanager
def watching(section=None):
    item = (asyncio.get_running_loop(), asyncio.Event(), section)
    _watchers.add(item)
    try:
        yield item[1]
    finally:
        _watchers.discard(item)


async def wait_change(seconds, event):
    try:
        await asyncio.wait_for(event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass
    event.clear()


def checks():
    if not path().is_file():
        return {}
    with database() as db:
        return {r['section']: {'ok': bool(r['ok']), 'message': r['message']} for r in db.execute('SELECT * FROM checks')}


def checked(section, ok, message):
    with database() as db:
        db.execute('INSERT INTO checks VALUES(?,?,?) ON CONFLICT(section) DO UPDATE SET ok=excluded.ok,message=excluded.message',
                   (section, int(ok), message))
