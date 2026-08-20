import pymupdf

from api.extract_source import extract_source
from api.inspect_source import InspectedSource


def readable_pdf() -> bytes:
    document = pymupdf.open()
    for page_number in range(3):
        page = document.new_page()
        page.insert_text((72, 55), "A Small Book", fontsize=9)
        if page_number == 0:
            page.insert_text((72, 120), "A SMALL BOOK", fontsize=24)
            page.insert_text((72, 165), "Chapter One", fontsize=17)
        page.insert_textbox(
            pymupdf.Rect(72, 210, 520, 660),
            "This is a paragraph of readable prose. It exists so the extractor can retain exact source text and its page location for the reader. " * 3,
            fontsize=12,
            lineheight=1.3,
        )
        page.insert_text((285, 790), str(page_number + 1), fontsize=9)
    payload = document.tobytes()
    document.close()
    return payload


def test_pdf_extraction_keeps_exact_text_and_page_provenance():
    extracted = extract_source(
        InspectedSource(
            url="https://example.org/book.pdf",
            type="pdf",
            content_type="application/pdf",
            payload=readable_pdf(),
        )
    )

    text = " ".join(block.text for block in extracted.blocks)
    assert extracted.metadata.title == "A SMALL BOOK"
    assert extracted.page_count == 3
    assert "This is a paragraph" in text
    assert "A Small Book" not in text
    assert not any(block.text in {"1", "2", "3"} for block in extracted.blocks)
    assert extracted.blocks[0].locator.type == "pdf_page"
    assert extracted.blocks[0].locator.page == 1


def test_html_extraction_preserves_exact_blocks_types_and_selectors():
    source = InspectedSource(
        url="https://example.org/essay",
        type="html",
        content_type="text/html",
        payload=(
            b"<html><head><title>Exact Essay</title></head><body><main>"
            b"<h1>Exact Essay</h1><p>First exact paragraph has enough words to be a readable part of this public work.</p>"
            b"<blockquote>Quoted exact source text stays exactly where the public author placed it.</blockquote>"
            b"<ul><li>One exact list item belongs to the document body and keeps its provenance.</li></ul>"
            b"<pre>print('exact source code stays intact')</pre>"
            b"</main></body></html>"
        ),
    )

    extracted = extract_source(source)

    assert extracted.metadata.title == "Exact Essay"
    assert [block.type for block in extracted.blocks] == ["heading", "paragraph", "quote", "list_item", "code"]
    assert extracted.blocks[1].text.startswith("First exact paragraph")
    assert extracted.blocks[2].locator.type == "html_node"
    assert extracted.blocks[2].locator.selector.startswith("html:nth-of-type(1) >")
