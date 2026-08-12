from fastapi.testclient import TestClient

from api.app import app


client = TestClient(app)


def test_home_keeps_the_prefilled_reader_form():
    response = client.get("/")

    assert response.status_code == 200
    assert 'id="url-form"' in response.text
    assert "The%20Two%20Old%20Men.pdf" in response.text


def test_read_entry_page_is_a_noindex_reader_surface():
    response = client.get("/read")

    assert response.status_code == 200
    assert 'meta name="robots" content="noindex,follow"' in response.text
    assert 'aria-label="Open a public PDF"' in response.text


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
