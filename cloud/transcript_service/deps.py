from __future__ import annotations

from functools import lru_cache

from .config import Settings, get_settings
from .emailer import ConsoleEmailer, SmtpEmailer, TranscriptEmailer
from .jobs import JobStore
from .pipeline import TranscriptPipeline
from .speech import GoogleSpeechTranscriber, MockSpeechTranscriber, SpeechTranscriber
from .storage import GcsStorage, LocalStorage, TranscriptStorage


def clear_settings_cache() -> None:
    get_settings.cache_clear()


def build_storage(settings: Settings) -> TranscriptStorage:
    if settings.use_gcs:
        return GcsStorage(settings.gcs_bucket)
    return LocalStorage(settings.local_data_dir)


def build_speech(settings: Settings) -> SpeechTranscriber:
    backend = settings.speech_backend.strip().lower()
    if backend == "mock":
        return MockSpeechTranscriber()
    if backend == "google":
        return GoogleSpeechTranscriber()
    raise ValueError(f"Unsupported TRANSCRIPT_SPEECH_BACKEND: {settings.speech_backend}")


def build_emailer(settings: Settings) -> TranscriptEmailer:
    if settings.smtp_configured:
        return SmtpEmailer(
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            from_address=settings.email_from or settings.smtp_user,
        )
    return ConsoleEmailer()


@lru_cache
def get_pipeline() -> TranscriptPipeline:
    settings = get_settings()
    storage = build_storage(settings)
    return TranscriptPipeline(
        settings=settings,
        storage=storage,
        speech=build_speech(settings),
        emailer=build_emailer(settings),
        job_store=JobStore(storage),
    )


def reset_pipeline_cache() -> None:
    clear_settings_cache()
    get_pipeline.cache_clear()
