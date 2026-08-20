"""The source-to-reader pipeline behind `/api/read`."""

from __future__ import annotations

from dataclasses import dataclass

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


async def prepare_read(url: str) -> PreparedRead:
    """Fetch, inspect, and extract a public source without persisting it."""

    source = await inspect_source(url)
    decision = await inspect_readability(source)
    if not decision.accepted:
        raise HTTPException(422, "That URL is not a readable document for Paper.")
    return PreparedRead(source=source, extracted=extract_source(source))


async def complete_prepared_read(prepared: PreparedRead) -> PaperDocument:
    """Arrange references and return a document whose blocks are validated."""

    plan = await structure_document(prepared.extracted)
    return materialize_document(prepared.source, prepared.extracted, plan)


async def read_source(url: str) -> PaperDocument:
    """Fetch, inspect, extract, structure, and validate one public source."""

    return await complete_prepared_read(await prepare_read(url))


def extracted_character_count(extracted: ExtractedSource) -> int:
    """Measure exact text so routes can defer unusually large reader jobs."""

    return sum(len(block.text) for block in extracted.blocks)
