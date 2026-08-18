"""Safely fetch and identify public sources before Paper reads them.

This layer deliberately does *not* extract reader blocks or generate text.  It
only answers a small, testable question: did a public URL return a PDF or a
substantive HTML document that the later readers can handle?
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Iterable, Literal
from urllib.parse import urljoin, urlparse

import httpcore
import httpx
import pymupdf
from fastapi import HTTPException
from httpcore._backends.auto import AutoBackend

MAX_SOURCE_BYTES = 30 * 1024 * 1024
MAX_REDIRECTS = 4
USER_AGENT = "Paper/0.1 (+public-internet reader)"
MIN_HTML_TEXT_CHARACTERS = 200
MIN_PDF_WORDS = 20

SourceType = Literal["pdf", "html"]


@dataclass(frozen=True)
class InspectedSource:
    """An in-memory public source that passed the initial reading checks."""

    url: str
    type: SourceType
    payload: bytes
    content_type: str | None


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect only to the public IP addresses inspected for each hostname."""

    def __init__(self, backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._backend = backend or AutoBackend()
        self._addresses: dict[tuple[str, int], tuple[str, ...]] = {}

    def pin(self, url: str, addresses: Iterable[str]) -> None:
        parsed = urlparse(url)
        assert parsed.hostname is not None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self._addresses[(_normalise_host(parsed.hostname), port)] = tuple(addresses)

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = self._addresses.get((_normalise_host(host), port))
        if not addresses:
            raise httpcore.ConnectError("The source host was not inspected before connecting.")
        return await self._backend.connect_tcp(
            addresses[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("Unix sockets are not allowed for public sources.")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """HTTPX transport using a DNS-pinning network backend.

    HTTPX intentionally resolves hostnames at connection time.  Replacing its
    connection pool ensures that the inspected address, rather than a later
    DNS answer, is the address used for the TCP connection.  TLS still uses
    the URL hostname as SNI and verifies the source certificate normally.
    """

    def __init__(self, backend: _PinnedNetworkBackend) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(
            max_connections=1,
            max_keepalive_connections=0,
            http1=True,
            http2=False,
            network_backend=backend,
        )


class _VisibleTextParser(HTMLParser):
    """Count visible HTML text without pretending to be the HTML extractor."""

    _IGNORED_TAGS = {"head", "script", "style", "template", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.visible_text: list[str] = []
        self.has_content_container = False
        self.has_password_field = False
        self._title_depth = 0
        self.title_text: list[str] = []
        self.long_text_runs = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"article", "main", "p", "section"}:
            self.has_content_container = True
        if tag in self._IGNORED_TAGS:
            self._ignored_depth += 1
        if tag == "title":
            self._title_depth += 1
        if tag == "input" and any(
            name.lower() == "type" and (value or "").lower() == "password"
            for name, value in attrs
        ):
            self.has_password_field = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
        if tag.lower() == "title" and self._title_depth:
            self._title_depth -= 1

    def handle_data(self, data: str) -> None:
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self._title_depth:
            self.title_text.append(text)
        if self._ignored_depth:
            return
        self.visible_text.append(text)
        if len(text) >= 80:
            self.long_text_runs += 1


def clean_public_url(url: str) -> str:
    """Validate a user URL before resolving or requesting it."""

    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(400, "Please enter a public http(s) URL.")
    if parsed.username or parsed.password:
        raise HTTPException(400, "URLs containing credentials are not allowed.")
    return parsed.geturl()


def _normalise_host(host: str) -> str:
    return host.encode("idna").decode("ascii").lower()


def assert_public_host(url: str) -> tuple[str, ...]:
    """Reject hosts that resolve to local or private network addresses."""

    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise HTTPException(400, "Invalid URL.")

    try:
        addresses = socket.getaddrinfo(
            host,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise HTTPException(400, f"Could not resolve host: {host}") from exc

    public_addresses: list[str] = []
    for item in addresses:
        try:
            ip = ipaddress.ip_address(item[4][0])
        except ValueError:
            continue
        if not ip.is_global:
            raise HTTPException(400, "Private or local network URLs are not allowed.")
        public_addresses.append(str(ip))

    if not public_addresses:
        raise HTTPException(400, f"Could not resolve a public address for host: {host}")
    return tuple(dict.fromkeys(public_addresses))


def _header_content_type(response: httpx.Response) -> str | None:
    value = response.headers.get("content-type")
    return value.split(";", 1)[0].strip().lower() if value else None


def _looks_like_pdf(payload: bytes) -> bool:
    # A PDF header is permitted within the first 1,024 bytes of a file.
    return b"%PDF-" in payload[:1024]


def _looks_like_html(payload: bytes) -> bool:
    sample = payload[:4096].lstrip().lower()
    return any(marker in sample for marker in (b"<!doctype html", b"<html", b"<body", b"<main", b"<article"))


def _is_readable_pdf(payload: bytes) -> bool:
    """Confirm that a PDF can supply selectable text to the current reader."""

    try:
        document = pymupdf.open(stream=payload, filetype="pdf")
    except Exception:
        return False

    try:
        if document.page_count == 0:
            return False
        text = " ".join(page.get_text("text") for page in document)
        return len(re.findall(r"\b\w+[’'\-]?\w*\b", text)) >= MIN_PDF_WORDS
    finally:
        document.close()


def _is_readable_html(payload: bytes) -> bool:
    """Reject blank, non-HTML, and clear error or login responses before extraction."""

    try:
        html = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Browser HTML defaults to Windows-1252 if it lacks a usable charset.
        # Replacement characters are safer than treating undecodable bytes as a
        # different source type; extraction will apply the final decode policy.
        html = payload.decode("cp1252", errors="replace")

    parser = _VisibleTextParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return False

    visible_text = " ".join(parser.visible_text)
    title = " ".join(parser.title_text).lower()
    blocked_titles = (
        "access denied",
        "captcha",
        "checking your browser",
        "just a moment",
        "not found",
        "page not found",
        "sign in",
        "log in",
    )
    return (
        parser.has_content_container
        and not parser.has_password_field
        and len(visible_text) >= MIN_HTML_TEXT_CHARACTERS
        and parser.long_text_runs >= 1
        and not any(marker in title for marker in blocked_titles)
    )


def _identify_source(payload: bytes, content_type: str | None) -> SourceType:
    if _looks_like_pdf(payload):
        if not _is_readable_pdf(payload):
            raise HTTPException(422, "That PDF has no readable selectable text for Paper yet.")
        return "pdf"

    is_html_content_type = content_type in {"text/html", "application/xhtml+xml"}
    if (is_html_content_type or _looks_like_html(payload)) and _is_readable_html(payload):
        return "html"

    if is_html_content_type or _looks_like_html(payload):
        raise HTTPException(422, "That HTML page does not contain enough readable public text.")
    raise HTTPException(415, "That URL did not return a supported readable PDF or HTML page.")


async def inspect_source(
    url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> InspectedSource:
    """Fetch a public source, follow safe redirects, and identify its format.

    The returned payload is held only in process memory.  Callers decide how
    to extract it; this function neither stores the source nor creates reader
    text from it.  ``transport`` exists solely for hermetic tests.
    """

    current = clean_public_url(url)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/pdf,text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
    }
    timeout = httpx.Timeout(25.0, connect=10.0)
    pinned_backend = _PinnedNetworkBackend()
    client_transport = transport or _PinnedAsyncHTTPTransport(pinned_backend)

    async with httpx.AsyncClient(
        timeout=timeout,
        headers=headers,
        transport=client_transport,
        trust_env=False,
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            public_addresses = assert_public_host(current)
            if transport is None:
                pinned_backend.pin(current, public_addresses)
            try:
                async with client.stream("GET", current, follow_redirects=False) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise HTTPException(502, "The source host returned an invalid redirect.")
                        current = clean_public_url(urljoin(current, location))
                        continue

                    if response.status_code >= 400:
                        raise HTTPException(502, f"The source host returned HTTP {response.status_code}.")

                    content_length = response.headers.get("content-length")
                    if content_length and content_length.isdigit() and int(content_length) > MAX_SOURCE_BYTES:
                        raise HTTPException(413, "That source is larger than the 30 MB MVP limit.")

                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_SOURCE_BYTES:
                            raise HTTPException(413, "That source is larger than the 30 MB MVP limit.")
            except httpx.HTTPError as exc:
                raise HTTPException(502, f"Could not fetch that source: {exc}") from exc

            payload = bytes(data)
            content_type = _header_content_type(response)
            return InspectedSource(
                url=current,
                type=_identify_source(payload, content_type),
                payload=payload,
                content_type=content_type,
            )

    raise HTTPException(502, "Too many redirects while fetching the source.")


async def fetch_pdf(url: str) -> bytes:
    """Compatibility wrapper for the legacy PDF-only reader route."""

    source = await inspect_source(url)
    if source.type != "pdf":
        raise HTTPException(415, "That URL did not return a PDF.")
    return source.payload
