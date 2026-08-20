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
from api.reader_pipeline import PreparedRead, complete_prepared_read

JobStatus = Literal["running", "complete", "failed"]
JOB_TTL_SECONDS = 30 * 60
MAX_ACTIVE_JOBS = 32


@dataclass
class ReadJob:
    id: str
    created_at: float
    status: JobStatus = "running"
    document: PaperDocument | None = None
    error_status: int | None = None
    error_detail: str | None = None
    task: asyncio.Task[None] | None = None


class MemoryReadJobStore:
    """Run and expose short-lived read jobs within one active application process."""

    def __init__(self) -> None:
        self._jobs: dict[str, ReadJob] = {}

    async def submit(self, prepared: PreparedRead) -> ReadJob:
        self._discard_expired()
        if sum(job.status == "running" for job in self._jobs.values()) >= MAX_ACTIVE_JOBS:
            raise HTTPException(503, "Paper is already preparing too many long documents. Please try again shortly.")
        job = ReadJob(id=uuid.uuid4().hex, created_at=time.monotonic())
        self._jobs[job.id] = job
        job.task = asyncio.create_task(self._complete(job, prepared))
        return job

    def get(self, job_id: str) -> ReadJob:
        self._discard_expired()
        job = self._jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "That reading job was not found or has expired.")
        return job

    async def _complete(self, job: ReadJob, prepared: PreparedRead) -> None:
        try:
            job.document = await complete_prepared_read(prepared)
            job.status = "complete"
        except HTTPException as exc:
            job.status = "failed"
            job.error_status = exc.status_code
            job.error_detail = str(exc.detail)
        except Exception:
            job.status = "failed"
            job.error_status = 502
            job.error_detail = "Paper could not prepare that document."

    def _discard_expired(self) -> None:
        cutoff = time.monotonic() - JOB_TTL_SECONDS
        for job_id, job in tuple(self._jobs.items()):
            if job.created_at < cutoff:
                self._jobs.pop(job_id, None)


read_jobs = MemoryReadJobStore()
