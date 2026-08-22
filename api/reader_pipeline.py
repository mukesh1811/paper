"""The source-to-reader pipeline behind `/api/read`."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Literal
from urllib.parse import urlparse

from fastapi import HTTPException

from api.document import PaperDocument
from api.extract_source import ExtractedSource, extract_source
from api.inspect_readability import inspect_readability
from api.inspect_source import inspect_source
from api.structure_document import materialize_document, structure_document


@dataclass(frozen=True)
class PreparedRead:
    """An accepted source held in memory until it is structured for reading."""

    source: InspectedSource
    extracted: ExtractedSource


PipelineStage = Literal["downloading", "checking", "extracting", "structuring", "validating"]
ProgressCallback = Callable[[PipelineStage, PreparedRead | None, dict[str, object] | None], None]


def _report(
    progress: ProgressCallback | None,
    stage: PipelineStage,
    prepared: PreparedRead | None = None,
    detail: dict[str, object] | None = None,
) -> None:
    if progress is not None:
        progress(stage, prepared, detail)


async def prepare_read(url: str, *, progress: ProgressCallback | None = None) -> PreparedRead:
    """Fetch, inspect, and extract a public source without persisting it.

    Stages are reported against the work actually in flight. Locating the source
    ends when its response headers arrive; transferring it is its own stage,
    reported with byte counts because it is usually the longest wait; reading
    and judging it is "checking". Extraction runs off the event loop so its
    stage reaches the client while it is happening rather than once it is over.
    """

    def report_download(received: int, total: int | None) -> None:
        _report(progress, "downloading", None, {"received": received, "total": total})

    source = await inspect_source(
        url,
        on_download=report_download,
        on_fetched=lambda: _report(progress, "checking"),
    )
    decision = await inspect_readability(source)
    if not decision.accepted:
        raise HTTPException(422, "That URL is not a readable document for Paper.")
    _report(progress, "extracting")
    extracted = await asyncio.to_thread(extract_source, source)
    return PreparedRead(source=source, extracted=extracted)


async def complete_prepared_read(
    prepared: PreparedRead,
    *,
    progress: ProgressCallback | None = None,
) -> PaperDocument:
    """Arrange references and return a document whose blocks are validated."""

    _report(progress, "structuring", prepared)
    plan = await structure_document(prepared.extracted)
    _report(progress, "validating", prepared)
    return materialize_document(prepared.source, prepared.extracted, plan)


async def read_source(url: str) -> PaperDocument:
    """Fetch, inspect, extract, structure, and validate one public source."""

    return await complete_prepared_read(await prepare_read(url))


def extracted_character_count(extracted: ExtractedSource) -> int:
    """Measure exact text so routes can defer unusually large reader jobs."""

    return sum(len(block.text) for block in extracted.blocks)


def source_passport(prepared: PreparedRead) -> dict[str, object]:
    """Return small, exact source facts that make preparation intelligible.

    These fields are derived only from the fetched source and deterministic
    extraction. They deliberately do not contain a generated summary.
    """

    blocks = prepared.extracted.blocks
    word_count = sum(len(block.text.split()) for block in blocks)
    opening = next(
        (block.text for block in blocks if block.type == "paragraph"),
        blocks[0].text,
    )
    host = urlparse(prepared.source.url).hostname or prepared.source.url
    return {
        "source_host": host,
        "source_type": prepared.source.type,
        "title": prepared.extracted.metadata.title,
        "author": prepared.extracted.metadata.author,
        "language": prepared.extracted.metadata.language,
        "page_count": prepared.extracted.page_count,
        "word_count": word_count,
        "reading_minutes": max(1, round(word_count / 230)),
        "section_count": sum(block.type == "heading" for block in blocks),
        "opening_text": opening,
    }
