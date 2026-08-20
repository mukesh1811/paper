from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from api.app import app
from api.document import DOCUMENT_SCHEMA, PaperDocument


client = TestClient(app)


def test_unbuilt_frontend_uses_its_own_api_origin_locally():
    script = (Path(__file__).resolve().parents[1] / "site" / "app.js").read_text(encoding="utf-8")

    assert "configured === '__PAPER_API_URL__'" in script
    assert "fetch(`${apiBaseUrl()}/api/read?url=${encodeURIComponent(activeUrl)}`)" in script


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


def test_api_read_returns_the_versioned_reader_document(monkeypatch):
    async def fake_prepare_read(url: str):
        assert url == "https://example.org/essay"
        return SimpleNamespace(extracted=SimpleNamespace())

    async def fake_complete_prepared_read(_prepared) -> PaperDocument:
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


def test_api_can_return_a_pollable_background_job(monkeypatch):
    async def fake_prepare_read(_url: str):
        return SimpleNamespace(extracted=SimpleNamespace())

    async def fake_submit(_prepared):
        return SimpleNamespace(id="job-123", status="running")

    monkeypatch.setattr("api.app.prepare_read", fake_prepare_read)
    monkeypatch.setattr("api.app.extracted_character_count", lambda _extracted: 500_000)
    monkeypatch.setattr("api.app.read_jobs.submit", fake_submit)

    response = client.get("/api/read", params={"url": "https://example.org/long", "background": "true"})

    assert response.status_code == 202
    assert response.json() == {"job_id": "job-123", "status": "running", "status_url": "/api/read/jobs/job-123"}
