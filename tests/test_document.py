import pytest
from pydantic import ValidationError

from api.document import DOCUMENT_SCHEMA, PaperDocument


def valid_document() -> dict:
    return {
        "schema": DOCUMENT_SCHEMA,
        "source": {
            "url": "https://example.org/book.pdf",
            "type": "pdf",
        },
        "metadata": {
            "title": "A Small Book",
            "author": "A. Writer",
            "language": "en",
        },
        "blocks": [
            {
                "id": "pdf-0001",
                "type": "heading",
                "text": "A Small Book",
                "locator": {
                    "type": "pdf_page",
                    "page": 1,
                    "bbox": [72.0, 120.0, 300.0, 150.0],
                },
            },
            {
                "id": "pdf-0002",
                "type": "paragraph",
                "text": "This exact text came from the source PDF.",
                "locator": {"type": "pdf_page", "page": 1},
            },
        ],
    }


def test_document_contract_serializes_a_source_grounded_pdf_in_order():
    document = PaperDocument.model_validate(valid_document())

    assert document.version == "paper.document.v1"
    assert [block.id for block in document.blocks] == ["pdf-0001", "pdf-0002"]
    assert document.blocks[1].text == "This exact text came from the source PDF."
    assert document.blocks[0].locator.page == 1
    assert document.model_dump(mode="json", by_alias=True, exclude_none=True) == valid_document()


def test_document_contract_accepts_html_nodes_as_the_other_supported_locator():
    payload = valid_document()
    payload["source"] = {
        "url": "https://www.gutenberg.org/cache/epub/1342/pg1342-images.html",
        "type": "html",
    }
    payload["blocks"] = [
        {
            "id": "html-0001",
            "type": "paragraph",
            "text": "It is a truth universally acknowledged.",
            "locator": {"type": "html_node", "selector": "#chapter-1 > p:nth-of-type(1)"},
        }
    ]

    document = PaperDocument.model_validate(payload)

    assert document.blocks[0].locator.type == "html_node"
    assert document.blocks[0].locator.selector == "#chapter-1 > p:nth-of-type(1)"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.update(schema="paper.document.v2"), "schema"),
        (lambda payload: payload["source"].update(url="file:///private/book.pdf"), "http"),
        (lambda payload: payload["metadata"].update(title="   "), "blank"),
        (lambda payload: payload["blocks"][0].update(text="   "), "blank"),
        (lambda payload: payload["blocks"][0].pop("locator"), "locator"),
        (lambda payload: payload["blocks"][0]["locator"].update(page="1"), "page"),
        (lambda payload: payload["blocks"].append(payload["blocks"][0].copy()), "unique"),
        (lambda payload: payload["blocks"][0].update(unexpected="nope"), "unexpected"),
    ],
)
def test_document_contract_rejects_invalid_or_ungrounded_blocks(mutate, message):
    payload = valid_document()
    mutate(payload)

    with pytest.raises(ValidationError, match=message):
        PaperDocument.model_validate(payload)


def test_pdf_locator_rejects_a_backwards_bounding_box():
    payload = valid_document()
    payload["blocks"][0]["locator"]["bbox"] = [300.0, 120.0, 72.0, 150.0]

    with pytest.raises(ValidationError, match="top-left"):
        PaperDocument.model_validate(payload)
