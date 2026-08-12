from __future__ import annotations

import ipaddress
import os
import re
import socket
from collections import Counter
from pathlib import Path
from statistics import median
from urllib.parse import urljoin, urlparse

import fitz  # PyMuPDF
import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
STATIC_DIR = Path(os.getenv("PAPER_SITE_DIR", str(PROJECT_DIR / "site")))
SERVE_SITE = os.getenv("PAPER_SERVE_SITE", "true").lower() in {"1", "true", "yes"}
MAX_PDF_BYTES = 30 * 1024 * 1024
MAX_REDIRECTS = 4
USER_AGENT = "Paper/0.1 (+PDF reflow reader)"

app = FastAPI(title="Paper", version="0.1.0")


def _cors_origin(value: str) -> str:
    """Accept origins with or without an accidental path, such as /paper."""
    value = value.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return value


allowed_origins = [
    _cors_origin(origin)
    for origin in os.getenv(
        "PAPER_ALLOWED_ORIGINS",
        "https://mukesh1811.github.io,http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET"],
    allow_headers=["*"],
)


class PublicAssetFiles(StaticFiles):
    """Serve CSS/JS/images without exposing page HTML at duplicate URLs."""

    async def get_response(self, path: str, scope: dict):
        if Path(path).suffix.lower() in {".html", ".htm"}:
            return Response(status_code=404)
        return await super().get_response(path, scope)


if SERVE_SITE:
    app.mount("/static", PublicAssetFiles(directory=STATIC_DIR), name="static")
SITE_URL = os.getenv("PAPER_SITE_URL", "http://localhost:8000").rstrip("/")


def _site_file(filename: str) -> FileResponse:
    if not SERVE_SITE:
        raise HTTPException(404, "The Paper frontend is hosted separately.")
    return FileResponse(STATIC_DIR / filename)


@app.get("/")
def index() -> Response:
    if not SERVE_SITE:
        return JSONResponse({"service": "paper-api", "status": "ok"})
    return _site_file("index.html")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/read")
def read() -> FileResponse:
    return _site_file("read.html")


@app.get("/features")
def features() -> FileResponse:
    return _site_file("features.html")


@app.get("/how-it-works")
def how_it_works() -> FileResponse:
    return _site_file("how-it-works.html")


@app.get("/read-pdf-online")
def read_pdf_online() -> FileResponse:
    return _site_file("read-pdf-online.html")


@app.get("/read-pdf-on-phone")
def read_pdf_on_phone() -> FileResponse:
    return _site_file("read-pdf-on-phone.html")


@app.get("/read-pdf-like-a-book")
def read_pdf_like_a_book() -> FileResponse:
    return _site_file("read-pdf-like-a-book.html")


@app.get("/pdf-reflow")
def pdf_reflow() -> FileResponse:
    return _site_file("pdf-reflow.html")


@app.get("/read-pdf-without-downloading")
def read_pdf_without_downloading() -> FileResponse:
    return _site_file("read-pdf-without-downloading.html")


@app.get("/open-pdf-link-online")
def open_pdf_link_online() -> FileResponse:
    return _site_file("open-pdf-link-online.html")


@app.get("/pdf-reader-for-long-documents")
def pdf_reader_for_long_documents() -> FileResponse:
    return _site_file("pdf-reader-for-long-documents.html")


@app.get("/read-research-papers")
def read_research_papers() -> FileResponse:
    return _site_file("read-research-papers.html")


@app.get("/formats")
def formats() -> FileResponse:
    return _site_file("formats.html")


@app.get("/blog")
def blog() -> FileResponse:
    return _site_file("blog.html")


@app.get("/blog/read-pdf-like-a-book")
def blog_read_pdf_like_a_book() -> FileResponse:
    return _site_file("blog-read-pdf-like-a-book.html")


@app.get("/blog/best-way-to-read-pdf-on-phone")
def blog_best_way_to_read_pdf_on_phone() -> FileResponse:
    return _site_file("blog-best-way-to-read-pdf-on-phone.html")


@app.get("/blog/what-is-pdf-reflow")
def blog_what_is_pdf_reflow() -> FileResponse:
    return _site_file("blog-what-is-pdf-reflow.html")


@app.get("/blog/pdf-selectable-text")
def blog_pdf_selectable_text() -> FileResponse:
    return _site_file("blog-pdf-selectable-text.html")


