"""Send-to-Kindle delivery via SMTP."""
from . import configuration
from email.message import EmailMessage
import logging
import os
from pathlib import Path
import re
import smtplib
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from . import main
from .conversion import ensure_epub_file

router = APIRouter(tags=["Send to Kindle"])
logger = logging.getLogger("uvicorn.error")

AMAZON_MAX_SIZE = 50 * 1024 * 1024  # 50 MB


def is_smtp_configured() -> bool:
    return bool(configuration.get("SMTP_HOST", "").strip() and configuration.get("SMTP_FROM", "").strip())


class SendKindleRequest(BaseModel):
    email: str | None = Field(default=None, max_length=256)


def get_target_email(specified_email: str | None = None) -> str:
    from .auth import current_user
    user = current_user.get()
    target = (specified_email or "").strip()
    if not target and user:
        target = (user.get("kindle_email") or "").strip()
    if not target:
        target = configuration.get("KINDLE_EMAIL", "").strip()
    if not target:
        raise HTTPException(400, "Укажите адрес почты Kindle (@kindle.com) в настройках аккаунта или в запросе")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", target):
        raise HTTPException(422, "Некорректный адрес электронной почты Kindle")
    return target


from contextlib import contextmanager


@contextmanager
def smtp_connection():
    host = configuration.get("SMTP_HOST", "").strip()
    if not host:
        raise HTTPException(503, "Откройте Настройки → Отправка книг на Kindle и укажите данные почты")
    port = int(configuration.get("SMTP_PORT", "587"))
    use_ssl = configuration.get("SMTP_SSL", "false").lower() == "true" or port == 465
    use_tls = configuration.get("SMTP_TLS", "true").lower() == "true" and not use_ssl
    user = configuration.get("SMTP_USER", "").strip()
    password = configuration.get("SMTP_PASSWORD", "").strip()
    factory = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with factory(host, port, timeout=int(configuration.get("SMTP_TIMEOUT_SECONDS", "60"))) as server:
        if use_tls:
            server.starttls()
        if user and password:
            server.login(user, password)
        yield server


def send_mail(msg: EmailMessage):
    try:
        with smtp_connection() as server:
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("Send-to-Kindle SMTP error: kind=%s", type(exc).__name__)
        raise HTTPException(502, "Почта не приняла отправку. Проверьте подключение на странице Настройки.") from None


def deliver_book_to_kindle(book_id: int, target_email: str | None = None) -> dict:
    if not is_smtp_configured():
        raise HTTPException(503, "Отправка на Kindle недоступна: настройте SMTP_HOST и SMTP_FROM на странице «Настройки»")
    recipient = get_target_email(target_email)

    with main.connect() as db:
        book = db.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if not book:
        raise HTTPException(404, "Книга не найдена")

    epub_path, epub_filename = ensure_epub_file(book)
    size = epub_path.stat().st_size
    if size > AMAZON_MAX_SIZE:
        raise HTTPException(413, f"Файл {epub_filename} ({size // (1024 * 1024)} МБ) превышает лимит Amazon Send-to-Kindle (50 МБ)")

    msg = EmailMessage()
    msg["Subject"] = book["title"]
    msg["From"] = configuration.get("SMTP_FROM", "").strip()
    msg["To"] = recipient
    msg.set_content(f"Книга «{book['title']}» отправлена на Kindle через Yandex2Kindle.")

    with epub_path.open("rb") as stream:
        file_data = stream.read()
    msg.add_attachment(file_data, maintype="application", subtype="epub+zip", filename=epub_filename)

    send_mail(msg)

    # Record SMTP acceptance separately from the user's device confirmation.
    now_iso = datetime.now(timezone.utc).isoformat()
    with main.connect() as db:
        db.execute("""INSERT INTO book_device_state(book_id,email_sent_at) VALUES(?,?)
            ON CONFLICT(book_id) DO UPDATE SET email_sent_at=excluded.email_sent_at""",
            (book_id, now_iso))

    logger.info("Book delivered to Kindle: book_id=%s title=%s recipient=%s size=%s",
                book_id, book["title"], recipient, size)
    return {"status": "sent", "book_id": book_id, "recipient": recipient, "title": book["title"]}


@router.get("/api/smtp/status")
def smtp_status():
    from .auth import current_user
    user = current_user.get()
    return {
        "configured": is_smtp_configured(),
        "sender": configuration.get("SMTP_FROM", "") if is_smtp_configured() else None,
        "user_kindle_email": user.get("kindle_email") if user else None,
        "default_kindle_email": configuration.get("KINDLE_EMAIL", ""),
    }


@router.post("/api/books/{book_id}/send-to-kindle")
def api_send_to_kindle(book_id: int, payload: SendKindleRequest | None = None):
    email = payload.email if payload else None
    return deliver_book_to_kindle(book_id, email)


@router.post("/books/{book_id}/send-to-kindle")
def form_send_to_kindle(book_id: int, email: str = Form(""), next_path: str = Form("/")):
    deliver_book_to_kindle(book_id, email or None)
    return RedirectResponse(next_path if next_path.startswith("/") else "/", status_code=303)
