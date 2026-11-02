"""Configured catalog adapters, explicit acquisition, and Yandex metadata sync."""
from . import configuration
import asyncio
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import httpx
from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import main
from .catalogs import ZLIB_MIRRORS
from .remote_http import request_headers, book_page, safe_referer

router = APIRouter(tags=["Integrations"])
SYNC_LOCK = threading.Lock()
logger = logging.getLogger("uvicorn.error")
ATOM = {"a": "http://www.w3.org/2005/Atom"}
FORMATS = {"application/epub+zip": ".epub", "application/x-fictionbook+xml": ".fb2",
           "application/fb2+zip": ".fb2.zip", "application/x-fictionbook+xml+zip": ".fb2.zip",
           "application/fb2+xml": ".fb2", "application/pdf": ".pdf",
           "application/x-mobipocket-ebook": ".mobi", "application/vnd.amazon.ebook": ".azw3",
           "text/plain": ".txt"}


def catalog_template(source):
    variable = {"flibusta": "FLIBUSTA_SEARCH_URL", "opds": "OPDS_SEARCH_URL"}.get(source)
    if not variable:
        raise HTTPException(422, "Неизвестный источник")
    template = configuration.get(variable, "").strip()
    if not template:
        raise HTTPException(503, f"Настройте {variable} на странице «Настройки»")
    if "{searchTerms}" not in template:
        raise HTTPException(503, f"{variable} должен содержать {{searchTerms}}")
    return template


def origin(url):
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            raise ValueError()
        return parts.scheme, parts.hostname.lower(), parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise HTTPException(502, "Некорректный адрес внешнего каталога") from None


def allowed_url(url, source):
    trusted = list(ZLIB_MIRRORS) if source == "zlib" else [catalog_template(source)]
    if source == "flibusta" and origin(trusted[0]) == ("https", "flibusta.is", 443):
        trusted.extend(["https://flibusta.site", "https://static.flibusta.site"])
    trusted.extend(x.strip() for x in configuration.get("REMOTE_ALLOWED_ORIGINS", "").split(",") if x.strip())
    scheme, host, port = origin(url)
    if (scheme, host, port) not in {origin(x) for x in trusted}:
        # Show only the origin: download paths and query strings may contain tokens.
        authority = f"[{host}]" if ":" in host else host
        blocked = f"{scheme}://{authority}"
        if port != (443 if scheme == "https" else 80):
            blocked += f":{port}"
        logger.warning("Remote origin blocked: source=%s origin=%s", source, blocked)
        raise HTTPException(502, f"Каталог ссылается на неразрешённый сервер {blocked}. "
                            f"Добавьте {blocked} в REMOTE_ALLOWED_ORIGINS на странице «Настройки» "
                            "(через запятую к существующим адресам) и пересоздайте контейнер: "
                            "docker compose up -d --build --force-recreate")
    return url


