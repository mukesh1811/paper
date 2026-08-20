"""Run Paper's live source-inspection regression set and write an audit CSV.

The runner fetches each public URL through ``inspect_source``. Readability
cases then use the same bounded dossier, structured-output schema, and
grounding validation as the production inspection boundary. It stores hashes
and metrics only: never fetched source text or credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException

# Allow both ``python -m evals.run_inspect_eval`` and a direct script call.
PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from api.inspect_readability import (
    DEFAULT_INSPECTION_MODEL,
    INSPECTION_TEMPERATURE,
    MODEL_INSTRUCTIONS,
    OPENROUTER_CHAT_COMPLETIONS_URL,
    READABILITY_SCHEMA,
    READABILITY_PROVIDER_PREFERENCES,
    _openrouter_api_key_from_environment,
    _validate_decision,
    build_readability_dossier,
    deterministic_readability_decision,
)
from api.inspect_source import inspect_source


DEFAULT_MANIFEST_PATH = PROJECT_DIR / "evals" / "inspect_urls.json"
CSV_FIELDS = (
    "run_at_utc",
    "case_id",
    "url",
    "shape",
    "expected_stage",
    "expected_verdict",
    "expected_http_status",
    "actual_stage",
    "actual_verdict",
    "actual_http_status",
    "passed",
    "resolved_url",
    "source_type",
    "content_type",
    "source_sha256",
    "dossier_sha256",
    "decision_path",
    "evidence_ids",
    "requested_model",
    "returned_model",
    "provider",
    "latency_ms",
    "prompt_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "total_tokens",
    "cost_usd",
    "error",
)


def _sha256(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def _empty_row(case: dict[str, Any], run_at_utc: str, model: str) -> dict[str, Any]:
    expected = case["expected"]
    return {
        "run_at_utc": run_at_utc,
        "case_id": case["id"],
        "url": case["url"],
        "shape": ";".join(case["shape"]),
        "expected_stage": expected["stage"],
        "expected_verdict": expected["verdict"],
        "expected_http_status": expected.get("http_status", ""),
        "actual_stage": "",
        "actual_verdict": "",
        "actual_http_status": "",
        "passed": False,
        "resolved_url": "",
        "source_type": "",
        "content_type": "",
        "source_sha256": "",
        "dossier_sha256": "",
        "decision_path": "",
        "evidence_ids": "",
        "requested_model": model,
        "returned_model": "",
        "provider": "",
        "latency_ms": "",
        "prompt_tokens": "",
        "completion_tokens": "",
        "reasoning_tokens": "",
        "total_tokens": "",
        "cost_usd": "",
        "error": "",
    }


def _request_payload(model: str, input_text: str) -> dict[str, Any]:
    """Mirror the production OpenRouter request while retaining run metrics."""

    return {
        "model": model,
        "messages": [
            {"role": "system", "content": MODEL_INSTRUCTIONS},
            {"role": "user", "content": input_text},
        ],
        "temperature": INSPECTION_TEMPERATURE,
        "max_tokens": 128,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "paper_readability_decision",
                "strict": True,
                "schema": READABILITY_SCHEMA,
            },
        },
        "provider": READABILITY_PROVIDER_PREFERENCES,
    }


async def _model_decision(
    *,
    api_key: str,
    model: str,
    input_text: str,
) -> tuple[str, dict[str, Any], int]:
    started = time.perf_counter()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        trust_env=False,
    ) as client:
        response = await client.post(
            OPENROUTER_CHAT_COMPLETIONS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-Title": "Paper",
            },
            json=_request_payload(model, input_text),
        )
    latency_ms = round((time.perf_counter() - started) * 1000)
    if response.is_error:
        raise HTTPException(response.status_code, response.text)

    try:
        body = response.json()
        raw_decision = body["choices"][0]["message"]["content"]
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(502, "OpenRouter returned an invalid response.") from exc
    if not isinstance(raw_decision, str) or not raw_decision.strip():
        raise HTTPException(502, "OpenRouter returned no decision.")
    return raw_decision, body, latency_ms


def _usage_value(usage: dict[str, Any], key: str) -> Any:
    """Keep provider usage values losslessly serializable in the audit CSV."""

    value = usage.get(key, "")
    return "" if value is None else value


async def _run_case(
    case: dict[str, Any], *, run_at_utc: str, model: str, api_key: str
) -> dict[str, Any]:
    row = _empty_row(case, run_at_utc, model)
    expected = case["expected"]
    try:
        source = await inspect_source(case["url"])
    except HTTPException as exc:
        row.update(
            actual_stage="fetch",
            actual_verdict="reject",
            actual_http_status=exc.status_code,
            error=str(exc.detail),
        )
        row["passed"] = (
            expected["stage"] == "fetch"
            and expected["verdict"] == "reject"
            and expected.get("http_status") == exc.status_code
        )
        return row
    except Exception as exc:  # Keep the remaining live cases runnable after a transient failure.
        row.update(actual_stage="fetch", actual_verdict="error", error=f"{type(exc).__name__}: {exc}")
        return row

    row.update(
        resolved_url=source.url,
        source_type=source.type,
        content_type=source.content_type or "",
        source_sha256=_sha256(source.payload),
    )
    if expected["stage"] == "fetch":
        row.update(
            actual_stage="fetch",
            actual_verdict="accept",
            error="Expected fetch rejection, but the source was accepted.",
        )
        return row

    try:
        dossier = build_readability_dossier(source)
        model_input = dossier.model_input()
        row["dossier_sha256"] = _sha256(model_input)
        deterministic_decision = deterministic_readability_decision(source, dossier)
        if deterministic_decision is not None:
            row.update(
                actual_stage="readability",
                actual_verdict=deterministic_decision.verdict,
                decision_path="deterministic",
                evidence_ids=json.dumps(deterministic_decision.evidence_ids),
            )
            row["passed"] = deterministic_decision.verdict == expected["verdict"]
            return row
        raw_decision, response_body, latency_ms = await _model_decision(
            api_key=api_key,
            model=model,
            input_text=model_input,
        )
        decision = _validate_decision(raw_decision, dossier)
    except HTTPException as exc:
        row.update(
            actual_stage="readability",
            actual_verdict="error",
            actual_http_status=exc.status_code,
            error=str(exc.detail),
        )
        return row
    except Exception as exc:
        row.update(actual_stage="readability", actual_verdict="error", error=f"{type(exc).__name__}: {exc}")
        return row

    usage = response_body.get("usage") if isinstance(response_body.get("usage"), dict) else {}
    row.update(
        actual_stage="readability",
        actual_verdict=decision.verdict,
        decision_path="intelligence",
        evidence_ids=json.dumps(decision.evidence_ids),
        returned_model=response_body.get("model", ""),
        provider=response_body.get("provider", response_body.get("provider_name", "")),
        latency_ms=latency_ms,
        prompt_tokens=_usage_value(usage, "prompt_tokens"),
        completion_tokens=_usage_value(usage, "completion_tokens"),
        reasoning_tokens=_usage_value(usage, "reasoning_tokens"),
        total_tokens=_usage_value(usage, "total_tokens"),
        cost_usd=_usage_value(usage, "cost"),
    )
    row["passed"] = decision.verdict == expected["verdict"]
    return row


async def run(manifest_path: Path, output_path: Path, model: str) -> list[dict[str, Any]]:
    """Evaluate every manifest case sequentially and persist a compact audit trail."""

    api_key = _openrouter_api_key_from_environment()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_at_utc = datetime.now(UTC).isoformat()
    rows: list[dict[str, Any]] = []
    for position, case in enumerate(manifest["cases"], start=1):
        print(f"[{position}/{len(manifest['cases'])}] {case['id']}", flush=True)
        rows.append(await _run_case(case, run_at_utc=run_at_utc, model=model, api_key=api_key))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    # Loading the local key also loads the local model setting, if present.
    _openrouter_api_key_from_environment()
    model = args.model or os.getenv("PAPER_INSPECT_MODEL", DEFAULT_INSPECTION_MODEL)
    rows = asyncio.run(run(args.manifest, args.output, model))
    passed = sum(bool(row["passed"]) for row in rows)
    total_cost = sum(float(row["cost_usd"] or 0) for row in rows)
    print(f"{passed}/{len(rows)} passed; ${total_cost:.8f}; {args.output}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
