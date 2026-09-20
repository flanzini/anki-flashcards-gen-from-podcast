from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

CLOUD_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = CLOUD_DIR.parent
sys.path.insert(0, str(CLOUD_DIR))
sys.path.insert(0, str(REPO_ROOT))

from podcast_to_anki import Episode  # noqa: E402
from transcript_service.app import app  # noqa: E402
from transcript_service.deps import reset_pipeline_cache  # noqa: E402
from transcript_service.emailer import ConsoleEmailer  # noqa: E402
from transcript_service.jobs import JobStore  # noqa: E402
from transcript_service.models import TranscriptRequest  # noqa: E402
from transcript_service.pipeline import TranscriptPipeline  # noqa: E402
from transcript_service.rss import safe_episode_key, select_episode  # noqa: E402
from transcript_service.speech import MockSpeechTranscriber  # noqa: E402
from transcript_service.storage import LocalStorage  # noqa: E402
from transcript_service.config import Settings  # noqa: E402


@pytest.fixture()
def tmp_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSCRIPT_API_TOKEN", "test-token")
    monkeypatch.setenv("EMAIL_TO", "listener@example.com")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TRANSCRIPT_SPEECH_BACKEND", "mock")
    monkeypatch.setenv("GCS_BUCKET", "")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    reset_pipeline_cache()
    Settings.model_config["env_file"] = None
    yield
    reset_pipeline_cache()


def test_safe_episode_key_normalizes():
    assert safe_episode_key("ULP 2-50: Hello!") == "ulp_2-50_hello"


def test_select_episode_by_title():
    episodes = [
        Episode("Alpha Show", "", "https://a.mp3", "", ""),
        Episode("Doctor Visit", "", "https://b.mp3", "", ""),
    ]
    picked = select_episode(episodes, index=0, title_search="doctor")
    assert picked.title == "Doctor Visit"


def test_cache_hit_skips_speech(tmp_path, monkeypatch):
    storage = LocalStorage(tmp_path)
    storage.write_transcript("demo_ep", "Вже готовий текст.")
    settings = Settings(
        api_token="x",
        email_to="a@b.c",
        local_data_dir=tmp_path,
        speech_backend="mock",
        gcs_bucket="",
    )
    emails: list[str] = []

    class CaptureEmailer(ConsoleEmailer):
        def send_transcript_ready(self, *, to_address, episode_title, download_url, transcript_preview=""):
            emails.append(download_url)
            super().send_transcript_ready(
                to_address=to_address,
                episode_title=episode_title,
                download_url=download_url,
                transcript_preview=transcript_preview,
            )

    pipeline = TranscriptPipeline(
        settings=settings,
        storage=storage,
        speech=MockSpeechTranscriber(),
        emailer=CaptureEmailer(),
        job_store=JobStore(storage),
    )

    monkeypatch.setattr(
        "transcript_service.pipeline.resolve_episode",
        lambda **kwargs: type(
            "E",
            (),
            {
                "title": "Demo Ep",
                "link": "https://example.com",
                "audio_url": "https://example.com/a.mp3",
                "published": "",
                "episode_key": "demo_ep",
            },
        )(),
    )
    result = pipeline.request_transcript(TranscriptRequest(episode_index=0))
    assert result.status.value == "ready"
    assert emails
    assert "demo_ep" in emails[0]


def test_api_requires_auth(tmp_settings):
    client = TestClient(app)
    response = client.post("/v1/transcripts", json={"episode_index": 0})
    assert response.status_code == 401


def test_api_rejects_bad_token(tmp_settings):
    client = TestClient(app)
    response = client.post(
        "/v1/transcripts",
        json={"episode_index": 0},
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401


def test_healthz():
    client = TestClient(app)
    assert client.get("/healthz").json() == {"ok": True}
