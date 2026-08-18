import asyncio
from collections.abc import Callable

import httpx
import pymupdf
import pytest
from fastapi import HTTPException

from api.inspect_source import MAX_SOURCE_BYTES, inspect_source
from api.inspect_source import _PinnedNetworkBackend


def response(
    request: httpx.Request,
    *,
    body: bytes,
    content_type: str = "application/octet-stream",
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    all_headers = {"content-type": content_type}
    if headers:
        all_headers.update(headers)
    return httpx.Response(status_code, headers=all_headers, content=body, request=request)


def run(handler: Callable[[httpx.Request], httpx.Response], url: str):
    transport = httpx.MockTransport(handler)
    return asyncio.run(inspect_source(url, transport=transport))


def public_dns(_: str, __: int, type: int):
    return [(None, None, None, None, ("93.184.216.34", 443))]


@pytest.fixture(autouse=True)
def resolve_only_public_hosts(monkeypatch):
    monkeypatch.setattr("api.inspect_source.socket.getaddrinfo", public_dns)


def readable_html() -> bytes:
    return (
        b"<!doctype html><html><body><main><h1>A Short Book</h1><p>"
        + b"Readable public prose for the Paper source inspector. " * 8
        + b"</p></main></body></html>"
    )


def readable_pdf() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_textbox(
        pymupdf.Rect(72, 72, 520, 720),
        "A compact but genuinely readable PDF for the Paper source inspector. " * 5,
        fontsize=12,
    )
    payload = document.tobytes()
    document.close()
    return payload


def test_inspect_source_accepts_a_pdf_by_its_content_not_its_extension():
    source = run(
        lambda request: response(request, body=readable_pdf(), content_type="application/pdf"),
        "https://example.org/download?id=42",
    )

    assert source.type == "pdf"
    assert source.url == "https://example.org/download?id=42"
    assert source.payload.startswith(b"%PDF-")


def test_inspect_source_accepts_a_substantive_html_page():
    source = run(
        lambda request: response(request, body=readable_html(), content_type="text/html; charset=utf-8"),
        "https://example.org/a-book",
    )

    assert source.type == "html"
    assert source.content_type == "text/html"


def test_inspect_source_follows_a_relative_redirect_and_records_the_final_url():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return response(request, body=b"", status_code=302, headers={"location": "/book.pdf"})
        return response(request, body=readable_pdf(), content_type="application/pdf")

    source = run(handler, "https://example.org/start")

    assert source.type == "pdf"
    assert source.url == "https://example.org/book.pdf"


@pytest.mark.parametrize(
    ("body", "content_type", "status", "message"),
    [
        (b"\x89PNG\r\n", "image/png", 415, "supported readable PDF or HTML"),
        (b"<html><body>Sign in</body></html>", "text/html", 422, "enough readable public text"),
        (readable_html().replace(b"<main>", b'<main><input type="password">'), "text/html", 422, "enough readable public text"),
    ],
)
def test_inspect_source_rejects_unsupported_or_unreadable_responses(body, content_type, status, message):
    with pytest.raises(HTTPException) as error:
        run(lambda request: response(request, body=body, content_type=content_type), "https://example.org/source")

    assert error.value.status_code == status
    assert message in error.value.detail


def test_inspect_source_rejects_a_lying_html_content_type():
    with pytest.raises(HTTPException, match="enough readable public text"):
        run(
            lambda request: response(request, body=b"This is not HTML.", content_type="text/html"),
            "https://example.org/not-a-page",
        )


def test_inspect_source_rejects_a_substantive_bot_check_page_by_its_title():
    bot_check = readable_html().replace(
        b"<html>",
        b"<html><head><title>Checking your browser</title></head>",
    )

    with pytest.raises(HTTPException, match="enough readable public text"):
        run(
            lambda request: response(request, body=bot_check, content_type="text/html"),
            "https://example.org/challenge",
        )


def test_inspect_source_uses_html_bytes_when_a_server_omits_the_right_mime_type():
    source = run(
        lambda request: response(request, body=readable_html(), content_type="application/octet-stream"),
        "https://example.org/download",
    )

    assert source.type == "html"


def test_inspect_source_rejects_a_pdf_without_selectable_text():
    with pytest.raises(HTTPException, match="readable selectable text"):
        run(
            lambda request: response(request, body=b"%PDF-1.7\nnot really a PDF", content_type="application/pdf"),
            "https://example.org/broken.pdf",
        )


def test_inspect_source_rejects_oversized_responses_before_reading_them():
    with pytest.raises(HTTPException) as error:
        run(
            lambda request: response(
                request,
                body=b"%PDF-1.7",
                content_type="application/pdf",
                headers={"content-length": str(MAX_SOURCE_BYTES + 1)},
            ),
            "https://example.org/large.pdf",
        )

    assert error.value.status_code == 413


@pytest.mark.parametrize(
    "url",
    [
        "file:///private/book.pdf",
        "https://user:pass@example.org/book.pdf",
    ],
)
def test_inspect_source_rejects_non_public_urls_before_fetching(url):
    with pytest.raises(HTTPException, match="public|credentials"):
        run(lambda request: response(request, body=b"%PDF-1.7"), url)


def test_inspect_source_rejects_a_host_that_resolves_to_private_network(monkeypatch):
    monkeypatch.setattr(
        "api.inspect_source.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("127.0.0.1", 443))],
    )

    with pytest.raises(HTTPException, match="Private or local"):
        run(lambda request: response(request, body=readable_pdf()), "https://example.org/book.pdf")


def test_pinned_network_backend_uses_the_already_inspected_ip_address():
    class RecordingBackend:
        def __init__(self):
            self.calls: list[tuple[str, int]] = []

        async def connect_tcp(self, host, port, **_kwargs):
            self.calls.append((host, port))
            return object()

        async def sleep(self, _seconds):
            return None

    recording = RecordingBackend()
    backend = _PinnedNetworkBackend(backend=recording)
    backend.pin("https://example.org/book", ("93.184.216.34",))

    asyncio.run(backend.connect_tcp("example.org", 443))

    assert recording.calls == [("93.184.216.34", 443)]
