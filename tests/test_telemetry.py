import json

from api.telemetry import emit_read_event, local_telemetry_events


def test_telemetry_is_a_single_structured_cloud_logging_and_local_event(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("PAPER_TELEMETRY_FILE", str(tmp_path / "events.jsonl"))
    emit_read_event(
        "read_prepared",
        read_id="read-123",
        source_url="https://www.gutenberg.org/cache/epub/1342/pg1342-images.html",
        stage="complete",
        elapsed_ms=812,
        source_type="html",
        title="Pride and Prejudice",
        block_count=928,
    )

    event = json.loads(capsys.readouterr().out)

    assert event | {"observed_at": "timestamp"} == {
        "schema": "paper.telemetry.v1",
        "observed_at": "timestamp",
        "event": "read_prepared",
        "read_id": "read-123",
        "source_url": "https://www.gutenberg.org/cache/epub/1342/pg1342-images.html",
        "source_host": "www.gutenberg.org",
        "stage": "complete",
        "elapsed_ms": 812,
        "source_type": "html",
        "title": "Pride and Prejudice",
        "block_count": 928,
    }
    assert local_telemetry_events() == [event]


import pytest

from api.telemetry import (
    TELEMETRY_EVENTS_PER_WINDOW,
    allow_telemetry_event,
    read_failure_reason,
)


@pytest.mark.parametrize(
    "status_code, detail, reason",
    [
        (422, "I couldn't find enough selectable text. This MVP doesn't OCR scanned PDFs yet.", "pdf_no_text_layer"),
        (422, "That URL is not a readable document for Paper.", "model_rejected_source"),
        (422, "That HTML page has too little readable text for Paper.", "html_too_little_text"),
        (413, "That source is larger than the 30 MB MVP limit.", "source_too_large"),
        (502, "The source host returned HTTP 403.", "source_host_error"),
        (502, "Paper's structure model returned a backwards range.", "plan_backwards_range"),
        (502, "Paper's structure model referenced a block that was not supplied.", "plan_unsupplied_block"),
        (503, "Paper's structure model is busy. Please try again.", "model_rate_limited"),
        (503, "Paper's readability model has no available API credit.", "model_no_credit"),
        (400, "Private or local network URLs are not allowed.", "url_not_public"),
        (500, "Something nobody has written a mapping for", "unmapped_500"),
    ],
)
def test_every_reader_facing_failure_groups_under_one_reason(status_code, detail, reason):
    """The log needs to say which failure is common without parsing sentences."""

    assert read_failure_reason(status_code, detail) == reason


def test_a_caller_is_rate_limited_only_after_a_reading_session_worth_of_events():
    """An ordinary session stays well under the limit; a flood does not."""

    caller = "203.0.113.7"
    accepted = sum(allow_telemetry_event(caller, now=1000.0) for _ in range(TELEMETRY_EVENTS_PER_WINDOW + 25))

    assert accepted == TELEMETRY_EVENTS_PER_WINDOW
    # A caller that waits out the window is served again.
    assert allow_telemetry_event(caller, now=1000.0 + 61) is True
    assert allow_telemetry_event("198.51.100.4", now=1000.0) is True
