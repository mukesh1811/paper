from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.app import app
from api.document import DOCUMENT_SCHEMA, PaperDocument
from api.extract_source import extract_source
from api.inspect_source import InspectedSource
from api.reader_pipeline import PreparedRead
from api.structure_document import StructureStats
from api.telemetry import TELEMETRY_EVENTS_PER_WINDOW, emit_read_event


client = TestClient(app)


def test_unbuilt_frontend_uses_its_own_api_origin_locally():
    script = (Path(__file__).resolve().parents[1] / "site" / "app.js").read_text(encoding="utf-8")

    assert "configured === '__PAPER_API_URL__'" in script
    assert "fetch(`${apiBaseUrl()}/api/read/events?${query}`" in script
    assert "headers: { Accept: 'text/event-stream' }" in script
    assert "window.location.assign(readerEntryUrl(url, 'pasted'))" in script
    assert "postTelemetry('reader-opened'" in script
    assert "postTelemetry('reading-progress'" in script
    assert "reportReaderOpened(documentData, readId, false, origin)" in script
    assert "reportReaderOpened(saved.document, saved.readId, true, origin)" in script


def test_home_keeps_the_prefilled_reader_form():
    response = client.get("/")

    assert response.status_code == 200
    assert 'id="url-form"' in response.text
    assert "The%20Two%20Old%20Men.pdf" in response.text


def test_read_entry_page_is_a_noindex_reader_surface():
    response = client.get("/read")

    assert response.status_code == 200
    assert 'meta name="robots" content="noindex,follow"' in response.text
    assert 'aria-label="Open a public document"' in response.text
    assert 'id="preparation"' in response.text
    assert 'id="passport-facts"' in response.text


def test_reader_entry_page_is_not_in_the_seo_sitemap():
    response = client.get("/sitemap.xml")

    assert response.status_code == 200
    assert ">http://localhost:8000/read</loc>" not in response.text


