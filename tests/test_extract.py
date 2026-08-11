import fitz
from api.app import extract_book


def make_pdf() -> bytes:
    doc = fitz.open()
    for n in range(3):
        page = doc.new_page()
        page.insert_text((72, 55), "A Small Book", fontsize=9)
        if n == 0:
            page.insert_text((72, 120), "A SMALL BOOK", fontsize=24)
            page.insert_text((72, 165), "Chapter One", fontsize=17)
        page.insert_textbox(
            fitz.Rect(72, 210, 520, 660),
            "This is a paragraph of readable prose. It exists so the extractor can turn a fixed PDF page into flowing text for a browser reader. " * 4,
            fontsize=12,
            lineheight=1.3,
        )
        page.insert_text((285, 790), str(n + 1), fontsize=9)
    data = doc.tobytes()
    doc.close()
    return data


def test_extract_book_filters_repeated_headers_and_page_numbers():
    result = extract_book(make_pdf())
    all_text = " ".join(x["text"] for x in result["blocks"])
    assert result["pages"] == 3
    assert result["word_count"] > 40
    assert "This is a paragraph" in all_text
    assert all_text.count("A Small Book") == 0
    assert not any(x["text"] in {"1", "2", "3"} for x in result["blocks"])
