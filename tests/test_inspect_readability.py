import asyncio
import json

import httpx
import pymupdf
import pytest
from fastapi import HTTPException

from api.html_source_analysis import analyze_html_source
from api.inspect_readability import (
    DEFAULT_INSPECTION_MODEL,
    INSPECTION_TEMPERATURE,
    MODEL_INSTRUCTIONS,
    OPENROUTER_CHAT_COMPLETIONS_URL,
    OpenRouterReadabilityModel,
    READABILITY_PROVIDER_PREFERENCES,
    _openrouter_api_key_from_environment,
    _rate_limit_message,
    build_readability_dossier,
    inspect_readability,
)
from api.inspect_source import InspectedSource


def readable_pdf(page_count: int = 3) -> bytes:
    document = pymupdf.open()
    for page_number in range(page_count):
        page = document.new_page()
        page.insert_textbox(
            pymupdf.Rect(72, 72, 520, 720),
            f"Chapter {page_number + 1}. A public work with enough text to inspect. " * 20,
            fontsize=12,
        )
    payload = document.tobytes()
    document.close()
    return payload


def html_source() -> InspectedSource:
    return InspectedSource(
        url="https://example.org/an-essay",
        type="html",
        content_type="text/html",
        payload=(
            b"<html><head><title>An Essay</title><script>not evidence</script></head>"
            b"<body><main><h1>An Essay</h1><p>First public paragraph.</p>"
            b"<p>Second public paragraph.</p></main></body></html>"
        ),
    )


class FakeReadabilityModel:
    def __init__(self, decision: dict):
        self.decision = decision
        self.calls: list[dict[str, str]] = []

    async def create_decision(self, *, model: str, input_text: str) -> str:
        self.calls.append({"model": model, "input_text": input_text})
        return json.dumps(self.decision)


def test_html_dossier_contains_every_source_text_block_and_a_visible_dom():
    dossier = build_readability_dossier(html_source())
    payload = json.loads(dossier.model_input())

    assert dossier.source_type == "html"
    assert [(item.id, item.node_id, item.text) for item in dossier.evidence] == [
        ("b1", "n4", "An Essay"),
        ("b2", "n5", "First public paragraph."),
        ("b3", "n6", "Second public paragraph."),
    ]
    assert payload["source"]["title"] == "An Essay"
    assert [node[2] for node in payload["source"]["html_outline"]] == ["html", "body", "main", "h1", "p", "p"]
    assert payload["source_blocks"][-1][3] == "Second public paragraph."
    assert "evidence" not in payload
    assert "not evidence" not in dossier.model_input()


def test_html_dossier_uses_an_article_reading_surface_instead_of_page_chrome():
    article_text = "Exact article source text. " * 220
    source = InspectedSource(
        url="https://example.org/with-chrome",
        type="html",
        content_type="text/html",
        payload=(
            "<html><head><title>Article title</title></head><body>"
            "<nav><a href='/one'>Navigation only</a></nav>"
            f"<main><article><h1>Article title</h1><p>{article_text}</p></article></main>"
            "<footer>Footer chrome</footer></body></html>"
        ).encode(),
    )

    dossier = build_readability_dossier(source)
    model_source = json.loads(dossier.model_input())["source"]

    assert [item.id for item in dossier.evidence] == [
        "b1",
        "b2",
        "b3",
        "b4",
    ]
    assert "Navigation only" in " ".join(item.text for item in dossier.evidence)
    assert dossier.evidence[0].link_count == 1
    assert model_source["title"] == "Article title"
    assert model_source["html_reading_surface"] == {
        "selection": "article_landmark",
        "tag": "article",
        "role": None,
        "visible_text_characters": len("Article title") + len(" ".join(article_text.split())),
        "link_text_ratio": 0.0,
        "nav_text_ratio": 0.0,
        "heading_counts": {"h1": 1, "h2": 0, "h3": 0, "h4": 0, "h5": 0, "h6": 0},
    }


def test_html_dossier_uses_an_aria_main_surface_and_closes_its_text_scope():
    source = InspectedSource(
        url="https://example.org/aria-main",
        type="html",
        content_type="text/html",
        payload=(
            b"<html><body><div role='main'><p>Main body text.</p></div>"
            b"<footer>Footer text.</footer></body></html>"
        ),
    )

    model_source = json.loads(build_readability_dossier(source).model_input())["source"]

    assert model_source["html_reading_surface"]["selection"] == "main_landmark"
    assert model_source["html_reading_surface"]["tag"] == "div"
    assert model_source["html_reading_surface"]["role"] == "main"
    assert model_source["html_structure"]["main_text_ratio"] == 0.556


