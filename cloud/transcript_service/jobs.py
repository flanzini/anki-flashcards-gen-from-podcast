from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import urllib.request

from .config import Settings
from .models import JobRecord, JobStatus
from .storage import TranscriptStorage

LOGGER = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_job_id() -> str:
    return uuid.uuid4().hex


class JobStore:
    def __init__(self, storage: TranscriptStorage) -> None:
        self.storage = storage

    def save(self, job: JobRecord) -> JobRecord:
        job.updated_at = utc_now()
        if not job.created_at:
            job.created_at = job.updated_at
        self.storage.write_job(job.job_id, job.model_dump())
        return job

    def get(self, job_id: str) -> Optional[JobRecord]:
        payload = self.storage.read_job(job_id)
        if payload is None:
            return None
        return JobRecord.model_validate(payload)


def enqueue_worker(settings: Settings, job_id: str) -> None:
    """Enqueue a Cloud Task, or no-op when not configured (caller runs inline/background)."""
    if not settings.cloud_tasks_configured:
        LOGGER.info("Cloud Tasks not configured; caller should run job %s locally/background", job_id)
        return

    from google.cloud import tasks_v2  # type: ignore
    from google.protobuf import timestamp_pb2  # type: ignore

    del timestamp_pb2  # imported for environments that pin protobuf helpers
    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(
        settings.cloud_tasks_project,
        settings.cloud_tasks_location,
        settings.cloud_tasks_queue,
    )
    worker_token = settings.worker_token or settings.api_token
    url = settings.worker_url.rstrip("/") + "/internal/workers/run"
    body = json.dumps({"job_id": job_id}).encode("utf-8")
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": url,
            "headers": {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {worker_token}",
            },
            "body": body,
        }
    }
    if settings.gcp_service_account_email:
        task["http_request"]["oidc_token"] = {
            "service_account_email": settings.gcp_service_account_email,
        }
    client.create_task(request={"parent": parent, "task": task})
    LOGGER.info("Enqueued Cloud Task for job %s", job_id)


def post_worker_inline(settings: Settings, job_id: str) -> None:
    """HTTP-call the worker URL from this process (useful without Cloud Tasks)."""
    if not settings.worker_url:
        raise RuntimeError("TRANSCRIPT_WORKER_URL is required for remote worker dispatch.")
    worker_token = settings.worker_token or settings.api_token
    url = settings.worker_url.rstrip("/") + "/internal/workers/run"
    request = urllib.request.Request(
        url,
        data=json.dumps({"job_id": job_id}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {worker_token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()
