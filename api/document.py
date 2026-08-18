"""The versioned, source-grounded document contract returned by Paper.

This module intentionally contains no extraction or presentation logic.  It
describes the boundary between source readers and the Paper frontend:

* blocks remain in their supplied list order;
* every block carries its exact source-derived text; and
* every block has exactly one locator back into that source.

The later validation step can verify a locator against fetched source content.
This schema makes it impossible to return a block without declaring that
evidence.
"""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


class _StrictModel(BaseModel):
    """Reject accidental fields and type coercion at an API boundary."""

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class DocumentSource(_StrictModel):
    """The public source that the reader document was derived from."""

    url: StrictStr = Field(min_length=1, max_length=4096)
    type: Literal["pdf", "html"]

    @field_validator("url")
    @classmethod
    def public_http_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("must be an absolute http(s) URL")
        if parsed.username or parsed.password:
            raise ValueError("must not contain credentials")
        return value


class DocumentMetadata(_StrictModel):
    """Small, source-derived metadata used to identify a document."""

    title: StrictStr = Field(min_length=1, max_length=1000)
    author: StrictStr | None = Field(default=None, max_length=1000)
    language: StrictStr | None = Field(default=None, min_length=2, max_length=35)

    @field_validator("title", "author", "language")
    @classmethod
    def text_is_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank when provided")
        return value


class PdfPageLocator(_StrictModel):
    """A block found on a one-indexed PDF page."""

    type: Literal["pdf_page"]
    page: StrictInt = Field(ge=1)
    bbox: list[StrictFloat] | None = Field(default=None, min_length=4, max_length=4)

    @model_validator(mode="after")
    def valid_bbox(self) -> PdfPageLocator:
        if self.bbox is not None:
            x0, y0, x1, y1 = self.bbox
            if x1 < x0 or y1 < y0:
                raise ValueError("bbox must run from top-left to bottom-right")
        return self


class HtmlNodeLocator(_StrictModel):
    """A block found in one HTML node, addressed by a deterministic selector."""

    type: Literal["html_node"]
    selector: StrictStr = Field(min_length=1, max_length=4096)

    @field_validator("selector")
    @classmethod
    def selector_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


SourceLocator = Annotated[
    PdfPageLocator | HtmlNodeLocator,
    Field(discriminator="type"),
]


class DocumentBlock(_StrictModel):
    """One ordered, source-grounded unit that the frontend can render."""

    id: StrictStr = Field(min_length=1, max_length=200)
    type: Literal["heading", "paragraph", "quote", "list_item", "code"]
    text: StrictStr = Field(min_length=1, max_length=100_000)
    locator: SourceLocator

    @field_validator("id", "text")
    @classmethod
    def required_text_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class PaperDocument(_StrictModel):
    """`paper.document.v1`, the only document payload accepted by the reader."""

    version: Literal["paper.document.v1"] = Field(alias="schema", serialization_alias="schema")
    source: DocumentSource
    metadata: DocumentMetadata
    blocks: list[DocumentBlock] = Field(min_length=1)

    @model_validator(mode="after")
    def block_ids_are_unique(self) -> PaperDocument:
        ids = [block.id for block in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("block ids must be unique")
        return self


DOCUMENT_SCHEMA = "paper.document.v1"
