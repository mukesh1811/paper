import asyncio
import json

import pytest
from fastapi import HTTPException

from api.document import DocumentBlock
from api.extract_source import extract_source
from api.inspect_source import InspectedSource
from api.structure_document import (
    BlockRange,
    StructurePlan,
    materialize_document,
    structure_document,
    structure_input,
    validate_document_provenance,
    validate_structure_plan,
)


def extracted_source():
    source = InspectedSource(
        url="https://example.org/essay",
        type="html",
        content_type="text/html",
        payload=(
            b"<html><body><nav>Ignore this menu</nav><main><h1>Exact Essay</h1>"
            b"<p>First exact paragraph has enough words to be a readable part of this public work.</p>"
            b"<p>Second exact paragraph completes the coherent written work without inventing any text.</p>"
            b"</main><footer>Ignore this footer</footer></body></html>"
        ),
    )
    return source, extract_source(source)


class FakeStructureModel:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create_plan(self, *, model, input_text):
        self.calls.append({"model": model, "input_text": input_text})
        response = self.response(input_text) if callable(self.response) else self.response
        return json.dumps(response)


def test_structure_input_contains_complete_exact_source_blocks():
    _, extracted = extracted_source()

    payload = json.loads(structure_input(extracted))

    assert payload["blocks"][1][2].startswith("Exact Essay")
    assert "Second exact paragraph completes" in payload["blocks"][3][2]


def test_structure_model_can_only_select_supplied_ordered_ranges():
    _, extracted = extracted_source()
    model = FakeStructureModel({"ranges": [{"start_id": "b2", "end_id": "b4"}]})

    plan = asyncio.run(structure_document(extracted, client=model))

    assert plan == StructurePlan(ranges=(BlockRange(start_id="b2", end_id="b4"),))
    assert model.calls[0]["model"]
    assert "First exact paragraph" in model.calls[0]["input_text"]


@pytest.mark.parametrize(
    "response, message",
    [
        ({"ranges": [{"start_id": "missing", "end_id": "b2"}]}, "not supplied"),
        ({"ranges": [{"start_id": "b4", "end_id": "b2"}]}, "backwards"),
        ({"ranges": [], "text": "invented"}, "invalid plan"),
    ],
)
def test_structure_plan_rejects_untrusted_or_invalid_references(response, message):
    _, extracted = extracted_source()

    with pytest.raises(HTTPException, match=message):
        validate_structure_plan(json.dumps(response), extracted)


@pytest.mark.parametrize(
    "ranges, expected",
    [
        # Overlapping spans point at one run of source, so they become one.
        ([("b2", "b3"), ("b3", "b4")], [("b2", "b4")]),
        # Listed out of order, the same spans still select the same source.
        ([("b3", "b4"), ("b2", "b2")], [("b2", "b4")]),
        # Adjacent single-block spans are one run split up, not several.
        ([("b2", "b2"), ("b3", "b3"), ("b4", "b4")], [("b2", "b4")]),
        # A genuine gap in the source must survive as a gap.
        ([("b1", "b1"), ("b3", "b4")], [("b1", "b1"), ("b3", "b4")]),
        # An exact repeat selects that span once.
        ([("b2", "b4"), ("b2", "b4")], [("b2", "b4")]),
    ],
)
def test_structure_plan_puts_selected_spans_back_in_source_order(ranges, expected):
    """A model slip in listing spans must not cost the whole document.

    Long sources are planned one chunk at a time, so a single out-of-order or
    duplicated span used to discard a book that was otherwise fully grounded.
    """

    _, extracted = extracted_source()
    response = {"ranges": [{"start_id": start, "end_id": end} for start, end in ranges]}

    plan = validate_structure_plan(json.dumps(response), extracted)

    assert plan == StructurePlan(
        ranges=tuple(BlockRange(start_id=start, end_id=end) for start, end in expected)
    )


def test_materialized_document_is_exactly_grounded_in_selected_source_blocks():
    source, extracted = extracted_source()
    plan = StructurePlan(ranges=(BlockRange(start_id="b2", end_id="b4"),))

    document = materialize_document(source, extracted, plan)

    assert [block.id for block in document.blocks] == ["b2", "b3", "b4"]
    assert "Ignore this menu" not in " ".join(block.text for block in document.blocks)
    assert document.blocks[0].locator.type == "html_node"
    assert document.model_dump(mode="json", by_alias=True)["schema"] == "paper.document.v1"

    tampered = document.model_copy(
        update={
            "blocks": [
                DocumentBlock(
                    id="b2",
                    type="paragraph",
                    text="Invented source text",
                    locator=document.blocks[0].locator,
                )
            ]
        }
    )
    with pytest.raises(HTTPException, match="does not match"):
        validate_document_provenance(tampered, extracted)


