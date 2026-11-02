"""Telegram Bot for Yandex2Kindle: upload books, search library, view stats."""
import asyncio
from . import configuration
import io
import logging
import os
from pathlib import Path
import tempfile
import httpx

logger = logging.getLogger("uvicorn.error")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_USERS = [x.strip() for x in os.environ.get("TELEGRAM_ALLOWED_USERS", "").split(",") if x.strip()]
AUTO_SEND_KINDLE = os.environ.get("TELEGRAM_AUTO_SEND_TO_KINDLE", "false").lower() in ("true", "1", "yes")

SUPPORTED_EXTS = {".epub", ".fb2", ".pdf", ".mobi", ".azw3", ".txt", ".mp3", ".m4b"}


def is_user_allowed(from_user: dict) -> bool:
    allowed = [v.strip().lstrip("@") for v in configuration.get("TELEGRAM_ALLOWED_USERS", ",".join(ALLOWED_USERS)).split(",") if v.strip()]
    if not allowed:
        return True
    user_id = str(from_user.get("id", ""))
    username = (from_user.get("username") or "").lower()
    return user_id in allowed or username in [u.lower() for u in allowed]


async def send_telegram_message(client: httpx.AsyncClient, chat_id: int, text: str, parse_mode: str = "Markdown"):
    url = f"https://api.telegram.org/bot{configuration.get('TELEGRAM_BOT_TOKEN', BOT_TOKEN)}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    try:
        await client.post(url, json=payload, timeout=10.0)
    except Exception as exc:
        logger.warning("Failed to send telegram message to %s: %s", chat_id, exc)


async def handle_document(client: httpx.AsyncClient, doc: dict, chat_id: int, from_user: dict):
    from . import main
    file_name = doc.get("file_name", "book.epub")
    file_id = doc.get("file_id")
    suffix = Path(file_name).suffix.lower()

    if suffix not in SUPPORTED_EXTS:
        await send_telegram_message(
            client, chat_id,
            f"❌ Неподдерживаемый формат: `{suffix}`.\nПоддерживаются: " + ", ".join(SUPPORTED_EXTS)
        )
        return

    await send_telegram_message(client, chat_id, f"⏳ Загружаю `{file_name}` в библиотеку...")

    # Get file path from Telegram
    get_file_url = f"https://api.telegram.org/bot{configuration.get('TELEGRAM_BOT_TOKEN', BOT_TOKEN)}/getFile"
    res = await client.get(get_file_url, params={"file_id": file_id}, timeout=15.0)
    if not res.is_success:
        await send_telegram_message(client, chat_id, "❌ Не удалось получить файл от Telegram.")
        return

    file_path_tg = res.json().get("result", {}).get("file_path")
    if not file_path_tg:
        await send_telegram_message(client, chat_id, "❌ Ошибка пути файла Telegram.")
        return

    # Download file content
    download_url = f"https://api.telegram.org/file/bot{configuration.get('TELEGRAM_BOT_TOKEN', BOT_TOKEN)}/{file_path_tg}"
    file_res = await client.get(download_url, timeout=120.0)
    if not file_res.is_success:
        await send_telegram_message(client, chat_id, "❌ Ошибка при скачивании файла.")
        return

    # Write to temp file and import
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_res.content)
        tmp_path = Path(tmp.name)

    try:
        result = await asyncio.to_thread(main.add_file, tmp_path)
        book_id = result.get("id")
        status = result.get("status")

        with main.connect() as db:
            book = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
            title = book["title"] if book else file_name
            author = book["author"] if book else ""

        status_text = "уже есть в библиотеке" if status == "duplicate" else "успешно добавлена"
        msg = f"✅ Книга *«{title}»* ({author or 'Без автора'}) {status_text}!"

        # Optional Auto Send-to-Kindle
        if configuration.get("TELEGRAM_AUTO_SEND_TO_KINDLE", str(AUTO_SEND_KINDLE)).lower() == "true" and book_id:
            from .smtp_delivery import is_smtp_configured, deliver_book_to_kindle
            if is_smtp_configured():
                kindle_email = configuration.get("KINDLE_EMAIL", "").strip()
                if kindle_email:
                    try:
                        await asyncio.to_thread(deliver_book_to_kindle, book_id, kindle_email)
                        msg += f"\n📨 Отправлена на Kindle: `{kindle_email}`"
                    except Exception as s_exc:
                        msg += f"\n⚠️ Ошибка отправки на Kindle: {s_exc}"

        await send_telegram_message(client, chat_id, msg)
    except Exception as exc:
        logger.error("Error importing file via telegram: %s", exc)
        await send_telegram_message(client, chat_id, f"❌ Ошибка импорта: {exc}")
    finally:
        tmp_path.unlink(missing_ok=True)


