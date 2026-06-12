"""Boundary DTOs shared by the interface contracts.

These Pydantic models are the typed currency that flows across the ``GmailClient``,
``Queue``, and ``LLMClient`` seams. All inbound data is untrusted until validated
downstream; these models only describe shape, not trust.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class InboundMessage(BaseModel):
    """An email pulled from the inbox.

    ``gmail_message_id`` is the idempotency key: every inbound message is processed
    and replied to at most once per id.
    """

    gmail_message_id: str
    thread_id: str
    sender: str
    subject: str
    body: str
    received_at: datetime
    attachment_refs: list[str] = Field(default_factory=list)


class OutboundMessage(BaseModel):
    """A reply queued for human-approved sending."""

    to: str
    subject: str
    body: str
    in_reply_to: str | None = None
    # Custom MIME headers, e.g. X-Freight-Quote-Id — a marker for future send dedup
    # (closing the at-least-once double-send window; see DECISIONS).
    headers: dict[str, str] = Field(default_factory=dict)


class QueueMessage(BaseModel):
    """A unit of work on the queue.

    ``id`` carries the idempotency key (typically the ``gmail_message_id``) so the
    consumer can claim-once.
    """

    id: str
    payload: dict[str, Any] = Field(default_factory=dict)


class LLMResult(BaseModel):
    """The structured wrapper an ``LLMClient`` always returns.

    ``data`` holds the decoded structured output (a plain dict for now), ``raw`` the
    underlying model text, and ``confidence`` an optional score in ``[0, 1]``.
    """

    data: dict[str, Any] = Field(default_factory=dict)
    raw: str
    confidence: float | None = None
