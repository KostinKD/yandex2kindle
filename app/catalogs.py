"""Z-Lib API adapter with ordered mirror fallback."""
from . import configuration
import json
import logging
import os
import re
import time

import httpx
from fastapi import HTTPException

from .remote_http import request_headers

ZLIB_MIRRORS = (
    "https://z-zibrary.bz", "https://z-lib.mx", "https://z-library.im",
    "https://z-lib.sk", "https://z-library.sk", "https://z-lib.fm", "https://libb.la",
)
logger = logging.getLogger("uvicorn.error")
EXTENSIONS = {"epub": ".epub", "fb2": ".fb2", "fb2.zip": ".fb2.zip", "pdf": ".pdf",
              "mobi": ".mobi", "azw3": ".azw3", "txt": ".txt"}


def api_request(path, data=None, preferred=None):
    """Keep session credentials on explicitly listed mirrors; never follow redirects."""
    mirrors = list(ZLIB_MIRRORS)
    if preferred in mirrors:
        mirrors.remove(preferred)
        mirrors.insert(0, preferred)
    cookies = {key: configuration.get(variable) for key, variable in
               (("remix_userid", "ZLIB_REMIX_USERID"), ("remix_userkey", "ZLIB_REMIX_USERKEY"))
               if configuration.get(variable)}
    last_error = None
    failures = {"network": 0, "html": 0, "http": 0}
    deadline = time.monotonic() + 90
    for mirror in mirrors:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            with httpx.Client(timeout=httpx.Timeout(min(10, remaining), connect=min(5, remaining)), follow_redirects=False,
                              trust_env=configuration.get("REMOTE_TRUST_ENV", "true").lower() == "true",
                              headers=request_headers(False), cookies=cookies) as client:
                started = time.monotonic()
                with client.stream("POST" if data is not None else "GET", mirror + path, data=data) as response:
                    if response.status_code in (401, 403):
                        raise HTTPException(502, "Z-Lib отклонил доступ: проверьте ZLIB_REMIX_USERID и ZLIB_REMIX_USERKEY на странице «Настройки»")
                    response.raise_for_status()
                    if response.is_redirect:
                        raise ValueError("API redirected")
                    if 'text/html' in response.headers.get('content-type', '').lower():
                        failures['html'] += 1
                        raise ValueError('HTML instead of API')
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 2 * 1024 * 1024 or time.monotonic() - started > 20 or time.monotonic() >= deadline:
                            raise ValueError("API response limit exceeded")
            payload = json.loads(body)
            if not isinstance(payload, dict) or "success" not in payload:
                raise ValueError("Invalid API response")
            if not payload["success"]:
                # Do not expose upstream text (which may contain account information).
                raise HTTPException(502, "Z-Lib не выполнил запрос: проверьте сессию ZLIB_REMIX_USERID / ZLIB_REMIX_USERKEY и лимит скачиваний")
            if path == "/eapi/book/search" and not isinstance(payload.get("books"), list):
                raise ValueError("Invalid book list")
            if path.endswith("/file"):
                file = payload.get("file")
                if not isinstance(file, dict) or not isinstance(file.get("downloadLink"), str) or not file["downloadLink"]:
                    raise ValueError("Missing download link")
            return payload, mirror
        except (httpx.HTTPError, ValueError, HTTPException) as exc:
            last_error = exc
            if isinstance(exc, httpx.RequestError): failures['network'] += 1
            elif isinstance(exc, httpx.HTTPStatusError): failures['http'] += 1
            logger.warning("Z-Lib mirror unavailable: host=%s kind=%s", mirror.split("//")[1], type(exc).__name__)
    if isinstance(last_error, HTTPException):
        raise last_error
    reason = ("Зеркала возвращают веб-страницу вместо API: возможна проверка браузера или смена адреса." if failures['html'] else
              "Не удалось соединиться с зеркалами из сервера приложения: проверьте интернет, DNS и доступ из Docker." if failures['network'] else
              "Зеркала отклонили запрос или вернули несовместимый ответ API.")
    raise HTTPException(502, "Z-Lib недоступен. " + reason + " Проверьте вход на сайте Z-Lib в браузере. Cookies можно обновить в Настройки → Внешние каталоги; они не устраняют сетевую блокировку или проверку браузера. Можно выбрать другой каталог.") from None


def search_books(title, author, page):
    query = " ".join(value.strip() for value in (title, author) if value.strip())
    payload, mirror = api_request("/eapi/book/search", {"message": query, "page": page + 1, "limit": 50})
    books = payload.get("books")
    if not isinstance(books, list):
        raise HTTPException(502, "Z-Lib вернул некорректный список книг")
    candidates = []
    for book in books[:200]:
        if not isinstance(book, dict):
            continue
        suffix = EXTENSIONS.get(str(book.get("extension", "")).lower())
        book_id, book_hash = str(book.get("id", "")), str(book.get("hash", ""))
        name = str(book.get("title") or "").strip()[:1000]
        if not suffix or not name or not re.fullmatch(r"[0-9]+", book_id) or not re.fullmatch(r"[a-zA-Z0-9]+", book_hash):
            continue
        candidates.append({"external_id": f"{book_id}/{book_hash}", "title": name,
                           "author": str(book.get("author") or "")[:1000], "suffix": suffix,
                           "url": f"{mirror}/eapi/book/{book_id}/{book_hash}/file"})
    return candidates


def download_url(candidate_url):
    from urllib.parse import urlsplit
    parts = urlsplit(candidate_url)
    mirror = f"{parts.scheme}://{parts.netloc}"
    if mirror not in ZLIB_MIRRORS or not re.fullmatch(r"/eapi/book/[0-9]+/[a-zA-Z0-9]+/file", parts.path) or parts.query or parts.fragment:
        raise HTTPException(502, "Некорректный адрес книги Z-Lib")
    payload, _ = api_request(parts.path, preferred=mirror)
    file = payload.get("file")
    if not isinstance(file, dict) or not isinstance(file.get("downloadLink"), str) or not file["downloadLink"]:
        raise HTTPException(502, "Z-Lib не вернул ссылку на скачивание; проверьте сессию и лимит скачиваний")
    return file["downloadLink"]
