"""Gmail API OAuth and email sending.

This module uses the Gmail API, not SMTP. The first authorization run opens a
browser window and creates token.json. Future runs refresh token.json quietly.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
import logging
import mimetypes
from pathlib import Path
import time

from config import Settings, validate_gmail_settings, validate_sender_settings


logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


class TransientGmailError(RuntimeError):
    """Raised for Gmail API errors that are safe to retry."""


class GmailClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._service = None

    def authenticate(self):
        """Create or refresh Gmail OAuth credentials and return a Gmail service."""

        validate_gmail_settings(self.settings)

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build

        creds = None
        if self.settings.gmail_token_file.exists():
            creds = Credentials.from_authorized_user_file(
                str(self.settings.gmail_token_file),
                SCOPES,
            )

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.settings.gmail_credentials_file),
                    SCOPES,
                )
                creds = flow.run_local_server(port=0)
            self.settings.gmail_token_file.write_text(creds.to_json(), encoding="utf-8")

        self._service = build("gmail", "v1", credentials=creds)
        logger.info("Gmail OAuth is ready using token file %s", self.settings.gmail_token_file)
        return self._service

    @property
    def service(self):
        if self._service is None:
            return self.authenticate()
        return self._service

    def _create_raw_message(
        self,
        to_email: str,
        subject: str,
        body: str,
        attachment_paths=None,
    ) -> dict:
        validate_sender_settings(self.settings)

        message = EmailMessage()
        message["To"] = to_email
        message["From"] = f"{self.settings.sender_name} <{self.settings.sender_email}>"
        message["Subject"] = subject
        message.set_content(body)

        for attachment_path in attachment_paths or []:
            path = Path(attachment_path)
            content_type, _ = mimetypes.guess_type(path.name)
            if content_type:
                maintype, subtype = content_type.split("/", 1)
            else:
                maintype, subtype = "application", "octet-stream"

            message.add_attachment(
                path.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                filename=path.name,
            )

        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        return {"raw": encoded_message}

    def send_email(self, to_email: str, subject: str, body: str, attachment_paths=None) -> str:
        """Send one email through Gmail API and return Gmail's message id."""

        attempts = max(self.settings.max_retries, 1)
        for attempt_number in range(1, attempts + 1):
            try:
                return self._send_email_once(to_email, subject, body, attachment_paths)
            except RuntimeError:
                raise
            except (TransientGmailError, OSError, TimeoutError) as exc:
                self._service = None
                if attempt_number >= attempts:
                    raise
                wait_seconds = min(30, 2 * attempt_number)
                logger.warning(
                    "Transient Gmail send error for %s on attempt %s/%s: %s. Retrying in %ss.",
                    to_email,
                    attempt_number,
                    attempts,
                    exc,
                    wait_seconds,
                )
                time.sleep(wait_seconds)
        return ""

    def _send_email_once(self, to_email: str, subject: str, body: str, attachment_paths=None) -> str:
        from googleapiclient.errors import HttpError

        try:
            sent_message = (
                self.service.users()
                .messages()
                .send(
                    userId="me",
                    body=self._create_raw_message(to_email, subject, body, attachment_paths),
                )
                .execute()
            )
        except HttpError as error:
            status = getattr(error.resp, "status", None)
            if status in {400, 401, 403}:
                raise RuntimeError(f"Gmail API rejected the request: {error}") from error
            if status in {429, 500, 502, 503, 504}:
                raise TransientGmailError(f"Gmail API temporary error: {error}") from error
            raise

        message_id = sent_message.get("id", "")
        logger.info("Gmail sent message id %s to %s", message_id, to_email)
        return message_id
