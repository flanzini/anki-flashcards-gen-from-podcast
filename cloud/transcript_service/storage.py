from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Optional, Protocol
from urllib.parse import quote

LOGGER = logging.getLogger(__name__)


class TranscriptStorage(Protocol):
    def transcript_exists(self, episode_key: str) -> bool: ...

    def read_transcript(self, episode_key: str) -> str: ...

    def write_transcript(self, episode_key: str, text: str) -> str: ...

    def write_audio(self, episode_key: str, data: bytes, *, content_type: str = "audio/mpeg") -> str: ...

    def audio_uri(self, episode_key: str) -> str: ...

    def signed_transcript_url(self, episode_key: str, *, ttl_seconds: int) -> str: ...

    def write_job(self, job_id: str, payload: dict) -> None: ...

    def read_job(self, job_id: str) -> Optional[dict]: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.transcripts = root / "transcripts"
        self.audio = root / "audio"
        self.jobs = root / "jobs"
        self.transcripts.mkdir(parents=True, exist_ok=True)
        self.audio.mkdir(parents=True, exist_ok=True)
        self.jobs.mkdir(parents=True, exist_ok=True)

    def _transcript_path(self, episode_key: str) -> Path:
        return self.transcripts / f"{episode_key}.txt"

    def _audio_path(self, episode_key: str) -> Path:
        return self.audio / f"{episode_key}.mp3"

    def transcript_exists(self, episode_key: str) -> bool:
        return self._transcript_path(episode_key).exists()

    def read_transcript(self, episode_key: str) -> str:
        return self._transcript_path(episode_key).read_text(encoding="utf-8")

    def write_transcript(self, episode_key: str, text: str) -> str:
        path = self._transcript_path(episode_key)
        path.write_text(text, encoding="utf-8")
        return str(path.resolve())

    def write_audio(self, episode_key: str, data: bytes, *, content_type: str = "audio/mpeg") -> str:
        del content_type
        path = self._audio_path(episode_key)
        path.write_bytes(data)
        return str(path.resolve())

    def audio_uri(self, episode_key: str) -> str:
        return self._audio_path(episode_key).resolve().as_uri()

    def signed_transcript_url(self, episode_key: str, *, ttl_seconds: int) -> str:
        del ttl_seconds
        path = self._transcript_path(episode_key).resolve()
        return path.as_uri()

    def write_job(self, job_id: str, payload: dict) -> None:
        path = self.jobs / f"{job_id}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def read_job(self, job_id: str) -> Optional[dict]:
        path = self.jobs / f"{job_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


class GcsStorage:
    def __init__(self, bucket_name: str) -> None:
        from google.cloud import storage  # type: ignore

        self.bucket_name = bucket_name
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    def _transcript_blob(self, episode_key: str):
        return self.bucket.blob(f"transcripts/{episode_key}.txt")

    def _audio_blob(self, episode_key: str):
        return self.bucket.blob(f"audio/{episode_key}.mp3")

    def _job_blob(self, job_id: str):
        return self.bucket.blob(f"jobs/{job_id}.json")

    def transcript_exists(self, episode_key: str) -> bool:
        return self._transcript_blob(episode_key).exists()

    def read_transcript(self, episode_key: str) -> str:
        return self._transcript_blob(episode_key).download_as_text(encoding="utf-8")

    def write_transcript(self, episode_key: str, text: str) -> str:
        blob = self._transcript_blob(episode_key)
        blob.upload_from_string(text, content_type="text/plain; charset=utf-8")
        return f"gs://{self.bucket_name}/{blob.name}"

    def write_audio(self, episode_key: str, data: bytes, *, content_type: str = "audio/mpeg") -> str:
        blob = self._audio_blob(episode_key)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{self.bucket_name}/{blob.name}"

    def audio_uri(self, episode_key: str) -> str:
        return f"gs://{self.bucket_name}/audio/{episode_key}.mp3"

    def signed_transcript_url(self, episode_key: str, *, ttl_seconds: int) -> str:
        blob = self._transcript_blob(episode_key)
        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=ttl_seconds),
            method="GET",
        )
        return url

    def write_job(self, job_id: str, payload: dict) -> None:
        blob = self._job_blob(job_id)
        blob.upload_from_string(json.dumps(payload), content_type="application/json")

    def read_job(self, job_id: str) -> Optional[dict]:
        blob = self._job_blob(job_id)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text(encoding="utf-8"))


def public_download_path(episode_key: str) -> str:
    return f"/v1/transcripts/{quote(episode_key, safe='')}/download"
