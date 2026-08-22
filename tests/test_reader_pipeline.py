import asyncio

import pytest
from fastapi import HTTPException

from api.extract_source import extract_source
from api.inspect_readability import ReadabilityDecision
from api.inspect_source import InspectedSource
from api.reader_pipeline import prepare_read, read_source
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

    async def fake_inspect_source(url, **_kwargs):
        assert url == inspected.url
        return inspected

    async def fake_readability(_source):
        return ReadabilityDecision(verdict="accept", evidence_ids=("b1",))

    async def fake_structure(_extracted, **_kwargs):
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

    async def fake_inspect_source(_url, **_kwargs):
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


def test_each_stage_is_reported_when_its_own_work_starts(monkeypatch):
    """Stages must track the work in flight, not the work already finished.

    Reporting "checking" only after `inspect_source` returned put the download
    *and* the format identification inside the fetching stage, so the reader
    looked stuck on the first step for the whole wait.
    """

    inspected = source()
    stages: list[str] = []

    async def fake_inspect_source(_url, *, on_fetched=None, **_kwargs):
        assert stages == [], "downloading the source belongs to the fetching stage"
        on_fetched()
        return inspected

    async def fake_readability(_source):
        assert stages == ["checking"], "judging the source belongs to the checking stage"
        return ReadabilityDecision(verdict="accept", evidence_ids=("b1",))

    monkeypatch.setattr("api.reader_pipeline.inspect_source", fake_inspect_source)
    monkeypatch.setattr("api.reader_pipeline.inspect_readability", fake_readability)

    prepared = asyncio.run(
        prepare_read(inspected.url, progress=lambda stage, _prepared, _detail=None: stages.append(stage))
    )

    assert stages == ["checking", "extracting"]
    assert prepared.extracted.blocks


def test_transferring_the_source_is_its_own_measured_stage(monkeypatch):
    """Locating a source and transferring it are different waits.

    Billing the download to "fetching" made the first step appear stuck for the
    whole transfer, which on a large PDF is most of the preparation time.
    """

    inspected = source()
    events: list[tuple[str, dict | None]] = []

    async def fake_inspect_source(_url, *, on_download=None, on_fetched=None, **_kwargs):
        on_download(0, 8192)
        on_download(4096, 8192)
        on_fetched()
        return inspected

    async def fake_readability(_source):
        return ReadabilityDecision(verdict="accept", evidence_ids=("b1",))

    monkeypatch.setattr("api.reader_pipeline.inspect_source", fake_inspect_source)
    monkeypatch.setattr("api.reader_pipeline.inspect_readability", fake_readability)

    asyncio.run(
        prepare_read(
            inspected.url,
            progress=lambda stage, _prepared, detail=None: events.append((stage, detail)),
        )
    )

    assert [stage for stage, _detail in events] == [
        "downloading",
        "downloading",
        "checking",
        "extracting",
    ]
    assert events[0][1] == {"received": 0, "total": 8192}
    assert events[1][1] == {"received": 4096, "total": 8192}
