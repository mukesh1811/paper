"""Safely fetch and identify public sources before Paper reads them.

This layer deliberately does *not* extract reader blocks or generate text.  It
only answers a small, testable question: did a public URL return a PDF or an
HTML document that later stages can inspect?
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable, Iterable, Literal
from urllib.parse import urljoin, urlparse

import httpcore
import httpx
import pymupdf
from fastapi import HTTPException
from httpcore._backends.auto import AutoBackend

from api.html_source_analysis import HTMLSourceAnalysis, analyze_html_source

MAX_SOURCE_BYTES = 30 * 1024 * 1024
MAX_REDIRECTS = 4
USER_AGENT = "Paper/0.1 (+public-internet reader)"
MIN_PDF_WORDS = 20
# Downloading a large source is the longest wait in the pipeline. Report it
# often enough to stay legible, rarely enough not to flood the event stream.
DOWNLOAD_REPORT_SECONDS = 0.25

SourceType = Literal["pdf", "html"]
ReadabilityRoute = Literal["auto_accept", "needs_intelligence"]


@dataclass(frozen=True)
class InspectedSource:
    """An in-memory public source and its deterministic routing result."""

    url: str
    type: SourceType
    payload: bytes
    content_type: str | None
    readability_route: ReadabilityRoute = "needs_intelligence"
    html_analysis: HTMLSourceAnalysis | None = None


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


class _HtmlSignalParser(HTMLParser):
    """Confirm HTML markup and collect source signals without extracting it."""

    _IGNORED_TAGS = {"head", "script", "style", "template", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.visible_text: list[str] = []
        self.has_html_markup = False
        self._title_depth = 0
        self.title_text: list[str] = []

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self.has_html_markup = True
        if tag in self._IGNORED_TAGS:
            self._ignored_depth += 1
        if tag == "title":
            self._title_depth += 1

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
        words = 0
        for page in document:
            words += len(re.findall(r"\b\w+[’'\-]?\w*\b", page.get_text("text")))
            if words >= MIN_PDF_WORDS:
                return True
        return False
    finally:
        document.close()


def _is_inspectable_html(payload: bytes) -> bool:
    """Accept parseable HTML with source text for the AI readability decision."""

    try:
        html = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Browser HTML defaults to Windows-1252 if it lacks a usable charset.
        # Replacement characters are safer than treating undecodable bytes as a
        # different source type; extraction will apply the final decode policy.
        html = payload.decode("cp1252", errors="replace")

    parser = _HtmlSignalParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return False

    return parser.has_html_markup and bool(parser.visible_text or parser.title_text)


def _identify_source(payload: bytes, content_type: str | None) -> SourceType:
    if _looks_like_pdf(payload):
        if not _is_readable_pdf(payload):
            raise HTTPException(422, "That PDF has no readable selectable text for Paper yet.")
        return "pdf"

    is_html_content_type = content_type in {"text/html", "application/xhtml+xml"}
    if is_html_content_type or _looks_like_html(payload):
        if _is_inspectable_html(payload):
            return "html"
        raise HTTPException(422, "That HTML page has no inspectable public text.")
    raise HTTPException(415, "That URL did not return a supported readable PDF or HTML page.")


def _classify_source(
    payload: bytes,
    content_type: str | None,
) -> tuple[SourceType, HTMLSourceAnalysis | None, ReadabilityRoute]:
    """Identify and route one fetched payload. Runs off the event loop."""

    source_type = _identify_source(payload, content_type)
    html_analysis = analyze_html_source(payload) if source_type == "html" else None
    readability_route: ReadabilityRoute = (
        "auto_accept"
        if html_analysis is not None and html_analysis.has_obvious_reading_surface
        else "needs_intelligence"
    )
    return source_type, html_analysis, readability_route


async def inspect_source(
    url: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    on_download: Callable[[int, int | None], None] | None = None,
    on_fetched: Callable[[], None] | None = None,
) -> InspectedSource:
    """Fetch a public source, follow safe redirects, and identify its format.

    The returned payload is held only in process memory.  Callers decide how
    to extract it; this function neither stores the source nor creates reader
    text from it.  ``transport`` exists solely for hermetic tests.

    ``on_download`` fires with ``(received, total)`` once the final response
    headers arrive and then while the body streams; ``total`` is ``None`` when
    the host sends no content length. ``on_fetched`` fires once the payload is
    complete and before it is identified. Together they let a caller report the
    three genuinely different waits here — locating the source, transferring it,
    and reading it — instead of billing all of them to one stage.
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
            public_addresses = await asyncio.to_thread(assert_public_host, current)
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
                    total = int(content_length) if content_length and content_length.isdigit() else None
                    if total is not None and total > MAX_SOURCE_BYTES:
                        raise HTTPException(413, "That source is larger than the 30 MB MVP limit.")

                    # The source is located once its final response headers land.
                    if on_download is not None:
                        on_download(0, total)

                    data = bytearray()
                    reported_at = time.monotonic()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_SOURCE_BYTES:
                            raise HTTPException(413, "That source is larger than the 30 MB MVP limit.")
                        now = time.monotonic()
                        if on_download is not None and now - reported_at >= DOWNLOAD_REPORT_SECONDS:
                            reported_at = now
                            on_download(len(data), total)
            except httpx.HTTPError as exc:
                raise HTTPException(502, f"Could not fetch that source: {exc}") from exc

            payload = bytes(data)
            content_type = _header_content_type(response)
            if on_fetched is not None:
                on_fetched()
            source_type, html_analysis, readability_route = await asyncio.to_thread(
                _classify_source, payload, content_type
            )
            return InspectedSource(
                url=current,
                type=source_type,
                payload=payload,
                content_type=content_type,
                readability_route=readability_route,
                html_analysis=html_analysis,
            )

    raise HTTPException(502, "Too many redirects while fetching the source.")


async def fetch_pdf(url: str) -> bytes:
    """Compatibility wrapper for the legacy PDF-only reader route."""

    source = await inspect_source(url)
    if source.type != "pdf":
        raise HTTPException(415, "That URL did not return a PDF.")
    return source.payload
