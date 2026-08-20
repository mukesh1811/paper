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


def obvious_book_html() -> bytes:
    body = "A continuous chapter of a public book, written for a person to read. " * 700
    return f"<!doctype html><html><body><main><h1>A Long Book</h1><p>{body}</p></main></body></html>".encode()


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
    assert source.readability_route == "needs_intelligence"


def test_inspect_source_auto_accepts_a_large_low_link_reading_surface():
    source = run(
        lambda request: response(request, body=obvious_book_html(), content_type="text/html"),
        "https://example.org/full-book",
    )

    assert source.readability_route == "auto_accept"
    assert source.html_analysis is not None
    assert source.html_analysis.reading_surface["tag"] == "main"
    assert source.html_analysis.reading_surface["visible_text_characters"] >= 40_000
    assert source.html_analysis.reading_surface["link_text_ratio"] == 0.0


def test_inspect_source_keeps_a_large_link_heavy_catalog_for_intelligence():
    entries = "".join(
        f"<li><a href='/work-{index}'>A catalog entry with a long linked title {index}</a></li>"
        for index in range(1_000)
    )
    catalog = f"<html><body><main><h1>Catalog</h1><ul>{entries}</ul></main></body></html>".encode()

    source = run(
        lambda request: response(request, body=catalog, content_type="text/html"),
        "https://example.org/catalog",
    )

    assert source.readability_route == "needs_intelligence"
    assert source.html_analysis is not None
    assert source.html_analysis.reading_surface["visible_text_characters"] >= 40_000
    assert source.html_analysis.reading_surface["link_text_ratio"] == 1.0


def test_inspect_source_uses_the_whole_body_when_a_book_is_split_into_many_chapters():
    chapter = "A chapter of a public book, written for a person to read. " * 260
    html = "<html><body><h1>A Long Book</h1>" + "".join(
        f"<div><h2>Chapter {number}</h2><p>{chapter}</p></div>"
        for number in range(1, 8)
    ) + "</body></html>"

    source = run(
        lambda request: response(request, body=html.encode(), content_type="text/html"),
        "https://example.org/many-chapters",
    )

    assert source.readability_route == "auto_accept"
    assert source.html_analysis is not None
    assert source.html_analysis.reading_surface["selection"] == "body_fallback"


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
        (b"This is not HTML.", "text/html", 422, "inspectable public text"),
    ],
)
def test_inspect_source_rejects_unsupported_or_unreadable_responses(body, content_type, status, message):
    with pytest.raises(HTTPException) as error:
        run(lambda request: response(request, body=body, content_type=content_type), "https://example.org/source")

    assert error.value.status_code == status
    assert message in error.value.detail


def test_inspect_source_rejects_a_lying_html_content_type():
    with pytest.raises(HTTPException, match="inspectable public text"):
        run(
            lambda request: response(request, body=b"This is not HTML.", content_type="text/html"),
            "https://example.org/not-a-page",
        )


def test_inspect_source_defers_login_and_bot_check_judgment_to_the_ai_step():
    bot_check = readable_html().replace(
        b"<html>",
        b"<html><head><title>Checking your browser</title></head>",
    )

    source = run(
        lambda request: response(request, body=bot_check, content_type="text/html"),
        "https://example.org/challenge",
    )

    assert source.type == "html"


def test_inspect_source_accepts_old_html_markup_for_the_ai_step_to_judge():
    old_markup = b"<html><body><font>" + b"A long public essay in old markup. " * 20 + b"</font></body></html>"

    source = run(
        lambda request: response(request, body=old_markup, content_type="text/html"),
        "https://example.org/old-essay",
    )

    assert source.type == "html"


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