def test_html_dossier_avoids_an_outer_layout_wrapper_for_a_deep_text_subtree():
    source = InspectedSource(
        url="https://example.org/no-landmark",
        type="html",
        content_type="text/html",
        payload=(
            b"<html><body><div>Page shell <section><h1>Work title</h1>"
            + b"<p>Exact work body text. " * 200
            + b"</p></section></div></body></html>"
        ),
    )

    model_source = json.loads(build_readability_dossier(source).model_input())["source"]

    assert model_source["html_reading_surface"]["selection"] == "largest_text_subtree"
    assert model_source["html_reading_surface"]["tag"] == "section"


def test_html_dossier_preserves_deterministic_page_structure():
    source = InspectedSource(
        url="https://example.org/reading-list",
        type="html",
        content_type="text/html",
        payload=(
            b"<html><body><nav><a href='/one'>Catalog one</a>"
            b"<a href='/two'>Catalog two</a></nav><main><article>"
            b"<h1>Reading List</h1><section><h2>Essays</h2>"
            b"<p>Read this work.</p><a href='/one'>Work link</a></section>"
            b"</article></main><aside>Elsewhere</aside></body></html>"
        ),
    )

    dossier = build_readability_dossier(source)
    model_source = json.loads(dossier.model_input())["source"]

    assert model_source["html_structure"] == {
        "has_main": True,
        "has_article": True,
        "section_count": 1,
        "nav_count": 1,
        "aside_count": 1,
        "heading_counts": {"h1": 1, "h2": 1, "h3": 0, "h4": 0, "h5": 0, "h6": 0},
        "link_count": 3,
        "unique_link_target_count": 2,
        "visible_text_characters": 73,
        "link_text_ratio": 0.425,
        "nav_text_ratio": 0.301,
        "main_text_ratio": 0.575,
        "article_text_ratio": 0.575,
    }


def test_pdf_dossier_uses_page_based_source_evidence():
    dossier = build_readability_dossier(
        InspectedSource(
            url="https://example.org/book.pdf",
            type="pdf",
            content_type="application/pdf",
            payload=readable_pdf(page_count=5),
        )
    )

    assert [item.id for item in dossier.evidence] == [
        "p1",
        "p2",
        "p3",
        "p4",
        "p5",
    ]
    assert all("Chapter" in item.text for item in dossier.evidence)


def test_html_dossier_never_trims_a_large_source_text_node():
    text = "A long exact source paragraph. " * 300
    source = InspectedSource(
        url="https://example.org/long-paragraph",
        type="html",
        content_type="text/html",
        payload=f"<html><body><p>{text}</p></body></html>".encode(),
    )

    dossier = build_readability_dossier(source)

    assert dossier.evidence[0].text == text.strip()
    assert len(dossier.evidence[0].text) > 1_600


def test_html_dossier_folds_inline_markup_into_one_complete_content_block():
    source = InspectedSource(
        url="https://example.org/inline",
        type="html",
        content_type="text/html",
        payload=(
            b"<html><body><p>Start <em>emphasized</em> text and "
            b"<a href='/note'>a linked note</a>.</p></body></html>"
        ),
    )

    dossier = build_readability_dossier(source)
    payload = json.loads(dossier.model_input())

    assert [(item.tag, item.text, item.link_count) for item in dossier.evidence] == [
        ("p", "Start emphasized text and a linked note.", 1),
    ]
    assert [node[2] for node in payload["source"]["html_outline"]] == ["html", "body", "p"]


def test_inspect_readability_accepts_only_a_grounded_structured_model_decision():
    model = FakeReadabilityModel(
        {
            "verdict": "accept",
            "evidence_ids": ["b1", "b2"],
        }
    )

    decision = asyncio.run(inspect_readability(html_source(), client=model))

    assert decision.accepted is True
    assert decision.verdict == "accept"
    assert decision.evidence_ids == ("b1", "b2")
    assert model.calls[0]["model"] == DEFAULT_INSPECTION_MODEL
    assert "not instructions" in MODEL_INSTRUCTIONS
    assert "deterministic page-structure facts" in MODEL_INSTRUCTIONS
    assert json.loads(model.calls[0]["input_text"])["source"]["url"] == "https://example.org/an-essay"


