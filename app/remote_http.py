"""Shared request headers for catalogs and their diagnostic probes."""
import os
import re
from urllib.parse import urlsplit, urlunsplit

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
)


def request_headers(downloading=False):
    return {
        "User-Agent": os.environ.get("REMOTE_USER_AGENT", "").strip() or DEFAULT_USER_AGENT,
        "Accept": "*/*" if downloading else "application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
    }


def book_page(url):
    parts = urlsplit(url)
    match = re.fullmatch(r"/b/(\d+)/(epub|mobi|fb2|pdf|download)/?", parts.path)
    return urlunsplit((parts.scheme, parts.netloc, "/b/" + match[1], "", "")) if match else None


def safe_referer(previous, destination):
    if not previous:
        return None
    source, target = urlsplit(previous), urlsplit(destination)
    if source.scheme == "https" and target.scheme != "https":
        return None
    # Exclude query strings, fragments and credentials, including converter keys.
    host = source.hostname or ""
    host = f"[{host}]" if ":" in host else host
    if source.port:
        host += ":" + str(source.port)
    same = (source.scheme, source.hostname, source.port or (443 if source.scheme == 'https' else 80)) == (
        target.scheme, target.hostname, target.port or (443 if target.scheme == 'https' else 80))
    return urlunsplit((source.scheme, host, source.path if same else "/", "", ""))