def test_github_pages_origin_is_allowed_for_api_requests():
    response = client.options(
        "/api/read",
        headers={
            "Origin": "https://mukesh1811.github.io",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.headers["access-control-allow-origin"] == "https://mukesh1811.github.io"


def test_github_pages_origin_can_report_a_reader_open():
    response = client.options(
        "/api/telemetry/reader-opened",
        headers={
            "Origin": "https://mukesh1811.github.io",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.headers["access-control-allow-origin"] == "https://mukesh1811.github.io"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_reader_open_is_recorded_only_after_the_browser_renders(monkeypatch):
    events = []
    monkeypatch.setattr("api.app.emit_read_event", lambda event, **details: events.append((event, details)))

    response = client.post(
        "/api/telemetry/reader-opened",
        headers={"Origin": "https://mukesh1811.github.io"},
        json={
            "source_url": "https://example.org/public-book?edition=first",
            "read_id": "03c3623e-62a4-41e4-8b99-aa165541aa5f",
            "device_id": "b1d38e1a-6f2a-4a4e-9d1e-2c0b9f5a77c4",
            "cache_hit": True,
            "origin": "link",
        },
    )

    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "https://mukesh1811.github.io"
    assert events == [
        (
            "reader_cache_opened",
            {
                "read_id": "03c3623e-62a4-41e4-8b99-aa165541aa5f",
                "source_url": "https://example.org/public-book?edition=first",
                "device_id": "b1d38e1a-6f2a-4a4e-9d1e-2c0b9f5a77c4",
                "origin": "link",
                "repeat": False,
            },
        )
    ]


def test_local_telemetry_view_exposes_the_saved_event_file(monkeypatch, tmp_path):
    telemetry_file = tmp_path / "telemetry" / "events.jsonl"
    monkeypatch.setenv("PAPER_TELEMETRY_FILE", str(telemetry_file))
    emit_read_event(
        "read_attempted",
        read_id="read-123",
        source_url="https://example.org/public-book",
        stage="fetching",
    )

    page = client.get("/telemetry")
    feed = client.get("/api/telemetry/events")

    assert page.status_code == 200
    assert 'id="telemetry-events"' in page.text
    assert feed.status_code == 200
    assert feed.json()["events"][0]["source_url"] == "https://example.org/public-book"


def test_api_read_returns_the_versioned_reader_document(monkeypatch):
    async def fake_prepare_read(url: str):
        assert url == "https://example.org/essay"
        return SimpleNamespace(extracted=SimpleNamespace())

    async def fake_complete_prepared_read(_prepared, **_kwargs) -> PaperDocument:
        return PaperDocument.model_validate(
            {
                "schema": DOCUMENT_SCHEMA,
                "source": {"url": "https://example.org/essay", "type": "html"},
                "metadata": {"title": "Exact Essay"},
                "blocks": [
                    {
                        "id": "b1",
                        "type": "paragraph",
                        "text": "Exact source text.",
                        "locator": {"type": "html_node", "selector": "html:nth-of-type(1)"},
                    }
                ],
            }
        )

    monkeypatch.setattr("api.app.prepare_read", fake_prepare_read)
    monkeypatch.setattr("api.app.complete_prepared_read", fake_complete_prepared_read)
    monkeypatch.setattr("api.app.extracted_character_count", lambda _extracted: 0)
    response = client.get("/api/read", params={"url": "https://example.org/essay"})

    assert response.status_code == 200
    assert response.json()["schema"] == DOCUMENT_SCHEMA
    assert response.json()["metadata"]["title"] == "Exact Essay"


def test_api_preparation_stream_reports_real_stages_and_a_source_passport(monkeypatch):
    source = InspectedSource(
        url="https://example.org/essay",
        type="html",
        content_type="text/html",
        payload=(
            b"<html><head><title>Exact Essay</title></head><body><main>"
            b"<h1>Exact Essay</h1><p>An exact source paragraph has enough words for Paper to safely render a real preparation stream in this test.</p>"
            b"</main></body></html>"
        ),
    )
    prepared = PreparedRead(source=source, extracted=extract_source(source))
    document = PaperDocument.model_validate(
        {
            "schema": DOCUMENT_SCHEMA,
            "source": {"url": source.url, "type": "html"},
            "metadata": {"title": "Exact Essay"},
            "blocks": [
                {
                    "id": "b1",
                    "type": "heading",
                    "text": "Exact Essay",
                    "locator": {"type": "html_node", "selector": "html:nth-of-type(1)"},
                }
            ],
        }
    )

    async def fake_prepare(url: str, *, progress):
        assert url == source.url
        progress("checking", None, None)
        progress("extracting", None, None)
        return prepared

    async def fake_complete(current: PreparedRead, *, progress, **_kwargs):
        progress("structuring", current, None)
        progress("validating", current, None)
        return document

    events = []
    monkeypatch.setattr("api.app.prepare_read", fake_prepare)
    monkeypatch.setattr("api.app.complete_prepared_read", fake_complete)
    monkeypatch.setattr("api.app.emit_read_event", lambda event, **details: events.append((event, details)))

    response = client.get("/api/read/events", params={"url": source.url})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"stage": "fetching"' in response.text
    assert '"stage": "checking"' in response.text
    assert '"stage": "structuring"' in response.text
    assert '"title": "Exact Essay"' in response.text
    assert "event: complete" in response.text
    assert '"read_id":' in response.text
    assert [event for event, _details in events] == ["read_attempted", "read_prepared"]
    assert events[0][1]["source_url"] == source.url
    assert events[1][1]["source_url"] == source.url
    assert events[1][1]["source_type"] == "html"


def test_api_can_return_a_pollable_background_job(monkeypatch):
    async def fake_submit_url(url: str):
        assert url == "https://example.org/long"
        return SimpleNamespace(id="job-123", status="running", stage="fetching")

    monkeypatch.setattr("api.app.read_jobs.submit_url", fake_submit_url)

    response = client.get("/api/read", params={"url": "https://example.org/long", "background": "true"})

    assert response.status_code == 202
    assert response.json() == {
        "job_id": "job-123",
        "status": "running",
        "stage": "fetching",
        "status_url": "/api/read/jobs/job-123",
    }


def test_api_job_status_includes_only_source_derived_passport_fields(monkeypatch):
    fake_job = SimpleNamespace(
        status="running",
        stage="structuring",
        passport={"title": "Exact Essay", "source_type": "html"},
        document=None,
        error_detail=None,
    )
    monkeypatch.setattr("api.app.read_jobs.get", lambda _job_id: fake_job)

    response = client.get("/api/read/jobs/job-123")

    assert response.status_code == 200
    assert response.json() == {
        "status": "running",
        "stage": "structuring",
        "passport": {"title": "Exact Essay", "source_type": "html"},
    }


def _essay_source_and_document():
    source = InspectedSource(
        url="https://example.org/essay",
        type="html",
        content_type="text/html",
        payload=(
            b"<html><head><title>Exact Essay</title></head><body><main><h1>Exact Essay</h1>"
            b"<p>An exact source paragraph has enough words for Paper to safely render a real"
            b" preparation stream in this test without inventing any reader text at all.</p>"
            b"</main></body></html>"
        ),
    )
    prepared = PreparedRead(source=source, extracted=extract_source(source))
    document = PaperDocument.model_validate(
        {
            "schema": DOCUMENT_SCHEMA,
            "source": {"url": source.url, "type": "html"},
            "metadata": {"title": "Exact Essay"},
            "blocks": [
                {
                    "id": "b1",
                    "type": "heading",
                    "text": "Exact Essay",
                    "locator": {"type": "html_node", "selector": "html:nth-of-type(1)"},
                }
            ],
        }
    )
    return source, prepared, document


def test_a_finished_read_records_where_its_time_and_its_model_calls_went(monkeypatch):
    """A total alone cannot tell a slow download from a slow model."""

    source, prepared, document = _essay_source_and_document()

    async def fake_prepare(url: str, *, progress):
        progress("downloading", None, {"received": 10, "total": 100})
        progress("downloading", None, {"received": 90, "total": 100})
        progress("checking", None, None)
        progress("extracting", None, None)
        return prepared

    async def fake_complete(current: PreparedRead, *, progress, on_structure_stats=None):
        progress("structuring", current, None)
        if on_structure_stats is not None:
            on_structure_stats(
                StructureStats(chunk_count=13, retry_count=2, prompt_characters=400, completion_tokens=64)
            )
        progress("validating", current, None)
        return document

    events = []
    monkeypatch.setattr("api.app.prepare_read", fake_prepare)
    monkeypatch.setattr("api.app.complete_prepared_read", fake_complete)
    monkeypatch.setattr("api.app.emit_read_event", lambda event, **details: events.append((event, details)))

    response = client.get(
        "/api/read/events",
        params={
            "url": source.url,
            "device": "b1d38e1a-6f2a-4a4e-9d1e-2c0b9f5a77c4",
            "origin": "sample",
        },
    )

    assert response.status_code == 200
    attempted, prepared_event = dict(events)["read_attempted"], dict(events)["read_prepared"]
    assert attempted["device_id"] == "b1d38e1a-6f2a-4a4e-9d1e-2c0b9f5a77c4"
    assert attempted["origin"] == "sample"
    # Every stage that ran is measured on its own, and repeated download
    # reports stay one stage rather than restarting the clock.
    assert set(prepared_event["stage_ms"]) == {
        "fetching",
        "downloading",
        "checking",
        "extracting",
        "structuring",
        "validating",
    }
    assert all(isinstance(value, int) for value in prepared_event["stage_ms"].values())
    assert prepared_event["chunk_count"] == 13
    assert prepared_event["retry_count"] == 2
    assert prepared_event["completion_tokens"] == 64
    assert prepared_event["tokens_measured"] is False
    assert prepared_event["source_bytes"] == len(source.payload)
    assert prepared_event["origin"] == "sample"


def test_a_rejected_read_records_the_reason_and_the_stage_it_reached(monkeypatch):
    """Which failure is common matters more than how it was worded."""

    source, _prepared, _document = _essay_source_and_document()

    async def fake_prepare(url: str, *, progress):
        progress("checking", None, None)
        raise HTTPException(422, "I couldn't find enough selectable text. This MVP doesn't OCR scanned PDFs yet.")

    events = []
    monkeypatch.setattr("api.app.prepare_read", fake_prepare)
    monkeypatch.setattr("api.app.emit_read_event", lambda event, **details: events.append((event, details)))

    response = client.get("/api/read/events", params={"url": source.url})

    assert response.status_code == 200
    rejected = dict(events)["read_rejected"]
    assert rejected["reason"] == "pdf_no_text_layer"
    assert rejected["stage"] == "checking"
    assert set(rejected["stage_ms"]) == {"fetching", "checking"}


def test_a_device_id_that_is_not_a_uuid_is_dropped_rather_than_logged(monkeypatch):
    """Anyone can send anything here, and the log has to stay queryable."""

    source, _prepared, _document = _essay_source_and_document()

    async def fake_prepare(url: str, *, progress):
        raise HTTPException(422, "That URL is not a readable document for Paper.")

    events = []
    monkeypatch.setattr("api.app.prepare_read", fake_prepare)
    monkeypatch.setattr("api.app.emit_read_event", lambda event, **details: events.append((event, details)))

    client.get("/api/read/events", params={"url": source.url, "device": "../../etc/passwd"})

    assert dict(events)["read_attempted"]["device_id"] is None


def test_reading_progress_is_recorded_so_opened_can_be_told_from_read(monkeypatch):
    events = []
    monkeypatch.setattr("api.app.emit_read_event", lambda event, **details: events.append((event, details)))

    response = client.post(
        "/api/telemetry/reading-progress",
        headers={"Origin": "https://mukesh1811.github.io"},
        json={
            "source_url": "https://example.org/public-book",
            "device_id": "b1d38e1a-6f2a-4a4e-9d1e-2c0b9f5a77c4",
            "percent": 75,
            "final": True,
        },
    )

    assert response.status_code == 204
    assert events == [
        (
            "reading_progress",
            {
                "read_id": None,
                "source_url": "https://example.org/public-book",
                "device_id": "b1d38e1a-6f2a-4a4e-9d1e-2c0b9f5a77c4",
                "percent": 75,
                "final": True,
            },
        )
    ]


def test_a_flood_of_browser_events_is_dropped_rather_than_logged(monkeypatch):
    """The reporting endpoints are public, so anything they accept anyone can send."""

    events = []
    monkeypatch.setattr("api.app.emit_read_event", lambda event, **details: events.append((event, details)))
    monkeypatch.setattr("api.app._telemetry_caller", lambda _request: "flooding-caller")

    for _ in range(TELEMETRY_EVENTS_PER_WINDOW + 20):
        response = client.post(
            "/api/telemetry/reading-progress",
            json={"source_url": "https://example.org/public-book", "percent": 10},
        )
        # A dropped event is still a successful request: the browser has
        # nothing useful to do about it.
        assert response.status_code == 204

    assert len(events) == TELEMETRY_EVENTS_PER_WINDOW
