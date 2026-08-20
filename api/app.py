from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from api.extract_source import extract_source
from api.inspect_source import InspectedSource
from api.read_jobs import read_jobs
from api.reader_pipeline import complete_prepared_read, extracted_character_count, prepare_read

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
STATIC_DIR = Path(os.getenv("PAPER_SITE_DIR", str(PROJECT_DIR / "site")))
SERVE_SITE = os.getenv("PAPER_SERVE_SITE", "true").lower() in {"1", "true", "yes"}
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
BACKGROUND_CHARACTER_THRESHOLD = int(os.getenv("PAPER_BACKGROUND_CHARACTER_THRESHOLD", "200000"))


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


def extract_book(pdf_bytes: bytes) -> dict:
    """Compatibility view backed by the source-grounded PDF extractor."""

    extracted = extract_source(
        InspectedSource(
            url="https://paper.invalid/legacy.pdf",
            type="pdf",
            payload=pdf_bytes,
            content_type="application/pdf",
        )
    )
    word_count = sum(len(block.text.split()) for block in extracted.blocks)
    return {
        "title": extracted.metadata.title,
        "author": extracted.metadata.author or "",
        "pages": extracted.page_count or 0,
        "word_count": word_count,
        "reading_minutes": max(1, round(word_count / 230)),
        "blocks": [{"type": block.type, "text": block.text} for block in extracted.blocks],
    }


@app.get("/api/read", response_model=None)
async def read_document(
    url: str = Query(..., min_length=8, max_length=4096),
    background: bool = Query(False),
) -> dict | JSONResponse:
    prepared = await prepare_read(url)
    if background or extracted_character_count(prepared.extracted) > BACKGROUND_CHARACTER_THRESHOLD:
        job = await read_jobs.submit(prepared)
        return JSONResponse(
            status_code=202,
            content={"job_id": job.id, "status": job.status, "status_url": f"/api/read/jobs/{job.id}"},
        )
    document = await complete_prepared_read(prepared)
    return document.model_dump(mode="json", by_alias=True, exclude_none=True)


@app.get("/api/read/jobs/{job_id}")
def read_job(job_id: str) -> dict:
    job = read_jobs.get(job_id)
    if job.status == "complete" and job.document is not None:
        return {"status": "complete", "document": job.document.model_dump(mode="json", by_alias=True, exclude_none=True)}
    if job.status == "failed":
        return {"status": "failed", "detail": job.error_detail or "Paper could not prepare that document."}
    return {"status": "running"}