def fetch_remote(url, source, target=None, max_size=2 * 1024 * 1024, trace=None):
    """Stream with bounded size, deadline, and validated redirects; never send OAuth."""
    downloading = target is not None
    stage = "скачивание книги" if downloading else "поиск в каталоге"
    try:
        budget = int(configuration.get("REMOTE_DOWNLOAD_TIMEOUT_SECONDS", "600")) if downloading else 120
        if not 30 <= budget <= 3600:
            raise ValueError()
    except ValueError:
        raise HTTPException(503, "REMOTE_DOWNLOAD_TIMEOUT_SECONDS должен быть целым числом от 30 до 3600") from None
    read_timeout = min(120, budget) if downloading else 20
    try:
        connect_timeout = int(configuration.get("REMOTE_CONNECT_TIMEOUT_SECONDS", "60"))
        if not 5 <= connect_timeout <= 120:
            raise ValueError()
    except ValueError:
        raise HTTPException(503, "REMOTE_CONNECT_TIMEOUT_SECONDS должен быть целым числом от 5 до 120") from None
    timeout = httpx.Timeout(connect=min(connect_timeout, budget), read=read_timeout, write=20, pool=10)
    started = time.monotonic()
    deadline = started + budget
    size = 0
    host = urlsplit(url).hostname
    def record(response, phase):
        item = {"phase": phase, "host": response.request.url.host, "path": response.request.url.path,
                "status": response.status_code, "http_version": response.http_version,
                "sets_cookie": "set-cookie" in response.headers,
                "request_had_cookie": "cookie" in response.request.headers,
                "elapsed_seconds": round(time.monotonic() - started, 1)}
        if trace is not None:
            trace.append(item)
        logger.info("Remote response: phase=%s source=%s host=%s status=%s http=%s elapsed=%.1fs",
                    phase, source, item["host"], item["status"], item["http_version"], time.monotonic() - started)
    try:
        allowed_url(url, source)
        with httpx.Client(timeout=timeout, follow_redirects=False,
                          http2=configuration.get("REMOTE_HTTP2", "true").lower() == "true",
                          trust_env=configuration.get("REMOTE_TRUST_ENV", "true").lower() == "true",
                          headers=request_headers(downloading)) as client:
            previous = None
            landing = book_page(url) if downloading and source == "flibusta" and configuration.get("REMOTE_BOOK_SESSION", "true").lower() == "true" else None
            if landing:
                allowed_url(landing, source)
                previous = landing
                try:
                    page_headers = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
                    with client.stream("GET", landing, headers=page_headers, timeout=min(20, budget)) as page:
                        record(page, "book_page")
                        page_size = 0
                        for chunk in page.iter_bytes():
                            page_size += len(chunk)
                            if page_size > 2 * 1024 * 1024 or time.monotonic() >= deadline:
                                break
                except httpx.HTTPError as exc:
                    logger.warning("Book page session unavailable: source=%s kind=%s", source, type(exc).__name__)
            retried = False
            for _ in range(6):
                if time.monotonic() > deadline:
                    raise HTTPException(504, f"{stage.capitalize()}: превышен общий лимит {budget} секунд")
                allowed_url(url, source)
                host = urlsplit(url).hostname
                logger.info("Remote request: source=%s stage=%s host=%s path=%s", source, stage, host, urlsplit(url).path)
                referer = safe_referer(previous, url)
                with client.stream("GET", url, headers={"Referer": referer} if referer else {}) as response:
                    record(response, "download" if downloading else "search")
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise HTTPException(502, "Пустое перенаправление каталога")
                        previous, url = url, urljoin(url, location)
                        continue
                    if downloading and not retried and response.status_code in (502, 503, 504):
                        retried = True
                        logger.warning("Retrying transient upstream error once: source=%s host=%s status=%s", source, host, response.status_code)
                        continue
                    response.raise_for_status()
                    size, chunks = 0, []
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > max_size:
                            raise HTTPException(413, "Ответ источника превышает допустимый размер")
                        if time.monotonic() > deadline:
                            raise HTTPException(504, f"{stage.capitalize()}: превышен общий лимит {budget} секунд")
                        if target:
                            target.write(chunk)
                        else:
                            chunks.append(chunk)
                    if not size:
                        raise HTTPException(502, "Источник вернул пустой ответ")
                    logger.info("Remote complete: source=%s host=%s bytes=%s elapsed=%.1fs", source, host, size, time.monotonic() - started)
                    return b"".join(chunks), url
        raise HTTPException(502, "Слишком много перенаправлений")
    except ImportError:
        raise HTTPException(503, "Для HTTP/2 установите httpx[http2] или пересоберите Docker-образ; временно можно задать REMOTE_HTTP2=false") from None
    except httpx.ConnectTimeout:
        logger.warning("Remote connect timeout: source=%s host=%s elapsed=%.1fs", source, host, time.monotonic() - started)
        raise HTTPException(504, f"{stage.capitalize()}: не удалось подключиться к {host} за {min(connect_timeout, budget)} секунд. Проверьте доступность домена из Docker; увеличение ожидания не устранит блокировку сети или ошибку DNS.") from None
    except httpx.TimeoutException:
        logger.warning("Remote timeout: source=%s stage=%s host=%s bytes=%s elapsed=%.1fs", source, stage, host, size, time.monotonic() - started)
        raise HTTPException(504, f"{stage.capitalize()}: {host} не передаёт данные более {read_timeout} секунд; получено {size} байт. Попробуйте другое издание или повторите позже.") from None
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403 and downloading:
            raise HTTPException(502, f"Источник {host} отказал в скачивании (HTTP 403). Увеличение тайм-аута не поможет. Выберите исходный FB2 ZIP или другое доступное издание. Если скачивание требует входа, загрузите доступный вам файл через браузер и добавьте его в библиотеку.") from None
        raise HTTPException(502, f"{stage.capitalize()}: {host} вернул HTTP {exc.response.status_code}") from None
    except httpx.HTTPError as exc:
        logger.warning("Remote transport error: source=%s host=%s kind=%s", source, host, type(exc).__name__)
        raise HTTPException(502, "Ошибка соединения или HTTP-ответа внешнего источника") from None


