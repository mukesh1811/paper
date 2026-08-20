import asyncio

from api.document import DOCUMENT_SCHEMA, PaperDocument
from api.extract_source import extract_source
from api.inspect_source import InspectedSource
from api.read_jobs import MemoryReadJobStore
from api.reader_pipeline import PreparedRead


def prepared_read():
    source = InspectedSource(
        url="https://example.org/essay",
        type="html",
        content_type="text/html",
        payload=(
            b"<html><body><p>A complete public source has enough exact words to prepare a reader document in a test and verify the in memory background job contract safely today.</p></body></html>"
        ),
    )
    return PreparedRead(source=source, extracted=extract_source(source))


def document():
    return PaperDocument.model_validate(
        {
            "schema": DOCUMENT_SCHEMA,
            "source": {"url": "https://example.org/essay", "type": "html"},
            "metadata": {"title": "Exact Essay"},
            "blocks": [
                {
                    "id": "b1",
                    "type": "paragraph",
                    "text": "A complete public source has enough exact words to prepare a reader document in a test and verify the in memory background job contract safely today.",
                    "locator": {"type": "html_node", "selector": "html:nth-of-type(1)"},
                }
            ],
        }
    )


def test_background_job_keeps_the_document_in_memory_until_polled(monkeypatch):
    async def fake_complete(_prepared):
        return document()

    monkeypatch.setattr("api.read_jobs.complete_prepared_read", fake_complete)
    store = MemoryReadJobStore()

    async def run():
        job = await store.submit(prepared_read())
        assert store.get(job.id).status == "running"
        await job.task
        return store.get(job.id)

    job = asyncio.run(run())

    assert job.status == "complete"
    assert job.document is not None
    assert job.document.metadata.title == "Exact Essay"


def test_background_job_exposes_a_safe_failure(monkeypatch):
    async def fake_complete(_prepared):
        raise RuntimeError("provider detail must not leak")

    monkeypatch.setattr("api.read_jobs.complete_prepared_read", fake_complete)
    store = MemoryReadJobStore()

    async def run():
        job = await store.submit(prepared_read())
        await job.task
        return store.get(job.id)

    job = asyncio.run(run())

    assert job.status == "failed"
    assert job.error_status == 502
    assert job.error_detail == "Paper could not prepare that document."
