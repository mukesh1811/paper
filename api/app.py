from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from time import perf_counter
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator

from api.extract_source import extract_source
from api.inspect_source import InspectedSource, clean_public_url
from api.read_jobs import read_jobs
from api.reader_pipeline import (
    PreparedRead,
    complete_prepared_read,
    extracted_character_count,
    prepare_read,
    source_passport,
)
from api.structure_document import StructureStats
from api.telemetry import (
    allow_telemetry_event,
    emit_read_event,
    local_telemetry_events,
    new_read_id,
    read_failure_reason,
)

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
    allow_methods=["GET", "POST"],
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


@app.get("/telemetry", include_in_schema=False)
def telemetry_dashboard() -> FileResponse:
    """Serve the local-only telemetry viewer when its event file is enabled."""

    if not SERVE_SITE or local_telemetry_events(limit=1) is None:
        raise HTTPException(404, "The local telemetry viewer is not enabled.")
    return _site_file("telemetry.html")


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


def _sse(event: str, payload: dict[str, object]) -> str:
    """Encode one small server-sent event for the preparation surface."""

    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


class BrowserTelemetry(BaseModel):
    """Fields every browser-reported event carries."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source_url: StrictStr = Field(min_length=8, max_length=4096)
    read_id: StrictStr | None = Field(default=None, max_length=36)
    device_id: StrictStr | None = Field(default=None, max_length=36)

    @field_validator("read_id", "device_id")
    @classmethod
    def identifier_is_a_uuid(cls, value: str | None) -> str | None:
        return str(UUID(value)) if value is not None else None


class ReaderOpenedTelemetry(BrowserTelemetry):
    """The browser-confirmed half of a reading event."""

    cache_hit: StrictBool = False
    # How the reader arrived at this source. Recorded because a launch needs to
    # separate people who brought their own document from people who clicked a
    # sample.
    origin: Literal["pasted", "sample", "link", "reload", "unknown"] = "unknown"
    # True when this tab has already rendered this document. Reopening a saved
    # book is worth recording, but it is not another first open.
    repeat: StrictBool = False


class ReadingProgressTelemetry(BrowserTelemetry):
    """How far into a document a reading session actually reached."""

    percent: int = Field(ge=0, le=100)
    # True when the browser is going away, so the figure is final rather than a
    # milestone passed on the way through.
    final: StrictBool = False


def _valid_device_id(value: str | None) -> str | None:
    """Accept only a well-formed browser device id, so the log stays queryable."""

    if not value:
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _structure_details(stats: StructureStats | None) -> dict[str, object]:
    """Describe what structuring cost, when it got far enough to have a cost."""

    if stats is None:
        return {}
    return {
        "chunk_count": stats.chunk_count,
        "retry_count": stats.retry_count,
        "prompt_tokens": stats.estimated_prompt_tokens,
        "completion_tokens": stats.completion_tokens,
        "tokens_measured": stats.prompt_tokens is not None,
    }


def _telemetry_caller(request: Request) -> str:
    """Identify the caller for rate limiting only; it is never logged."""

    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.post("/api/telemetry/reader-opened", status_code=204)
def record_reader_opened(event: ReaderOpenedTelemetry, request: Request) -> Response:
    """Record that Paper rendered a reading copy in the browser.

    A completed preparation does not prove that the reader reached the person:
    they can close a tab or lose a connection first. The client reports this
    event only after ``openReader`` has rendered the document.
    """

    if not allow_telemetry_event(_telemetry_caller(request)):
        return Response(status_code=204)
    emit_read_event(
        "reader_cache_opened" if event.cache_hit else "reader_opened",
        read_id=event.read_id,
        source_url=clean_public_url(event.source_url),
        device_id=event.device_id,
        origin=event.origin,
        repeat=event.repeat,
    )
    return Response(status_code=204)


@app.post("/api/telemetry/reading-progress", status_code=204)
def record_reading_progress(event: ReadingProgressTelemetry, request: Request) -> Response:
    """Record how far a reading session reached.

    Preparing a document is not the point; finishing one is. Progress is already
    kept in the browser so the reader can return to its place, and reporting the
    milestones it passes is what turns "opened" into "read".
    """

    if not allow_telemetry_event(_telemetry_caller(request)):
        return Response(status_code=204)
    emit_read_event(
        "reading_progress",
        read_id=event.read_id,
        source_url=clean_public_url(event.source_url),
        device_id=event.device_id,
        percent=event.percent,
        final=event.final,
    )
    return Response(status_code=204)


@app.get("/api/telemetry/events")
def local_telemetry_event_feed(limit: int = Query(100, ge=1, le=500)) -> dict[str, object]:
    """Return recent events only when local file telemetry is configured."""

    events = local_telemetry_events(limit=limit)
    if events is None:
        raise HTTPException(404, "The local telemetry viewer is not enabled.")
    return {"events": events}


@app.get("/api/read/events")
async def stream_read_document(
    url: str = Query(..., min_length=8, max_length=4096),
    device: str | None = Query(None, max_length=36),
    origin: str | None = Query(None, max_length=16),
) -> StreamingResponse:
    """Keep one request open while it prepares a source and reports real stages.

    This intentionally streams from the request-owning Cloud Run instance. It
    avoids treating a process-local background job as durable state that a
    later poll could safely find on another instance.
    """

    events: asyncio.Queue[tuple[str, dict[str, object]] | None] = asyncio.Queue()
    read_id = new_read_id()
    device_id = _valid_device_id(device)
    source_origin = origin if origin in {"pasted", "sample", "link", "reload"} else "unknown"
    started_at = perf_counter()
    current_stage = "fetching"
    # When each stage began, so the finished run can say where its time went.
    # A total alone cannot tell a slow download from a slow model.
    stage_started_at = {current_stage: started_at}
    stage_ms: dict[str, int] = {}
    structure_stats: StructureStats | None = None
    emit_read_event(
        "read_attempted",
        read_id=read_id,
        source_url=url,
        device_id=device_id,
        stage=current_stage,
        origin=source_origin,
    )

    def close_current_stage() -> None:
        """Add the time spent in the stage that is ending."""

        now = perf_counter()
        began = stage_started_at.get(current_stage)
        if began is None:
            return
        stage_ms[current_stage] = stage_ms.get(current_stage, 0) + round((now - began) * 1000)

    def report(stage: str, prepared: PreparedRead | None, detail: dict[str, object] | None) -> None:
        nonlocal current_stage
        if stage != current_stage:
            # "downloading" reports repeatedly as bytes arrive; only a genuine
            # change of stage closes the one before it.
            close_current_stage()
            current_stage = stage
            stage_started_at[stage] = perf_counter()
        payload: dict[str, object] = {"stage": stage}
        if detail:
            payload.update(detail)
        if prepared is not None:
            payload["passport"] = source_passport(prepared)
        events.put_nowait(("progress", payload))

    def keep_structure_stats(stats: StructureStats) -> None:
        nonlocal structure_stats
        structure_stats = stats

    async def prepare() -> None:
        nonlocal structure_stats
        try:
            prepared = await prepare_read(url, progress=report)
            document = await complete_prepared_read(
                prepared,
                progress=report,
                on_structure_stats=keep_structure_stats,
            )
            close_current_stage()
            elapsed_ms = round((perf_counter() - started_at) * 1000)
            emit_read_event(
                "read_prepared",
                read_id=read_id,
                source_url=document.source.url,
                device_id=device_id,
                stage="complete",
                elapsed_ms=elapsed_ms,
                source_type=document.source.type,
                title=document.metadata.title,
                block_count=len(document.blocks),
                page_count=prepared.extracted.page_count,
                source_bytes=len(prepared.source.payload),
                origin=source_origin,
                stage_ms=dict(stage_ms),
                **_structure_details(structure_stats),
            )
            events.put_nowait(
                (
                    "complete",
                    {
                        "document": document.model_dump(mode="json", by_alias=True, exclude_none=True),
                        "read_id": read_id,
                    },
                )
            )
        except HTTPException as exc:
            close_current_stage()
            elapsed_ms = round((perf_counter() - started_at) * 1000)
            event = "read_rejected" if 400 <= exc.status_code < 500 else "read_failed"
            emit_read_event(
                event,
                read_id=read_id,
                source_url=url,
                device_id=device_id,
                stage=current_stage,
                elapsed_ms=elapsed_ms,
                status_code=exc.status_code,
                reason=read_failure_reason(exc.status_code, exc.detail),
                origin=source_origin,
                stage_ms=dict(stage_ms),
                **_structure_details(structure_stats),
            )
            events.put_nowait(("error", {"detail": str(exc.detail)}))
        except Exception:
            close_current_stage()
            elapsed_ms = round((perf_counter() - started_at) * 1000)
            emit_read_event(
                "read_failed",
                read_id=read_id,
                source_url=url,
                device_id=device_id,
                stage=current_stage,
                elapsed_ms=elapsed_ms,
                status_code=500,
                reason="unhandled_error",
                origin=source_origin,
                stage_ms=dict(stage_ms),
                **_structure_details(structure_stats),
            )
            events.put_nowait(("error", {"detail": "Paper could not prepare that document."}))
        finally:
            events.put_nowait(None)

    async def event_stream():
        task = asyncio.create_task(prepare())
        yield _sse("progress", {"stage": "fetching"})
        try:
            while event := await events.get():
                event_name, payload = event
                yield _sse(event_name, payload)
        finally:
            if not task.done():
                close_current_stage()
                emit_read_event(
                    "read_abandoned",
                    read_id=read_id,
                    source_url=url,
                    device_id=device_id,
                    stage=current_stage,
                    elapsed_ms=round((perf_counter() - started_at) * 1000),
                    origin=source_origin,
                    stage_ms=dict(stage_ms),
                )
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/read", response_model=None)
async def read_document(
    url: str = Query(..., min_length=8, max_length=4096),
    background: bool = Query(False),
) -> dict | JSONResponse:
    if background:
        job = await read_jobs.submit_url(url)
        return JSONResponse(
            status_code=202,
            content={
                "job_id": job.id,
                "status": job.status,
                "stage": job.stage,
                "status_url": f"/api/read/jobs/{job.id}",
            },
        )

    prepared = await prepare_read(url)
    if extracted_character_count(prepared.extracted) > BACKGROUND_CHARACTER_THRESHOLD:
        job = await read_jobs.submit(prepared)
        return JSONResponse(
            status_code=202,
            content={
                "job_id": job.id,
                "status": job.status,
                "stage": job.stage,
                "status_url": f"/api/read/jobs/{job.id}",
            },
        )
    document = await complete_prepared_read(prepared)
    return document.model_dump(mode="json", by_alias=True, exclude_none=True)


@app.get("/api/read/jobs/{job_id}")
def read_job(job_id: str) -> dict:
    job = read_jobs.get(job_id)
    response: dict[str, object] = {"status": job.status, "stage": job.stage}
    if job.passport is not None:
        response["passport"] = job.passport
    if job.status == "complete" and job.document is not None:
        response["document"] = job.document.model_dump(mode="json", by_alias=True, exclude_none=True)
        return response
    if job.status == "failed":
        response["detail"] = job.error_detail or "Paper could not prepare that document."
    return response
