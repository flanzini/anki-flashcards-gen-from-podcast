from __future__ import annotations

import logging
import urllib.request
from typing import Callable, Optional

from podcast_to_anki import USER_AGENT

from .config import Settings
from .emailer import TranscriptEmailer
from .jobs import JobStore, enqueue_worker, new_job_id, utc_now
from .models import JobRecord, JobStatus, TranscriptRequest, TranscriptResponse
from .rss import resolve_episode
from .speech import SpeechTranscriber
from .storage import TranscriptStorage

LOGGER = logging.getLogger(__name__)


def download_audio_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


class TranscriptPipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        storage: TranscriptStorage,
        speech: SpeechTranscriber,
        emailer: TranscriptEmailer,
        job_store: JobStore,
        background_runner: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.speech = speech
        self.emailer = emailer
        self.job_store = job_store
        self.background_runner = background_runner

    def request_transcript(self, request: TranscriptRequest) -> TranscriptResponse:
        episode = resolve_episode(
            rss_url=self.settings.rss_url,
            episode_index=request.episode_index,
            title_search=request.title_search,
        )
        email_to = (request.email_to or self.settings.email_to or "").strip()
        if not email_to:
            raise ValueError("Set EMAIL_TO or pass email_to in the request body.")

        if self.storage.transcript_exists(episode.episode_key) and not request.force:
            download_url = self.storage.signed_transcript_url(
                episode.episode_key,
                ttl_seconds=self.settings.signed_url_ttl_seconds,
            )
            preview = self.storage.read_transcript(episode.episode_key)
            self.emailer.send_transcript_ready(
                to_address=email_to,
                episode_title=episode.title,
                download_url=download_url,
                transcript_preview=preview,
            )
            return TranscriptResponse(
                status=JobStatus.READY,
                episode=episode.title,
                episode_key=episode.episode_key,
                download_url=download_url,
                message="Cached transcript emailed.",
            )

        job = JobRecord(
            job_id=new_job_id(),
            status=JobStatus.QUEUED,
            episode_key=episode.episode_key,
            episode_title=episode.title,
            audio_url=episode.audio_url,
            source=episode.link or episode.audio_url,
            email_to=email_to,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.job_store.save(job)
        self._dispatch(job.job_id)
        return TranscriptResponse(
            status=JobStatus.QUEUED,
            episode=episode.title,
            episode_key=episode.episode_key,
            job_id=job.job_id,
            message="Transcription queued. You will receive an email when ready.",
        )

    def _dispatch(self, job_id: str) -> None:
        if self.settings.cloud_tasks_configured:
            enqueue_worker(self.settings, job_id)
            return
        if self.background_runner is not None:
            self.background_runner(job_id)
            return
        # Synchronous fallback (local CLI / tests).
        self.run_job(job_id)

    def run_job(self, job_id: str) -> JobRecord:
        job = self.job_store.get(job_id)
        if job is None:
            raise FileNotFoundError(f"Unknown job_id: {job_id}")
        if job.status == JobStatus.READY and job.download_url:
            return job

        job.status = JobStatus.RUNNING
        job.error = ""
        self.job_store.save(job)

        try:
            if self.storage.transcript_exists(job.episode_key):
                text = self.storage.read_transcript(job.episode_key)
                uri = f"cached:{job.episode_key}"
            else:
                LOGGER.info("Downloading audio for %s", job.episode_title)
                audio_bytes = download_audio_bytes(job.audio_url)
                audio_uri = self.storage.write_audio(job.episode_key, audio_bytes)
                # Prefer gs:// URI for Google STT; local backend returns a path/file URI.
                stt_uri = self.storage.audio_uri(job.episode_key)
                LOGGER.info("Transcribing %s via %s", job.episode_title, stt_uri)
                if stt_uri.startswith("gs://"):
                    text = self.speech.transcribe_gcs_audio(
                        stt_uri,
                        language_code=self.settings.speech_language_code,
                    )
                else:
                    text = self.speech.transcribe_local_audio(
                        audio_uri,
                        language_code=self.settings.speech_language_code,
                    )
                uri = self.storage.write_transcript(job.episode_key, text)

            download_url = self.storage.signed_transcript_url(
                job.episode_key,
                ttl_seconds=self.settings.signed_url_ttl_seconds,
            )
            self.emailer.send_transcript_ready(
                to_address=job.email_to,
                episode_title=job.episode_title,
                download_url=download_url,
                transcript_preview=text,
            )
            job.status = JobStatus.READY
            job.transcript_uri = uri
            job.download_url = download_url
            job.error = ""
            return self.job_store.save(job)
        except Exception as exc:  # noqa: BLE001 - persist failure for phone clients
            LOGGER.exception("Job %s failed", job_id)
            job.status = JobStatus.FAILED
            job.error = str(exc)
            return self.job_store.save(job)