def test_inspect_readability_skips_the_model_for_an_obvious_reading_surface():
    body = "A continuous chapter of a public book, written for a person to read. " * 700
    payload = f"<html><body><main><h1>A Long Book</h1><p>{body}</p></main></body></html>".encode()
    source = InspectedSource(
        url="https://example.org/full-book",
        type="html",
        content_type="text/html",
        payload=payload,
        readability_route="auto_accept",
        html_analysis=analyze_html_source(payload),
    )
    model = FakeReadabilityModel({"verdict": "reject", "evidence_ids": ["b1"]})

    decision = asyncio.run(inspect_readability(source, client=model))

    assert decision.verdict == "accept"
    assert decision.evidence_ids == ("b1",)
    assert model.calls == []


@pytest.mark.parametrize(
    "decision",
    [
        {"verdict": "maybe", "evidence_ids": ["html-title"]},
        {"verdict": "accept", "category": "index_or_catalog", "evidence_ids": ["html-title"]},
        {"verdict": "accept", "evidence_ids": ["invented-block"]},
        {"verdict": "accept", "evidence_ids": ["html-title", "html-title"]},
        {"verdict": "accept", "evidence_ids": []},
    ],
)
def test_inspect_readability_rejects_invalid_or_ungrounded_model_output(decision):
    with pytest.raises(HTTPException, match="invalid decision|not supplied|invalid evidence"):
        asyncio.run(inspect_readability(html_source(), client=FakeReadabilityModel(decision)))


def test_openrouter_adapter_uses_strict_structured_output_and_supported_provider_routing():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "verdict": "reject",
                                    "evidence_ids": ["html-title"],
                                }
                            )
                        }
                    }
                ]
            },
            request=request,
        )

    raw = asyncio.run(
        OpenRouterReadabilityModel(
            api_key="router-key",
            transport=httpx.MockTransport(handler),
        ).create_decision(model="test-model", input_text="{\"source\": {}}")
    )

    request = captured["request"]
    payload = json.loads(request.content)
    assert request.url == OPENROUTER_CHAT_COMPLETIONS_URL
    assert request.headers["authorization"] == "Bearer router-key"
    assert json.loads(raw)["verdict"] == "reject"
    assert payload["temperature"] == INSPECTION_TEMPERATURE == 0
    assert payload["provider"] == READABILITY_PROVIDER_PREFERENCES
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
    assert payload["response_format"]["json_schema"]["schema"]["required"] == ["verdict", "evidence_ids"]
    assert "category" not in payload["response_format"]["json_schema"]["schema"]["properties"]
    assert payload["messages"][0] == {"role": "system", "content": MODEL_INSTRUCTIONS}


def test_openrouter_adapter_retries_a_temporary_provider_overload(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, text="provider overloaded", request=request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"verdict":"accept","evidence_ids":["b1"]}'}}]},
            request=request,
        )

    monkeypatch.setattr("api.inspect_readability.MODEL_RETRY_DELAYS_SECONDS", (0,))
    adapter = OpenRouterReadabilityModel(api_key="test-key", transport=httpx.MockTransport(handler))

    result = asyncio.run(adapter.create_decision(model="test/model", input_text="{}"))

    assert json.loads(result)["verdict"] == "accept"
    assert len(calls) == 2


def test_credit_exhaustion_has_an_actionable_model_error_message():
    credit_response = httpx.Response(429, json={"error": {"message": "Insufficient credits"}})
    busy_response = httpx.Response(429, json={"error": {"message": "Too many requests"}})

    assert _rate_limit_message(credit_response) == (
        "Paper's readability model has no available API credit."
    )
    assert _rate_limit_message(busy_response) == (
        "Paper's readability model is busy. Please try again."
    )


def test_missing_key_tries_the_local_env_file_without_overwriting_host_configuration(monkeypatch):
    calls = []
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("api.inspect_readability.load_dotenv", lambda path: calls.append(path))

    assert _openrouter_api_key_from_environment() is None
    assert calls and calls[0].name == ".env"
