"""Compare HTTP clients against the same search endpoint; never print secrets."""
import json
import argparse
import os
import tempfile
import time
import zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, getproxies

import httpx
from .build import fingerprint
from .remote_http import request_headers


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(url, client):
    started = time.monotonic()
    result = {"client": client}
    headers = request_headers()
    if client == "httpx_legacy_ua":
        headers["User-Agent"] = "Yandex2Kindle/1.0"
    try:
        if client == "urllib":
            request = Request(url, headers=headers)
            with build_opener(NoRedirect()).open(request, timeout=20) as response:
                result.update(status=response.status, sample_bytes=len(response.read(1024)))
        else:
            with httpx.Client(timeout=20, trust_env=client != "httpx_direct", follow_redirects=False,
                              headers=headers) as session:
                with session.stream("GET", url) as response:
                    result["status"] = response.status_code
        result["ok"] = result["status"] == 200
    except HTTPError as exc:
        result.update(status=exc.code, ok=False)
        exc.close()
    except Exception as exc:
        # Exception text may contain proxy passwords or signed URLs.
        result.update(error=type(exc).__name__, ok=False)
    result["elapsed_seconds"] = round(time.monotonic() - started, 1)
    return result


def download_probe(url):
    # Import the application first so its routers finish initialization.
    from . import main as application
    from .integrations import fetch_remote
    from fastapi import HTTPException
    trace = []
    result = {"build": fingerprint(), "mode": "full_download",
              "http2_enabled": os.environ.get("REMOTE_HTTP2", "true").lower() == "true",
              "book_session": os.environ.get("REMOTE_BOOK_SESSION", "true").lower() == "true"}
    try:
        with tempfile.TemporaryDirectory(prefix="y2k-download-probe-") as folder:
            path = Path(folder) / "book.epub"
            with path.open("wb") as stream:
                fetch_remote(url, "flibusta", target=stream, max_size=application.MAX_FILE_SIZE, trace=trace)
            result["bytes"] = path.stat().st_size
            valid = False
            try:
                with zipfile.ZipFile(path) as bundle:
                    if bundle.getinfo("mimetype").file_size <= 128:
                        valid = bundle.read("mimetype").strip() == b"application/epub+zip"
            except (zipfile.BadZipFile, KeyError):
                pass
            result.update(ok=valid, epub_valid=valid)
            if not valid:
                result["detail"] = "Ответ получен, но не является EPUB с корректным mimetype"
    except HTTPException as exc:
        result.update(ok=False, status=exc.status_code, detail=exc.detail)
    except Exception as exc:
        result.update(ok=False, error=type(exc).__name__)
    result["redirect_chain"] = trace
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if result.get("ok") else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Диагностика поиска и скачивания Flibusta без вывода секретов")
    parser.add_argument("--download", metavar="URL", help="Скачать и проверить полный EPUB во временной папке")
    parser.add_argument("--http1", action="store_true", help="Отключить HTTP/2 для сравнительной проверки")
    parser.add_argument("--no-session", action="store_true", help="Не запрашивать страницу книги перед скачиванием")
    args = parser.parse_args(argv)
    if args.http1:
        os.environ["REMOTE_HTTP2"] = "false"
    if args.no_session:
        os.environ["REMOTE_BOOK_SESSION"] = "false"
    if args.download:
        return download_probe(args.download)
    template = os.environ.get("FLIBUSTA_SEARCH_URL", "")
    if not template or "{searchTerms}" not in template:
        print("FLIBUSTA_SEARCH_URL не настроен или не содержит {searchTerms}")
        return 1
    search = template.replace("{searchTerms}", quote("Дюна", safe=""))
    search = search.replace("{startPage?}", "0").replace("{startPage}", "0")
    parts = urlsplit(search)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        print("Некорректный адрес каталога")
        return 1
    print(json.dumps({"build": fingerprint(), "host": parts.hostname, "path": parts.path,
                      "connect_timeout_seconds": os.environ.get("REMOTE_CONNECT_TIMEOUT_SECONDS", "60"),
                      "trust_env": os.environ.get("REMOTE_TRUST_ENV", "true").lower() == "true",
                      "proxy_types_present": sorted(getproxies())}, ensure_ascii=False), flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda client: probe(search, client), ("urllib", "httpx_env", "httpx_direct", "httpx_legacy_ua")))
    print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)
    return 0 if any(result["ok"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
