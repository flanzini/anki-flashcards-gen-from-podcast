from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    api_token: str = Field(default="", alias="TRANSCRIPT_API_TOKEN")
    rss_url: str = Field(
        default="https://feeds.buzzsprout.com/1370836.rss",
        alias="TRANSCRIPT_RSS_URL",
    )
    gcs_bucket: str = Field(default="", alias="GCS_BUCKET")
    local_data_dir: Path = Field(default=Path("cloud_data"), alias="LOCAL_DATA_DIR")
    signed_url_ttl_seconds: int = Field(default=7 * 24 * 3600, alias="SIGNED_URL_TTL_SECONDS")

    speech_backend: str = Field(default="google", alias="TRANSCRIPT_SPEECH_BACKEND")
    speech_language_code: str = Field(default="uk-UA", alias="SPEECH_LANGUAGE_CODE")

    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    email_from: str = Field(default="", alias="EMAIL_FROM")
    email_to: str = Field(default="", alias="EMAIL_TO")

    worker_url: str = Field(default="", alias="TRANSCRIPT_WORKER_URL")
    worker_token: str = Field(default="", alias="TRANSCRIPT_WORKER_TOKEN")
    cloud_tasks_project: str = Field(default="", alias="CLOUD_TASKS_PROJECT")
    cloud_tasks_location: str = Field(default="europe-west1", alias="CLOUD_TASKS_LOCATION")
    cloud_tasks_queue: str = Field(default="", alias="CLOUD_TASKS_QUEUE")
    gcp_service_account_email: str = Field(default="", alias="GCP_SERVICE_ACCOUNT_EMAIL")

    public_base_url: str = Field(default="", alias="PUBLIC_BASE_URL")

    @property
    def use_gcs(self) -> bool:
        return bool(self.gcs_bucket)

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.email_to and (self.email_from or self.smtp_user))

    @property
    def cloud_tasks_configured(self) -> bool:
        return bool(
            self.cloud_tasks_project
            and self.cloud_tasks_queue
            and self.worker_url
            and (self.worker_token or self.api_token)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