@app.get("/blog/why-two-column-pdfs-are-hard-to-read")
def blog_two_column_pdfs() -> FileResponse:
    return _site_file("blog-why-two-column-pdfs-are-hard-to-read.html")


@app.get("/blog/pdf-reflow-vs-pdf-viewer")
def blog_pdf_reflow_vs_viewer() -> FileResponse:
    return _site_file("blog-pdf-reflow-vs-pdf-viewer.html")


@app.get("/blog/read-long-report-without-losing-place")
def blog_read_long_report() -> FileResponse:
    return _site_file("blog-read-long-report-without-losing-place.html")


@app.get("/blog/pdf-vs-epub")
def blog_pdf_vs_epub() -> FileResponse:
    return _site_file("blog-pdf-vs-epub.html")


@app.get("/blog/scanned-pdf-reflow")
def blog_scanned_pdf_reflow() -> FileResponse:
    return _site_file("blog-scanned-pdf-reflow.html")


@app.get("/blog/pdf-reading-setup")
def blog_pdf_reading_setup() -> FileResponse:
    return _site_file("blog-pdf-reading-setup.html")


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    if not SERVE_SITE:
        return "User-agent: *\nDisallow: /\n"
    return f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {SITE_URL}/sitemap.xml\n"


@app.get("/sitemap.xml")
def sitemap() -> Response:
    if not SERVE_SITE:
        return Response(status_code=404)
    paths = [
        "/",
        "/features",
        "/how-it-works",
        "/read-pdf-online",
        "/read-pdf-on-phone",
        "/read-pdf-like-a-book",
        "/pdf-reflow",
        "/read-pdf-without-downloading",
        "/open-pdf-link-online",
        "/pdf-reader-for-long-documents",
        "/read-research-papers",
        "/formats",
        "/blog",
        "/blog/read-pdf-like-a-book",
        "/blog/best-way-to-read-pdf-on-phone",
        "/blog/what-is-pdf-reflow",
        "/blog/pdf-selectable-text",
        "/blog/why-two-column-pdfs-are-hard-to-read",
        "/blog/pdf-reflow-vs-pdf-viewer",
        "/blog/read-long-report-without-losing-place",
        "/blog/pdf-vs-epub",
        "/blog/scanned-pdf-reflow",
        "/blog/pdf-reading-setup",
    ]
    urls = "".join(f"<url><loc>{SITE_URL}{path}</loc></url>" for path in paths)
    body = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return Response(content=body, media_type="application/xml")


def _clean_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(400, "Please enter a public http(s) PDF URL.")
    if parsed.username or parsed.password:
        raise HTTPException(400, "URLs containing credentials are not allowed.")
    return parsed.geturl()