async def handle_message(client: httpx.AsyncClient, message: dict):
    chat_id = message.get("chat", {}).get("id")
    from_user = message.get("from", {})
    text = (message.get("text") or "").strip()

    if not chat_id or not is_user_allowed(from_user):
        return

    # Document / Audio upload
    doc = message.get("document") or message.get("audio")
    if doc:
        await handle_document(client, doc, chat_id, from_user)
        return

    # Commands
    if text.startswith("/start"):
        await send_telegram_message(
            client, chat_id,
            "👋 Привет! Я бот для вашей личной библиотеки *Yandex2Kindle*.\n\n"
            "📥 *Отправьте мне книгу* (файл `.epub`, `.fb2`, `.mobi`, `.pdf`, `.mp3`), "
            "и я автоматически добавлю её в библиотеку и подготовлю для вашего Kindle!\n\n"
            "Команды:\n"
            "• `/stats` — статистика и читательские стрики\n"
            "• `/search <запрос>` — поиск книг в библиотеке"
        )
    elif text.startswith("/stats"):
        from .stats import get_stats
        s = get_stats()
        await send_telegram_message(
            client, chat_id,
            f"📊 *Статистика библиотеки*\n\n"
            f"📚 Всего книг: *{s['total_books']}*\n"
            f"📖 Читаю сейчас: *{s['reading_books']}*\n"
            f"✅ Прочитано: *{s['finished_books']}*\n"
            f"🔥 Читательский стрик: *{s['reading_streak_days']} дн.*\n"
            f"⏱ Время чтения: *{s['total_reading_minutes']} мин*"
        )
    elif text.startswith("/search"):
        query = text.replace("/search", "", 1).strip()
        if not query:
            await send_telegram_message(client, chat_id, "Использование: `/search <название или автор>`")
            return
        from . import main
        with main.connect() as db:
            books, total = main.list_books(db, query, limit=5, offset=0)
        if not books:
            await send_telegram_message(client, chat_id, f"По запросу «{query}» ничего не найдено.")
            return
        lines = [f"🔍 Найдено {total} книг (показано {len(books)}):\n"]
        for b in books:
            lines.append(f"• *{b['title']}* — {b['author'] or 'Неизвестен'} (#{b['id']})")
        await send_telegram_message(client, chat_id, "\n".join(lines))


async def run_telegram_bot():
    """Keep polling available when credentials are added through the web UI."""
    offset, previous_token = 0, None
    async with httpx.AsyncClient(timeout=40.0) as client:
        with configuration.watching('telegram') as changed:
            while True:
                token = configuration.get("TELEGRAM_BOT_TOKEN", BOT_TOKEN).strip()
                if not token:
                    await configuration.wait_change(3600, changed)
                    continue
                if token != previous_token:
                    offset, previous_token = 0, token
                try:
                    url = f"https://api.telegram.org/bot{token}/getUpdates"
                    res = await client.get(url, params={"offset":offset,"timeout":25})
                    if res.is_success and token == configuration.get("TELEGRAM_BOT_TOKEN", BOT_TOKEN).strip():
                        for update in res.json().get("result", []):
                            offset = max(offset, update["update_id"] + 1)
                            if update.get("message"):
                                await handle_message(client, update["message"])
                    if not res.is_success:
                        await configuration.wait_change(5, changed)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("Telegram connection failed; retrying")
                    await configuration.wait_change(5, changed)