def normalized(text):
    return " ".join(re.findall(r"\w+", text.casefold().replace("ё", "е")))


def match_score(title, author, wanted_title, wanted_author):
    if not wanted_title.strip():
        return round(SequenceMatcher(None, normalized(author), normalized(wanted_author)).ratio(), 3) if wanted_author.strip() else 0
    score = SequenceMatcher(None, normalized(title), normalized(wanted_title)).ratio()
    if wanted_author:
        score = 0.7 * score + 0.3 * SequenceMatcher(None, normalized(author), normalized(wanted_author)).ratio()
    return round(score, 3)


@router.get("/api/sources/{source}/search")
def search(source: str, q: str = Query("", max_length=500),
           author: str = Query("", max_length=500), page: int = Query(0, ge=0, le=1000)):
    if source == "zlib":
        from .catalogs import search_books
        candidates = search_books(q, author, page)
        for row in candidates:
            row.update(id=uuid.uuid4().hex, source=source,
                       score=match_score(row["title"], row["author"], q, author))
        return save_candidates(candidates, page)
    template = catalog_template(source)
    url = template.replace("{searchTerms}", quote(q.strip() or author.strip(), safe=""))
    url = url.replace("{startPage?}", str(page)).replace("{startPage}", str(page))
    try:
        data, feed_url = fetch_remote(url, source)
    except HTTPException as exc:
        if source != "flibusta" or origin(url) != ("https", "flibusta.is", 443) or exc.status_code not in (502, 504):
            raise
        logger.info("Flibusta search unavailable; trying fallback flibusta.site")
        data, feed_url = fetch_remote(url.replace("https://flibusta.is", "https://flibusta.site", 1), source)
    # Reject DTDs including UTF-16/32 encodings before parsing untrusted XML.
    if b"<!doctype" in data.replace(b"\x00", b"").lower() or b"<!entity" in data.replace(b"\x00", b"").lower():
        raise HTTPException(502, "DTD и XML-сущности в каталоге не поддерживаются")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise HTTPException(502, "Источник вернул некорректный XML") from None
    if root.tag != f"{{{ATOM['a']}}}feed":
        raise HTTPException(502, "Источник не вернул OPDS Atom feed")
    candidates = []
    xml_base = "{http://www.w3.org/XML/1998/namespace}base"
    root_base = urljoin(feed_url, root.get(xml_base, ""))
    for entry in root.findall("a:entry", ATOM)[:200]:
        title = (entry.findtext("a:title", "", ATOM) or "").strip()[:1000]
        if not title:
            continue
        writer = ", ".join((x.text or "") for x in entry.findall("a:author/a:name", ATOM))[:1000]
        external_id = entry.findtext("a:id", "", ATOM)[:2000]
        for link in entry.findall("a:link", ATOM):
            rel, href = link.get("rel", ""), link.get("href", "")
            # Buy/borrow/subscribe/sample links are not downloadable full books.
            if rel not in ("http://opds-spec.org/acquisition", "http://opds-spec.org/acquisition/open-access") or not href:
                continue
            mime = link.get("type", "").split(";")[0].strip().lower()
            suffix = FORMATS.get(mime)
            if not suffix:
                continue
            entry_base = urljoin(root_base, entry.get(xml_base, ""))
            download_url = urljoin(urljoin(entry_base, link.get(xml_base, "")), href)
            try:
                allowed_url(download_url, source)
            except HTTPException:
                continue
            candidates.append({"id": uuid.uuid4().hex, "source": source, "external_id": external_id,
                               "title": title, "author": writer, "url": download_url, "suffix": suffix,
                               "score": match_score(title, writer, q, author)})
    return save_candidates(candidates, page)


