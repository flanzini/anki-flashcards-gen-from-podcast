from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .deps import get_pipeline, reset_pipeline_cache
from .models import JobStatus, TranscriptRequest, TranscriptResponse
from .pipeline import TranscriptPipeline

LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    yield


app = FastAPI(
    title="ULP Transcript Service",
    description="Phone-triggered on-demand Ukrainian Lessons Podcast transcripts.",
    version="0.1.0",
    lifespan=lifespan,
)


class WorkerRunBody(BaseModel):
    job_id: str = Field(min_length=1)


def require_bearer(
    authorization: Optional[str] = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = settings.api_token.strip()
    if not expected:
        raise HTTPException(status_code=500, detail="TRANSCRIPT_API_TOKEN is not configured.")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    token = authorization.split(" ", 1)[1].strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid bearer token.")


def require_worker_bearer(
    authorization: Optional[str] = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = (settings.worker_token or settings.api_token).strip()
    if not expected:
        raise HTTPException(status_code=500, detail="Worker token is not configured.")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token.")
    token = authorization.split(" ", 1)[1].strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid bearer token.")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/v1/transcripts", response_model=TranscriptResponse)
def create_transcript(
    body: TranscriptRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(require_bearer),
    pipeline: TranscriptPipeline = Depends(get_pipeline),
    settings: Settings = Depends(get_settings),
):
    # Without Cloud Tasks, run the job after the response (local/dev).
    # Production Cloud Run should configure Cloud Tasks so work survives CPU throttling.
    if not settings.cloud_tasks_configured:
        pipeline.background_runner = lambda job_id: background_tasks.add_task(pipeline.run_job, job_id)
    else:
        pipeline.background_runner = None

    try:
        result = pipeline.request_transcript(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Failed to request transcript")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    payload = result.model_dump(mode="json")
    status_code = 202 if result.status == JobStatus.QUEUED else 200
    return JSONResponse(status_code=status_code, content=payload)


@app.get("/v1/jobs/{job_id}", response_model=TranscriptResponse)
def get_job(
    job_id: str,
    _: None = Depends(require_bearer),
    pipeline: TranscriptPipeline = Depends(get_pipeline),
) -> TranscriptResponse:
    job = pipeline.job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return TranscriptResponse(
        status=job.status,
        episode=job.episode_title,
        episode_key=job.episode_key,
        job_id=job.job_id,
        download_url=job.download_url,
        message=job.error or job.status.value,
    )


@app.post("/internal/workers/run")
def run_worker(
    body: WorkerRunBody,
    _: None = Depends(require_worker_bearer),
    pipeline: TranscriptPipeline = Depends(get_pipeline),
) -> dict:
    job = pipeline.run_job(body.job_id)
    return {"job_id": job.job_id, "status": job.status.value, "error": job.error}


def run_job_thread(pipeline: TranscriptPipeline, job_id: str) -> None:
    thread = threading.Thread(target=pipeline.run_job, args=(job_id,), daemon=True)
    thread.start()


# Test helper export
__all__ = ["app", "reset_pipeline_cache", "require_bearer"]
