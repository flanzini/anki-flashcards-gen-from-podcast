from __future__ import annotations

import logging
import time
from typing import Protocol

LOGGER = logging.getLogger(__name__)


class SpeechTranscriber(Protocol):
    def transcribe_gcs_audio(self, gcs_uri: str, *, language_code: str) -> str: ...

    def transcribe_local_audio(self, path: str, *, language_code: str) -> str: ...


class MockSpeechTranscriber:
    """Deterministic transcriber for local tests without GCP."""

    def transcribe_gcs_audio(self, gcs_uri: str, *, language_code: str) -> str:
        return (
            f"[mock transcript for {gcs_uri} language={language_code}]\n"
            "Привіт. Це тестовий транскрипт епізоду."
        )

    def transcribe_local_audio(self, path: str, *, language_code: str) -> str:
        return self.transcribe_gcs_audio(path, language_code=language_code)


class GoogleSpeechTranscriber:
    def __init__(self) -> None:
        from google.cloud import speech_v1 as speech  # type: ignore

        self._speech = speech
        self.client = speech.SpeechClient()

    def transcribe_gcs_audio(self, gcs_uri: str, *, language_code: str) -> str:
        speech = self._speech
        audio = speech.RecognitionAudio(uri=gcs_uri)
        config = speech.RecognitionConfig(
            language_code=language_code,
            enable_automatic_punctuation=True,
            model="latest_long",
            use_enhanced=True,
        )
        LOGGER.info("Starting Speech-to-Text long-running recognize for %s", gcs_uri)
        operation = self.client.long_running_recognize(config=config, audio=audio)
        response = operation.result(timeout=3600)
        lines = []
        for result in response.results:
            if result.alternatives:
                lines.append(result.alternatives[0].transcript.strip())
        text = "\n".join(line for line in lines if line)
        if not text:
            raise RuntimeError(f"Speech-to-Text returned empty transcript for {gcs_uri}")
        LOGGER.info("Speech-to-Text finished (%d chars)", len(text))
        return text

    def transcribe_local_audio(self, path: str, *, language_code: str) -> str:
        # Local files should be uploaded to GCS before Google STT; keep a clear error.
        raise RuntimeError(
            "Google Speech-to-Text requires a gs:// audio URI. "
            f"Upload the file first (got local path {path!r})."
        )


def wait_briefly() -> None:
    """Tiny yield helper for tests/hooks; no-op delay kept explicit."""
    time.sleep(0)
