"""Read-only Bookmate REST adapter, independent of SDK release differences.

Endpoint, Auth-Token header and page/per_page contract are documented in:
https://github.com/stepan163s/yandex-book-api/blob/main/yandex_book/client.py
https://github.com/stepan163s/yandex-book-api/blob/main/yandex_book/utils/request.py
"""
from . import configuration
import os
import json
import time

import httpx
from fastapi import HTTPException

API = "https://api.bookmate.yandex.net/api/v5"


def request_json(client, path, **kwargs):
    try:
        with client.stream("GET", API + path, **kwargs) as response:
            if response.status_code in (401, 403):
                raise HTTPException(401, "OAuth-токен Яндекса отклонён или не имеет доступа к библиотеке")
            if not 200 <= response.status_code < 300:
                raise HTTPException(502, f"API Яндекс Книг: HTTP {response.status_code}")
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > 2 * 1024 * 1024:
                    raise HTTPException(502, "Ответ API Яндекс Книг превышает 2 МБ")
            payload = json.loads(content)
    except httpx.TimeoutException:
        raise HTTPException(504, "API Яндекс Книг не ответил вовремя") from None
    except httpx.HTTPError:
        raise HTTPException(502, "Не удалось соединиться с API Яндекс Книг") from None
    except (ValueError, UnicodeError):
        raise HTTPException(502, "API Яндекс Книг вернул некорректный JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(502, "API Яндекс Книг: ожидался JSON-объект")
    return payload


def author_text(book):
    for field in ("authors_text", "authors", "authors_objects"):
        value = book.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            names = []
            for person in value:
                name = person.get("name") if isinstance(person, dict) else person
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
            if names:
                return ", ".join(names)
    return ""


def fetch_library():
    token = configuration.get("YANDEX_BOOK_TOKEN", "").strip()
    if not token:
        raise HTTPException(503, "Подключите Яндекс на странице «Настройки» → «Яндекс Книги»")
    headers = {"Auth-Token": token, "App-Language": "ru", "App-Locale": "ru",
               "App-Platform": "android", "Bookmate-Version": "20200305",
               "Device-Os": "Android", "User-Agent": "okhttp/4.12.0", "Accept": "application/json"}
    with httpx.Client(headers=headers, timeout=20, follow_redirects=False, trust_env=False) as client:
        profile = request_json(client, "/profile").get("user")
        login = profile.get("login") if isinstance(profile, dict) else None
        if not isinstance(login, str) or not login.strip():
            raise HTTPException(502, "API Яндекс Книг: в profile.user отсутствует логин")
        source, books = "yandex:" + login.strip(), {}
        deadline = time.monotonic() + 300
        for page in range(1, 1001):
            if time.monotonic() > deadline:
                raise HTTPException(504, "Синхронизация Яндекс Книг превысила 5 минут; импорт отменён")
            response = request_json(client, "/profile/library_cards", params={"page": page, "per_page": 100})
            cards = response.get("library_cards")
            if not isinstance(cards, list):
                raise HTTPException(502, "API Яндекс Книг: отсутствует список library_cards")
            if not cards:
                return source, list(books.values())
            previous_ids = set(books)
            for card in cards:
                if not isinstance(card, dict):
                    raise HTTPException(502, "API Яндекс Книг: карточка библиотеки должна быть объектом")
                book = card.get("book")
                if book is None:
                    continue
                if not isinstance(book, dict):
                    raise HTTPException(502, "API Яндекс Книг: поле book должно быть объектом")
                identity = book.get("uuid")
                title = next((book.get(key).strip() for key in ("display_title", "title", "name")
                              if isinstance(book.get(key), str) and book.get(key).strip()), None)
                if not isinstance(identity, str) or not identity.strip() or not title:
                    raise HTTPException(502, "API Яндекс Книг: у книги отсутствует UUID или название")
                state = card.get("state")
                books[identity] = {"external_id": identity, "title": title, "author": author_text(book),
                                   "state": state if isinstance(state, str) else ""}
            if len(cards) < 100:
                return source, list(books.values())
            if previous_ids and previous_ids == set(books):
                raise HTTPException(502, "Пагинация Яндекса не продвигается; импорт отменён")
        raise HTTPException(502, "Библиотека превышает лимит 100 000 записей; импорт отменён")
