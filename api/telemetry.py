"""Small, first-party telemetry events for Paper's reading flow.

Cloud Run captures valid JSON written to stdout as structured Cloud Logging
entries. Keeping this at the API boundary gives Paper useful product signals
without a browser analytics SDK or a server-side document library.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

TelemetryEvent = Literal[
    "read_attempted",
    "read_prepared",
    "read_rejected",
    "read_failed",
    "read_abandoned",
    "reader_opened",
    "reader_cache_opened",
    "reading_progress",
]
TELEMETRY_FILE_ENV = "PAPER_TELEMETRY_FILE"
_write_lock = Lock()

# One reader sends a handful of events per document. This ceiling leaves an
# ordinary reading session far below the limit while stopping a flood from
# filling Cloud Logging with events Paper never observed.
TELEMETRY_EVENTS_PER_WINDOW = 60
TELEMETRY_WINDOW_SECONDS = 60.0
TELEMETRY_TRACKED_CALLERS = 2048

_recent_calls: dict[str, deque[float]] = {}
_rate_lock = Lock()

# Every failure the reader can hit already carries a sentence written for the
# person waiting. These codes carry the same failures for the log, where the
# useful question is which one is happening often, not how it was phrased.
# Ordered: the first matching fragment wins, so narrower entries come first.
_FAILURE_REASONS: tuple[tuple[str, str], ...] = (
    ("no readable selectable text", "pdf_no_text_layer"),
    ("doesn't OCR scanned PDFs", "pdf_no_text_layer"),
    ("has no text for readability", "pdf_no_text_layer"),
    ("could not be prepared for readability", "pdf_unreadable"),
    ("The PDF could not be parsed", "pdf_unreadable"),
    ("The PDF has no pages", "pdf_unreadable"),
    ("not a readable document for Paper", "model_rejected_source"),
    ("could not find a readable document body", "structure_found_no_body"),
    ("too little readable text", "html_too_little_text"),
    ("no inspectable public text", "html_too_little_text"),
    ("could not be located for reading", "html_body_not_found"),
    ("larger than the 30 MB", "source_too_large"),
    ("did not return a supported readable", "unsupported_content_type"),
    ("did not return a PDF", "unsupported_content_type"),
    ("Private or local network", "url_not_public"),
    ("containing credentials", "url_not_public"),
    ("Could not resolve", "host_unresolvable"),
    ("public http(s) URL", "url_invalid"),
    ("Invalid URL", "url_invalid"),
    ("The source host returned HTTP", "source_host_error"),
    ("invalid redirect", "source_host_error"),
    ("Too many redirects", "source_host_error"),
    ("Could not fetch that source", "source_unreachable"),
    ("has no available API credit", "model_no_credit"),
    ("is busy", "model_rate_limited"),
    ("is not configured", "model_misconfigured"),
    ("is unavailable", "model_unavailable"),
    ("referenced a block that was not supplied", "plan_unsupplied_block"),
    ("returned a backwards range", "plan_backwards_range"),
    ("cited evidence that was not supplied", "plan_unsupplied_block"),
    ("returned invalid JSON", "plan_invalid_json"),
    ("returned invalid evidence", "plan_invalid"),
    ("returned an invalid plan", "plan_invalid"),
    ("returned an invalid decision", "plan_invalid"),
    ("returned no plan", "plan_empty"),
    ("returned no decision", "plan_empty"),
    ("could not prepare that source (provider HTTP", "provider_error"),
    ("could not inspect that source", "provider_error"),
    ("without source evidence", "grounding_failed"),
    ("does not match its source evidence", "grounding_failed"),
)


def read_failure_reason(status_code: int, detail: object) -> str:
    """Name why one read ended, so failures group in the log without parsing prose."""

    if not isinstance(detail, str):
        return "unknown"
    for fragment, reason in _FAILURE_REASONS:
        if fragment in detail:
            return reason
    return f"unmapped_{status_code}"


def allow_telemetry_event(caller: str, *, now: float | None = None) -> bool:
    """Rate-limit one caller's browser-reported events.

    The reporting endpoints are public and unauthenticated, so anything they
    accept can be sent by anyone. This keeps a flood from drowning out the
    events Paper actually observed. It is per instance, which is the right
    scale for a guard rail rather than a quota.
    """

    stamp = time.monotonic() if now is None else now
    with _rate_lock:
        calls = _recent_calls.setdefault(caller, deque())
        while calls and stamp - calls[0] > TELEMETRY_WINDOW_SECONDS:
            calls.popleft()
        if not calls and len(_recent_calls) > TELEMETRY_TRACKED_CALLERS:
            # Drop callers that have gone quiet before tracking a new one.
            for key in [key for key, seen in _recent_calls.items() if not seen]:
                del _recent_calls[key]
            _recent_calls[caller] = calls
        if len(calls) >= TELEMETRY_EVENTS_PER_WINDOW:
            return False
        calls.append(stamp)
        return True


def new_read_id() -> str:
    """Create the id that ties one preparation attempt to its later open."""

    return str(uuid4())


def local_telemetry_file() -> Path | None:
    """Return the opt-in local event file, if this process has one."""

    configured = os.getenv(TELEMETRY_FILE_ENV, "").strip()
    return Path(configured) if configured else None


def local_telemetry_events(limit: int = 100) -> list[dict[str, object]] | None:
    """Read the most recent local events for the development-only viewer."""

    path = local_telemetry_file()
    if path is None:
        return None
    if not path.is_file():
        return []

    events: deque[dict[str, object]] = deque(maxlen=limit)
    try:
        with path.open(encoding="utf-8") as event_file:
            for line in event_file:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("schema") == "paper.telemetry.v1":
                    events.append(event)
    except OSError:
        return []
    return list(reversed(events))


def emit_read_event(
    event: TelemetryEvent,
    *,
    read_id: str | None,
    source_url: str,
    device_id: str | None = None,
    stage: str | None = None,
    elapsed_ms: int | None = None,
    **details: object,
) -> None:
    """Write one queryable event to stdout for Cloud Logging.

    Paper deliberately records the public source URL: compatibility work needs
    to show exactly which public documents people try to read. It does not add
    an account id, IP address, document payload, or reader text to the event.

    ``device_id`` is a random value the browser makes for itself and keeps
    beside its reading copies. It answers whether a read came from a browser
    Paper has served before, which is the only form of "returning reader"
    available without accounts. It identifies storage, not a person.
    """

    payload: dict[str, object] = {
        "schema": "paper.telemetry.v1",
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
        "source_url": source_url,
        "source_host": urlparse(source_url).hostname or "",
    }
    if read_id is not None:
        payload["read_id"] = read_id
    if device_id is not None:
        payload["device_id"] = device_id
    if stage is not None:
        payload["stage"] = stage
    if elapsed_ms is not None:
        payload["elapsed_ms"] = elapsed_ms
    payload.update(details)

    # Cloud Run promotes a single JSON object written to stdout into a
    # structured log entry. Do not send this through Uvicorn's formatted logger
    # or the JSON would become an opaque string in Cloud Logging.
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write(line + "\n")
    sys.stdout.flush()

    # ``local_run.bat`` opts into this file. Cloud Run leaves it unset and
    # continues to receive only the structured stdout event above.
    path = local_telemetry_file()
    if path is None:
        return
    try:
        with _write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as event_file:
                event_file.write(line + "\n")
    except OSError:
        # Local diagnostics must never make the reader fail.
        return
