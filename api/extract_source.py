"""Extract exact, locatable reader blocks from an inspected PDF or HTML source.

Extraction is deterministic.  It never asks a model to rewrite source text:
the later structure step may only select references to these blocks.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median
from typing import Literal

import pymupdf
from fastapi import HTTPException

from api.document import DocumentMetadata, HtmlNodeLocator, PdfPageLocator, SourceLocator
from api.html_source_analysis import HTMLSourceAnalysis, analyze_html_source
from api.inspect_source import InspectedSource

ReaderBlockType = Literal["heading", "paragraph", "quote", "list_item", "code"]


@dataclass(frozen=True)
class ExtractedBlock:
    """One exact source block ready for selection by the structure step."""

    id: str
    type: ReaderBlockType
    text: str
    locator: SourceLocator


@dataclass(frozen=True)
class ExtractedSource:
    """The complete deterministic extraction for one accepted source."""

    metadata: DocumentMetadata
    blocks: tuple[ExtractedBlock, ...]
    page_count: int | None = None


def extract_source(source: InspectedSource) -> ExtractedSource:
    """Extract a supported source without changing its text or storing it."""

    if source.type == "pdf":
        return _extract_pdf(source.payload)
    return _extract_html(source.html_analysis or analyze_html_source(source.payload))


def _extract_pdf(payload: bytes) -> ExtractedSource:
    try:
        document = pymupdf.open(stream=payload, filetype="pdf")
    except Exception as exc:
        raise HTTPException(422, "The PDF could not be parsed.") from exc

    try:
        if document.page_count == 0:
            raise HTTPException(422, "The PDF has no pages.")

        pages: list[list[dict[str, object]]] = []
        body_sizes: list[float] = []
        edge_candidates: list[str] = []

        for page_index in range(document.page_count):
            page = document[page_index]
            page_height = max(float(page.rect.height), 1.0)
            page_blocks: list[dict[str, object]] = []
            for block in page.get_text("dict").get("blocks", []):
                if "lines" not in block:
                    continue
                pieces: list[str] = []
                sizes: list[float] = []
                fonts: list[str] = []
                for line in block.get("lines", []):
                    pieces.append("".join(span.get("text", "") for span in line.get("spans", [])))
                    for span in line.get("spans", []):
                        span_text = span.get("text", "").strip()
                        if span_text:
                            sizes.append(float(span.get("size", 0) or 0))
                            fonts.append(str(span.get("font", "")))
                text = _clean_text("\n".join(pieces))
                if not text:
                    continue
                x0, y0, x1, y1 = (float(value) for value in block.get("bbox", (0, 0, 0, 0)))
                max_size = max(sizes) if sizes else 0.0
                item = {
                    "text": text,
                    "max_size": max_size,
                    "bold": any(
                        marker in font.lower()
                        for font in fonts
                        for marker in ("bold", "black", "semibold")
                    ),
                    "edge": y1 < page_height * 0.13 or y0 > page_height * 0.87,
                    "locator": PdfPageLocator(type="pdf_page", page=page_index + 1, bbox=[x0, y0, x1, y1]),
                }
                page_blocks.append(item)
                if item["edge"] and len(text) <= 180:
                    edge_candidates.append(_norm_repeat(text))
                elif 5 <= len(text) and sizes:
                    body_sizes.extend(size for size in sizes if size > 0)
            pages.append(page_blocks)

        repeated_edges = {
            text
            for text, count in Counter(edge_candidates).items()
            if text and count >= max(3, int(document.page_count * 0.25))
        }
        base_size = median(body_sizes) if body_sizes else 11.0
        blocks: list[ExtractedBlock] = []

        for page_blocks in pages:
            for item in page_blocks:
                text = str(item["text"])
                if item["edge"] and _norm_repeat(text) in repeated_edges:
                    continue
                if re.fullmatch(r"(?:page\s*)?\d+", text, flags=re.I):
                    continue
                short = len(text) <= 120
                kind: ReaderBlockType = "heading" if short and (
                    float(item["max_size"]) >= base_size * 1.24
                    or (bool(item["bold"]) and float(item["max_size"]) >= base_size * 1.05)
                    or (len(text) <= 70 and text.isupper() and len(text) > 3)
                ) else "paragraph"
                if blocks and blocks[-1].type == kind and blocks[-1].text == text:
                    continue
                blocks.append(
                    ExtractedBlock(
                        id=f"pdf-{len(blocks) + 1:05d}",
                        type=kind,
                        text=text,
                        locator=item["locator"],  # type: ignore[arg-type]
                    )
                )

        if _word_count(block.text for block in blocks) < 20:
            raise HTTPException(422, "I couldn't find enough selectable text. This MVP doesn't OCR scanned PDFs yet.")
        metadata = document.metadata or {}
        title = (metadata.get("title") or "").strip()
        if not title:
            title = next((block.text for block in blocks if block.type == "heading" and len(block.text) < 140), "Untitled PDF")
        return ExtractedSource(
            metadata=DocumentMetadata(
                title=title,
                author=_optional_text(metadata.get("author")),
                language=_optional_text(metadata.get("language")),
            ),
            blocks=tuple(blocks),
            page_count=document.page_count,
        )
    finally:
        document.close()


def _extract_html(analysis: HTMLSourceAnalysis) -> ExtractedSource:
    blocks: list[ExtractedBlock] = []
    for source_block in analysis.source_blocks:
        text = source_block.text.strip()
        if not text:
            continue
        selector = analysis.node_selectors.get(source_block.node_id)
        if not selector:
            raise HTTPException(422, "That HTML source could not be located for reading.")
        blocks.append(
            ExtractedBlock(
                id=source_block.id,
                type=_html_block_type(source_block.tag),
                text=text,
                locator=HtmlNodeLocator(type="html_node", selector=selector),
            )
        )
    if _word_count(block.text for block in blocks) < 20:
        raise HTTPException(422, "That HTML page has too little readable text for Paper.")
    title = analysis.title or next((block.text for block in blocks if block.type == "heading"), "Untitled page")
    return ExtractedSource(metadata=DocumentMetadata(title=title), blocks=tuple(blocks))


def _html_block_type(tag: str) -> ReaderBlockType:
    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return "heading"
    if tag == "blockquote":
        return "quote"
    if tag == "li":
        return "list_item"
    if tag == "pre":
        return "code"
    return "paragraph"


def _clean_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    result = lines[0]
    for line in lines[1:]:
        result = result[:-1] + line if result.endswith("-") and line[:1].islower() else f"{result} {line}"
    return re.sub(r"\s+", " ", result).strip()


def _norm_repeat(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", text).strip().lower())[:180]


def _word_count(parts: Iterable[str]) -> int:
    return sum(len(re.findall(r"\b\w+[’'\-]?\w*\b", text)) for text in parts)


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