def save_candidates(candidates, page):
    candidates.sort(key=lambda value: value["score"], reverse=True)
    with main.connect() as db:
        db.execute("DELETE FROM remote_candidates WHERE expires_at < ?", (time.time(),))
        for row in candidates:
            db.execute("INSERT INTO remote_candidates VALUES(?,?,?,?,?,?,?,?)",
                       tuple(row[key] for key in ("id", "source", "external_id", "title", "author", "url", "suffix")) + (time.time() + 3600,))
    return {"items": [{k: v for k, v in row.items() if k != "url"} for row in candidates],
            "page": page, "requires_selection": True}


class Acquisition(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=64)
    wish_id: int | None = Field(default=None, ge=1)


def unpack_fb2(archive, destination):
    """Read one bounded FB2 member without extracting any archive-controlled path."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = [entry for entry in bundle.infolist() if not entry.is_dir() and entry.filename.lower().endswith(".fb2")]
            if len(members) != 1:
                raise HTTPException(502, "ZIP должен содержать ровно один файл FB2")
            member = members[0]
            if member.flag_bits & 1:
                raise HTTPException(502, "Зашифрованный ZIP не поддерживается")
            if not 0 < member.file_size <= main.MAX_FILE_SIZE:
                raise HTTPException(413, "Распакованный FB2 пуст или превышает 100 МБ")
            size = 0
            with bundle.open(member) as stream, destination.open("wb") as out:
                while chunk := stream.read(64 * 1024):
                    size += len(chunk)
                    if size > main.MAX_FILE_SIZE:
                        raise HTTPException(413, "Распакованный FB2 превышает 100 МБ")
                    out.write(chunk)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError):
        raise HTTPException(502, "Источник вернул повреждённый или неподдерживаемый ZIP") from None
    return destination


@router.post("/api/sources/import")
def acquire(value: Acquisition):
    with main.connect() as db:
        if value.wish_id is not None and not db.execute("SELECT 1 FROM wishlist WHERE id=?", (value.wish_id,)).fetchone():
            raise HTTPException(404, "Запись списка чтения не найдена")
        row = db.execute("SELECT * FROM remote_candidates WHERE id=? AND expires_at>?",
                         (value.candidate_id, time.time())).fetchone()
    if not row:
        raise HTTPException(404, "Вариант не найден или устарел; повторите поиск")
    main.library_db().parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="acquire-", dir=main.library_db().parent) as folder:
        path = Path(folder) / ("book" + row["suffix"])
        with path.open("wb") as out:
            url = row["url"]
            if row["source"] == "zlib":
                from .catalogs import download_url
                url = download_url(url)
            fetch_remote(url, row["source"], target=out, max_size=main.MAX_FILE_SIZE)
        if row["suffix"] == ".fb2.zip":
            path = unpack_fb2(path, Path(folder) / "book.fb2")
        # Reject common HTML login/error pages advertised as ebook files.
        with path.open("rb") as stream:
            head = stream.read(512).lstrip().lower()
        if head.startswith((b"<!doctype html", b"<html")):
            raise HTTPException(502, "Вместо книги источник вернул HTML-страницу")
        try:
            logger.info("Calibre import starting: source=%s candidate=%s", row["source"], row["id"])
            result = main.add_file(path, row["title"], row["author"])
            if value.wish_id is not None:
                from .library_state import link_book
                with main.connect() as db:
                    link_book(db, value.wish_id, result["id"])
            logger.info("Calibre import complete: status=%s book_id=%s", result["status"], result["id"])
            return result
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc


def fetch_yandex_library():
    from .yandex import fetch_library
    return fetch_library()


def sync_state(status, message):
    with main.connect() as db:
        db.execute("""INSERT INTO integration_state VALUES('yandex',?,?,?)
            ON CONFLICT(name) DO UPDATE SET status=excluded.status,
            updated_at=excluded.updated_at, message=excluded.message""",
            (status, datetime.now(timezone.utc).isoformat(), message))


@router.post("/api/integrations/yandex/sync")
def sync_yandex():
    if not SYNC_LOCK.acquire(blocking=False):
        raise HTTPException(409, "Синхронизация уже выполняется")
    try:
        sync_state("running", "")
        source, books = fetch_yandex_library()
        added, updated = 0, 0
        # Fetch every page first; an upstream error must not leave a partial import.
        with main.connect() as db:
            for book in books:
                previous = db.execute("SELECT wish_id FROM external_wishes WHERE source=? AND external_id=?",
                                      (source, book["external_id"])).fetchone()
                target = db.execute("SELECT id FROM wishlist WHERE title=? AND author=? AND source=?",
                                    (book["title"], book["author"], source)).fetchone()
                if previous and db.execute("SELECT 1 FROM wishlist WHERE id=?", (previous[0],)).fetchone() and not target:
                    db.execute("DELETE FROM wishlist_books WHERE wish_id=? AND method='automatic'", (previous[0],))
                    db.execute("UPDATE wishlist SET title=?,author=? WHERE id=?",
                               (book["title"], book["author"], previous[0]))
                    wish_id = previous[0]
                    updated += 1
                elif target:
                    wish_id = target[0]
                else:
                    wish_id = db.execute("INSERT INTO wishlist(title,author,source) VALUES(?,?,?)",
                                         (book["title"], book["author"], source)).lastrowid
                    added += 1
                db.execute("INSERT OR REPLACE INTO external_wishes VALUES(?,?,?)", (source, book["external_id"], wish_id))
                db.execute("INSERT OR REPLACE INTO external_wish_state VALUES(?,?,?,?)",
                           (source, book["external_id"], book.get("state", ""), datetime.now(timezone.utc).isoformat()))
            from .library_state import reconcile
            linked = reconcile(db)
        result = {"status": "ok", "fetched": len(books), "added": added, "updated": updated, "linked": linked}
        sync_state("ok", json.dumps(result))
        return result
    except HTTPException as exc:
        sync_state("error", str(exc.detail))
        raise
    except Exception:
        sync_state("error", "Ошибка синхронизации; проверьте настройки и доступность сервиса")
        raise HTTPException(502, "Ошибка синхронизации Яндекс Книг") from None
    finally:
        SYNC_LOCK.release()


async def wait_settings(seconds, event):
    await configuration.wait_change(seconds, event)


async def scheduled_sync():
    with configuration.watching('yandex') as changed:
        while True:
            try:
                interval = int(configuration.get("YANDEX_SYNC_INTERVAL_SECONDS", "0"))
            except ValueError:
                interval = 0
            if interval > 0:
                try:
                    await asyncio.to_thread(sync_yandex)
                except HTTPException:
                    pass
            await wait_settings(max(60, interval) if interval > 0 else 3600, changed)


@router.get("/api/integrations")
def integration_status():
    with main.connect() as db:
        states = [dict(row) for row in db.execute("SELECT * FROM integration_state")]
    return {"yandex_configured": bool(configuration.get("YANDEX_BOOK_TOKEN")),
            "flibusta_configured": bool(configuration.get("FLIBUSTA_SEARCH_URL")),
            "opds_configured": bool(configuration.get("OPDS_SEARCH_URL")), "states": states}


@router.get("/integrations", response_class=HTMLResponse)
@router.get("/integrations/results", response_class=HTMLResponse)
def integration_page(request: Request, q: str = Query("", max_length=500), author: str = Query("", max_length=500),
                     source: str = "flibusta", page: int = Query(0, ge=0, le=1000), wish_id: int | None = Query(None, ge=1)):
    error, items = "", []
    if request.url.path == "/integrations/results":
        try:
            items = search(source, q, author, page)["items"]
        except HTTPException as exc:
            error = str(exc.detail)
    return main.ui.sources_page(q, author, source, page, items, error, integration_status(), wish_id)


@router.post("/integrations/import")
def import_form(candidate_id: str = Form(...), wish_id: int | None = Form(None)):
    acquire(Acquisition(candidate_id=candidate_id, wish_id=wish_id))
    return RedirectResponse("/", status_code=303)


@router.post("/integrations/yandex/sync")
def sync_form():
    sync_yandex()
    return RedirectResponse("/integrations", status_code=303)
