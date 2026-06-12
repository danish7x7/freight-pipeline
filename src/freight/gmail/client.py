"""Real ``GmailClient`` — single-inbox, refresh-token OAuth, least-privilege scopes.

Structural slice: the Google API ``service`` (a googleapiclient Resource) is INJECTED so
the mapping logic is unit-testable with no live calls. ``from_settings`` builds the real
service from the refresh token (no token table — single mailbox; the refresh token is
one runtime secret).

⚠️ Phase 8: verify end-to-end against a live mailbox when wiring the deployed poll. The
mapping here is exercised only against stub Gmail payloads.
"""

import base64
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

from freight.config import Settings
from freight.interfaces.types import InboundMessage, OutboundMessage

# Least privilege: read the inbox and send replies — nothing else (no modify/delete).
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

_TOKEN_URI = "https://oauth2.googleapis.com/token"


def _decode_b64url(data: str) -> str:
    # Gmail uses URL-safe base64 without padding; restore it before decoding.
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _extract_body(payload: dict[str, Any]) -> str:
    """Return the text/plain body from a Gmail message payload (best effort)."""
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data")
            if data:
                return _decode_b64url(data)
    data = payload.get("body", {}).get("data")
    return _decode_b64url(data) if data else ""


def _to_inbound(raw: dict[str, Any]) -> InboundMessage:
    """Map a Gmail ``users.messages.get`` resource to an InboundMessage."""
    payload = raw.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    received_at = datetime.fromtimestamp(int(raw["internalDate"]) / 1000, tz=UTC)
    return InboundMessage(
        gmail_message_id=raw["id"],
        thread_id=raw.get("threadId", ""),
        sender=headers.get("from", ""),
        subject=headers.get("subject", ""),
        body=_extract_body(payload),
        received_at=received_at,
    )


def _to_raw(message: OutboundMessage) -> str:
    """Build a base64url-encoded MIME message for ``users.messages.send``."""
    mime = EmailMessage()
    mime["To"] = message.to
    mime["Subject"] = message.subject
    if message.in_reply_to:
        mime["In-Reply-To"] = message.in_reply_to
        mime["References"] = message.in_reply_to
    for name, value in message.headers.items():
        mime[name] = value
    mime.set_content(message.body)
    return base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")


class GmailApiClient:
    """GmailClient backed by an injected googleapiclient service resource."""

    def __init__(self, service: Any, *, user_id: str = "me") -> None:
        self._service = service
        self._user_id = user_id

    @classmethod
    def from_settings(cls, settings: Settings) -> "GmailApiClient":
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        credentials = Credentials(  # type: ignore[no-untyped-call]
            token=None,
            refresh_token=settings.gmail_refresh_token,
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret,
            token_uri=_TOKEN_URI,
            scopes=SCOPES,
        )
        service = build(
            "gmail", "v1", credentials=credentials,
            cache_discovery=False, static_discovery=True,
        )
        return cls(service)

    def list_messages(self) -> list[InboundMessage]:
        listing = (
            self._service.users().messages().list(userId=self._user_id).execute()
        )
        ids = [item["id"] for item in listing.get("messages", [])]
        return [self.get_message(message_id) for message_id in ids]

    def get_message(self, message_id: str) -> InboundMessage:
        raw = (
            self._service.users()
            .messages()
            .get(userId=self._user_id, id=message_id, format="full")
            .execute()
        )
        return _to_inbound(raw)

    def send(self, message: OutboundMessage) -> str:
        result = (
            self._service.users()
            .messages()
            .send(userId=self._user_id, body={"raw": _to_raw(message)})
            .execute()
        )
        return str(result["id"])
