"""One-shot local mock request against the FastAPI app (no GCP, no RSS)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
CLOUD = ROOT / "cloud"
DATA = ROOT / "cloud_data" / "live_mock"

sys.path.insert(0, str(CLOUD))
sys.path.insert(0, str(ROOT))


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "transcripts").mkdir(exist_ok=True)
    transcript_path = DATA / "transcripts" / "demo_live_episode.txt"
    transcript_path.write_text(
        "Привіт! Це локальний mock-транскрипт для перевірки API.\nДругий рядок.\n",
        encoding="utf-8",
    )

    os.environ["TRANSCRIPT_API_TOKEN"] = "dev-token"
    os.environ["EMAIL_TO"] = "you@example.com"
    os.environ["TRANSCRIPT_SPEECH_BACKEND"] = "mock"
    os.environ["LOCAL_DATA_DIR"] = str(DATA)
    os.environ["GCS_BUCKET"] = ""
    os.environ.pop("SMTP_HOST", None)

    from fastapi.testclient import TestClient

    from transcript_service.app import app
    from transcript_service.deps import reset_pipeline_cache
    from transcript_service.rss import SelectedEpisode

    reset_pipeline_cache()
    episode = SelectedEpisode(
        title="Demo Live Episode",
        link="https://example.com/demo",
        audio_url="https://example.com/demo.mp3",
        published="",
        episode_key="demo_live_episode",
    )

    out = {
        "transcript_path": str(transcript_path),
        "health": None,
        "transcript_request": None,
    }

    with patch("transcript_service.pipeline.resolve_episode", return_value=episode):
        client = TestClient(app)
        health = client.get("/healthz")
        out["health"] = {"status_code": health.status_code, "body": health.json()}
        resp = client.post(
            "/v1/transcripts",
            headers={"Authorization": "Bearer dev-token"},
            json={"episode_index": 0},
        )
        out["transcript_request"] = {"status_code": resp.status_code, "body": resp.json()}

    result_path = DATA / "last_response.json"
    result_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(result_path.read_text(encoding="utf-8"))
    return 0 if out["transcript_request"]["status_code"] == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