def test_long_sources_are_chunked_only_between_exact_blocks(monkeypatch):
    _, extracted = extracted_source()
    def select_all_in_chunk(input_text):
        blocks = json.loads(input_text)["blocks"]
        return {"ranges": [{"start_id": blocks[0][0], "end_id": blocks[-1][0]}]}

    model = FakeStructureModel(select_all_in_chunk)
    monkeypatch.setattr("api.structure_document.STRUCTURE_CHUNK_CHARACTERS", 45)

    plan = asyncio.run(structure_document(extracted, client=model))

    assert len(model.calls) > 1
    ids = [block.id for block in extracted.blocks]
    selected = []
    for block_range in plan.ranges:
        selected.extend(ids[ids.index(block_range.start_id) : ids.index(block_range.end_id) + 1])
    assert selected == ids


def test_chunk_can_be_empty_but_a_complete_source_cannot(monkeypatch):
    _, extracted = extracted_source()
    monkeypatch.setattr("api.structure_document.STRUCTURE_CHUNK_CHARACTERS", 45)
    model = FakeStructureModel({"ranges": []})

    with pytest.raises(HTTPException, match="readable document body"):
        asyncio.run(structure_document(extracted, client=model))


class FlakyStructureModel:
    """Fail the first `failures` calls the way a provider outage does."""

    def __init__(self, response, *, failures, status=502):
        self.response = response
        self.failures = failures
        self.status = status
        self.calls = 0

    async def create_plan(self, *, model, input_text):
        self.calls += 1
        if self.calls <= self.failures:
            raise HTTPException(self.status, "Paper's structure model could not prepare that source.")
        return json.dumps(self.response)


def test_a_provider_failure_on_one_chunk_is_retried(monkeypatch):
    """One bad provider reply must not cost a source Paper already fetched."""

    _, extracted = extracted_source()
    monkeypatch.setattr("api.structure_document.STRUCTURE_CHUNK_RETRY_DELAYS_SECONDS", (0, 0))
    model = FlakyStructureModel({"ranges": [{"start_id": "b2", "end_id": "b4"}]}, failures=2)

    plan = asyncio.run(structure_document(extracted, client=model))

    assert model.calls == 3
    assert plan == StructurePlan(ranges=(BlockRange(start_id="b2", end_id="b4"),))


def test_a_source_paper_cannot_read_is_not_retried(monkeypatch):
    """Only provider-side failures are worth repeating."""

    _, extracted = extracted_source()
    monkeypatch.setattr("api.structure_document.STRUCTURE_CHUNK_RETRY_DELAYS_SECONDS", (0, 0))
    model = FlakyStructureModel({"ranges": []}, failures=1, status=503)

    with pytest.raises(HTTPException):
        asyncio.run(structure_document(extracted, client=model))

    assert model.calls == 1


def test_chunks_are_planned_together_and_kept_in_source_order(monkeypatch):
    """Chunk plans run concurrently, but the reader still follows the source."""

    _, extracted = extracted_source()
    monkeypatch.setattr("api.structure_document.STRUCTURE_CHUNK_CHARACTERS", 45)

    started = 0
    peak = 0

    class ConcurrentModel:
        def __init__(self):
            self.calls = []

        async def create_plan(self, *, model, input_text):
            nonlocal started, peak
            started += 1
            peak = max(peak, started)
            await asyncio.sleep(0)
            started -= 1
            self.calls.append(input_text)
            blocks = json.loads(input_text)["blocks"]
            return json.dumps({"ranges": [{"start_id": blocks[0][0], "end_id": blocks[-1][0]}]})

    model = ConcurrentModel()
    plan = asyncio.run(structure_document(extracted, client=model))

    assert len(model.calls) > 1
    assert peak > 1, "independent chunks must not wait on each other"
    ids = [block.id for block in extracted.blocks]
    selected = []
    for block_range in plan.ranges:
        selected.extend(ids[ids.index(block_range.start_id) : ids.index(block_range.end_id) + 1])
    assert selected == ids, "concurrent planning must still yield source order"
