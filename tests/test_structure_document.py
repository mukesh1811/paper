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
        ({"ranges": [{"start_id": "b4", "end_id": "b2"}]}, "out-of-order"),
        ({"ranges": [{"start_id": "b2", "end_id": "b3"}, {"start_id": "b3", "end_id": "b4"}]}, "overlapping"),
        ({"ranges": [], "text": "invented"}, "invalid plan"),
    ],
)
def test_structure_plan_rejects_untrusted_or_invalid_references(response, message):
    _, extracted = extracted_source()

    with pytest.raises(HTTPException, match=message):
        validate_structure_plan(json.dumps(response), extracted)


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
