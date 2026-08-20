import asyncio

import pytest
from fastapi import HTTPException

from api.extract_source import extract_source
from api.inspect_readability import ReadabilityDecision
from api.inspect_source import InspectedSource
from api.reader_pipeline import read_source
from api.structure_document import BlockRange, StructurePlan


def source():
    return InspectedSource(
        url="https://example.org/essay",
        type="html",
        content_type="text/html",
        payload=(
            b"<html><body><main><h1>Exact Essay</h1>"
            b"<p>This is a coherent source paragraph with enough words for a test document to pass validation today.</p>"
            b"<p>A second exact source paragraph completes the readable public essay without added prose.</p>"
            b"</main></body></html>"
        ),
    )


def test_pipeline_returns_a_validated_reader_document(monkeypatch):
    inspected = source()
    extracted = extract_source(inspected)

    async def fake_inspect_source(url):
        assert url == inspected.url
        return inspected

    async def fake_readability(_source):
        return ReadabilityDecision(verdict="accept", evidence_ids=("b1",))

    async def fake_structure(_extracted):
        return StructurePlan(ranges=(BlockRange(start_id="b1", end_id="b3"),))

    monkeypatch.setattr("api.reader_pipeline.inspect_source", fake_inspect_source)
    monkeypatch.setattr("api.reader_pipeline.inspect_readability", fake_readability)
    monkeypatch.setattr("api.reader_pipeline.structure_document", fake_structure)

    document = asyncio.run(read_source(inspected.url))

    assert document.source.url == inspected.url
    assert document.metadata.title == "Exact Essay"
    assert [block.id for block in document.blocks] == ["b1", "b2", "b3"]


def test_pipeline_stops_before_extraction_when_source_is_rejected(monkeypatch):
    inspected = source()

    async def fake_inspect_source(_url):
        return inspected

    async def fake_readability(_source):
        return ReadabilityDecision(verdict="reject", evidence_ids=("b1",))

    def extraction_must_not_run(_source):
        raise AssertionError("rejected sources must not be extracted")

    monkeypatch.setattr("api.reader_pipeline.inspect_source", fake_inspect_source)
    monkeypatch.setattr("api.reader_pipeline.inspect_readability", fake_readability)
    monkeypatch.setattr("api.reader_pipeline.extract_source", extraction_must_not_run)

    with pytest.raises(HTTPException, match="not a readable document"):
        asyncio.run(read_source(inspected.url))
