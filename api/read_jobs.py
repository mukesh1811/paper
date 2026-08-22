"""Short-lived, in-memory background jobs for unusually large reader sources.

The job keeps already-fetched source bytes in the active process only. This is
intentionally not a durable queue: a Cloud Run instance may stop at any time,
so a production scale-out deployment should replace this store with a durable
job backend before relying on jobs across instances.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException

from api.document import PaperDocument
from api.reader_pipeline import (
    PipelineStage,
    PreparedRead,
    complete_prepared_read,
    prepare_read,
    source_passport,
)

JobStatus = Literal["running", "complete", "failed"]
ReadStage = Literal[
    "fetching",
    "downloading",
    "checking",
    "extracting",
    "structuring",
    "validating",
    "complete",
    "failed",
]
JOB_TTL_SECONDS = 30 * 60
MAX_ACTIVE_JOBS = 32


@dataclass
class ReadJob:
    id: str
    created_at: float
    status: JobStatus = "running"
    stage: ReadStage = "fetching"
    passport: dict[str, object] | None = None
    document: PaperDocument | None = None
    error_status: int | None = None
    error_detail: str | None = None
    task: asyncio.Task[None] | None = None


class MemoryReadJobStore:
    """Run and expose short-lived read jobs within one active application process."""

    def __init__(self) -> None:
        self._jobs: dict[str, ReadJob] = {}

    async def submit(self, prepared: PreparedRead) -> ReadJob:
        """Continue an already-fetched source for the synchronous API path."""

        job = self._new_job(stage="structuring")
        job.passport = source_passport(prepared)
        job.task = asyncio.create_task(self._complete_prepared(job, prepared))
        return job

    async def submit_url(self, url: str) -> ReadJob:
        """Start a fully observable preparation job before source work begins."""

        job = self._new_job(stage="fetching")
        job.task = asyncio.create_task(self._complete_url(job, url))
        return job

    def _new_job(self, *, stage: ReadStage) -> ReadJob:
        self._discard_expired()
        if sum(job.status == "running" for job in self._jobs.values()) >= MAX_ACTIVE_JOBS:
            raise HTTPException(503, "Paper is already preparing too many long documents. Please try again shortly.")
        job = ReadJob(id=uuid.uuid4().hex, created_at=time.monotonic(), stage=stage)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> ReadJob:
        self._discard_expired()
        job = self._jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "That reading job was not found or has expired.")
        return job

    async def _complete_url(self, job: ReadJob, url: str) -> None:
        try:
            prepared = await prepare_read(
                url,
                progress=lambda stage, item, _detail: self._report(job, stage, item),
            )
            job.passport = source_passport(prepared)
            await self._complete_prepared(job, prepared)
        except HTTPException as exc:
            self._fail(job, exc.status_code, str(exc.detail))
        except Exception:
            self._fail(job, 502, "Paper could not prepare that document.")

    async def _complete_prepared(self, job: ReadJob, prepared: PreparedRead) -> None:
        try:
            job.document = await complete_prepared_read(
                prepared,
                progress=lambda stage, item, _detail: self._report(job, stage, item),
            )
            job.status = "complete"
            job.stage = "complete"
        except HTTPException as exc:
            self._fail(job, exc.status_code, str(exc.detail))
        except Exception:
            self._fail(job, 502, "Paper could not prepare that document.")

    def _report(self, job: ReadJob, stage: PipelineStage, prepared: PreparedRead | None) -> None:
        job.stage = stage
        if prepared is not None:
            job.passport = source_passport(prepared)

    @staticmethod
    def _fail(job: ReadJob, status: int, detail: str) -> None:
        job.status = "failed"
        job.stage = "failed"
        job.error_status = status
        job.error_detail = detail

    def _discard_expired(self) -> None:
        cutoff = time.monotonic() - JOB_TTL_SECONDS
        for job_id, job in tuple(self._jobs.items()):
            if job.created_at < cutoff:
                self._jobs.pop(job_id, None)


read_jobs = MemoryReadJobStore()
