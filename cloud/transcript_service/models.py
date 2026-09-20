from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


class TranscriptRequest(BaseModel):
    episode_index: Optional[int] = Field(default=None, ge=0)
    title_search: str = ""
    email_to: Optional[str] = None
    force: bool = False

    @model_validator(mode="after")
    def require_selector(self) -> "TranscriptRequest":
        if self.episode_index is None and not self.title_search.strip():
            self.episode_index = 0
        return self


class JobRecord(BaseModel):
    job_id: str
    status: JobStatus
    episode_key: str = ""
    episode_title: str = ""
    audio_url: str = ""
    source: str = ""
    email_to: str = ""
    transcript_uri: str = ""
    download_url: str = ""
    error: str = ""
    created_at: str = ""
    updated_at: str = ""


class TranscriptResponse(BaseModel):
    status: JobStatus
    episode: str = ""
    episode_key: str = ""
    job_id: str = ""
    download_url: str = ""
    message: str = ""