def _assert_public_host(url: str) -> None:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise HTTPException(400, "Invalid URL.")

    try:
        addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise HTTPException(400, f"Could not resolve host: {host}") from exc

    for item in addresses:
        raw_ip = item[4][0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        if not ip.is_global:
            raise HTTPException(400, "Private or local network URLs are not allowed.")


async def fetch_pdf(url: str) -> bytes:
    current = _clean_url(url)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*;q=0.8"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=10.0), headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            _assert_public_host(current)
            try:
                async with client.stream("GET", current, follow_redirects=False) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise HTTPException(502, "The PDF host returned an invalid redirect.")
                        current = _clean_url(urljoin(current, location))
                        continue

                    if response.status_code >= 400:
                        raise HTTPException(502, f"The PDF host returned HTTP {response.status_code}.")

                    content_length = response.headers.get("content-length")
                    if content_length and content_length.isdigit() and int(content_length) > MAX_PDF_BYTES:
                        raise HTTPException(413, "That PDF is larger than the 30 MB MVP limit.")

                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_PDF_BYTES:
                            raise HTTPException(413, "That PDF is larger than the 30 MB MVP limit.")
                    payload = bytes(data)
                    if not payload.startswith(b"%PDF-"):
                        raise HTTPException(415, "That URL did not return a PDF.")
                    return payload
            except httpx.HTTPError as exc:
                raise HTTPException(502, f"Could not fetch that PDF: {exc}") from exc

    raise HTTPException(502, "Too many redirects while fetching the PDF.")


def _norm_repeat(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip().lower()
    text = re.sub(r"\d+", "#", text)
    return text[:180]


def _clean_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""

    out = lines[0]
    for line in lines[1:]:
        if out.endswith("-") and line and line[0].islower():
            out = out[:-1] + line
        else:
            out += " " + line
    return re.sub(r"\s+", " ", out).strip()


def extract_book(pdf_bytes: bytes) -> dict:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise HTTPException(422, "The PDF could not be parsed.") from exc

    if doc.page_count == 0:
        doc.close()
        raise HTTPException(422, "The PDF has no pages.")

    pages: list[list[dict]] = []
    body_sizes: list[float] = []
    edge_candidates: list[str] = []

    for page_index in range(doc.page_count):
        page = doc[page_index]
        page_h = max(float(page.rect.height), 1.0)
        raw = page.get_text("dict")
        page_blocks: list[dict] = []

        for block in raw.get("blocks", []):
            if "lines" not in block:
                continue
            pieces: list[str] = []
            sizes: list[float] = []
            fonts: list[str] = []
            for line in block.get("lines", []):
                line_text = "".join(span.get("text", "") for span in line.get("spans", []))
                pieces.append(line_text)
                for span in line.get("spans", []):
                    txt = span.get("text", "").strip()
                    if txt:
                        sizes.append(float(span.get("size", 0) or 0))
                        fonts.append(str(span.get("font", "")))
            text = _clean_text("\n".join(pieces))
            if not text:
                continue
            bbox = block.get("bbox", (0, 0, 0, 0))
            y0, y1 = float(bbox[1]), float(bbox[3])
            max_size = max(sizes) if sizes else 0.0
            avg_size = sum(sizes) / len(sizes) if sizes else 0.0
            boldish = any("bold" in f.lower() or "black" in f.lower() or "semibold" in f.lower() for f in fonts)
            item = {
                "text": text,
                "max_size": max_size,
                "avg_size": avg_size,
                "bold": boldish,
                "edge": y1 < page_h * 0.13 or y0 > page_h * 0.87,
            }
            page_blocks.append(item)
            if item["edge"] and len(text) <= 180:
                edge_candidates.append(_norm_repeat(text))
            elif 5 <= len(text) and sizes:
                body_sizes.extend(s for s in sizes if s > 0)
        pages.append(page_blocks)

    repeat_counts = Counter(edge_candidates)
    repeat_threshold = max(3, int(doc.page_count * 0.25))
    repeated_edges = {k for k, count in repeat_counts.items() if count >= repeat_threshold and k}
    base_size = median(body_sizes) if body_sizes else 11.0

    blocks: list[dict] = []
    seen_title = False
    for page_blocks in pages:
        for item in page_blocks:
            text = item["text"]
            normalized = _norm_repeat(text)
            if item["edge"] and normalized in repeated_edges:
                continue
            if re.fullmatch(r"(?:page\s*)?\d+", text, flags=re.I):
                continue

            short = len(text) <= 120
            heading = short and (
                item["max_size"] >= base_size * 1.24
                or (item["bold"] and item["max_size"] >= base_size * 1.05)
                or (len(text) <= 70 and text.isupper() and len(text) > 3)
            )
            kind = "heading" if heading else "paragraph"

            if blocks and blocks[-1]["type"] == kind and blocks[-1]["text"] == text:
                continue
            blocks.append({"type": kind, "text": text})
            if kind == "heading" and not seen_title:
                seen_title = True

    metadata = doc.metadata or {}
    doc.close()

    word_count = sum(len(re.findall(r"\b\w+[’'\-]?\w*\b", b["text"])) for b in blocks)
    reading_minutes = max(1, round(word_count / 230))

    title = (metadata.get("title") or "").strip()
    if not title:
        first_heading = next((b["text"] for b in blocks if b["type"] == "heading" and len(b["text"]) < 140), None)
        title = first_heading or "Untitled PDF"

    if not blocks or word_count < 20:
        raise HTTPException(422, "I couldn't find enough selectable text. This MVP doesn't OCR scanned PDFs yet.")

    return {
        "title": title,
        "author": (metadata.get("author") or "").strip(),
        "pages": len(pages),
        "word_count": word_count,
        "reading_minutes": reading_minutes,
        "blocks": blocks,
    }


@app.get("/api/read")
async def read_pdf(url: str = Query(..., min_length=8, max_length=4096)) -> dict:
    payload = await fetch_pdf(url)
    return extract_book(payload)
