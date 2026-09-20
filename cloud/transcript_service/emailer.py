from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

LOGGER = logging.getLogger(__name__)


class TranscriptEmailer(Protocol):
    def send_transcript_ready(
        self,
        *,
        to_address: str,
        episode_title: str,
        download_url: str,
        transcript_preview: str = "",
    ) -> None: ...


class ConsoleEmailer:
    def send_transcript_ready(
        self,
        *,
        to_address: str,
        episode_title: str,
        download_url: str,
        transcript_preview: str = "",
    ) -> None:
        LOGGER.info(
            "EMAIL (console) to=%s episode=%r url=%s preview_chars=%d",
            to_address,
            episode_title,
            download_url,
            len(transcript_preview),
        )


class SmtpEmailer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        from_address: str,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_address = from_address

    def send_transcript_ready(
        self,
        *,
        to_address: str,
        episode_title: str,
        download_url: str,
        transcript_preview: str = "",
    ) -> None:
        message = EmailMessage()
        message["Subject"] = f"Transcript: {episode_title}"
        message["From"] = self.from_address
        message["To"] = to_address
        preview = (transcript_preview[:500] + "…") if len(transcript_preview) > 500 else transcript_preview
        body = (
            f"Your transcript for {episode_title} is ready.\n\n"
            f"Download: {download_url}\n\n"
        )
        if preview:
            body += f"Preview:\n{preview}\n"
        message.set_content(body)

        LOGGER.info("Sending transcript email to %s via %s", to_address, self.host)
        with smtplib.SMTP(self.host, self.port, timeout=60) as smtp:
            smtp.starttls()
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(message)
