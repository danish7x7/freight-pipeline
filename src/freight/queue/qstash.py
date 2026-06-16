"""QStashQueue — real ``Queue`` implementation (structural slice).

Publishes a message to Upstash QStash, which then delivers it (push, at-least-once) to
the consumer endpoint and DLQs it after retries are exhausted.

CONFIRMED against the live Upstash QStash docs (Phase 8.3b, 2026-06-15):
  - publish path ``/v2/publish/{destination_url}`` — the destination is a raw URL
    appended to the path; NO pre-registration / topic needed (direct-URL publish).
  - retry header ``Upstash-Retries`` — total deliveries = 1 + retries.
  - automatic DLQ after retries are exhausted.
The signing keys + token are account-level (set in Render env); QStash signs each
delivery and the /ingest verifier (6.1) checks current→next + the sub claim.
"""

import httpx

from freight.interfaces.types import QueueMessage

DEFAULT_RETRIES = 3
DEFAULT_TIMEOUT_SECONDS = 10.0


class QStashQueue:
    """Publish messages to QStash for push delivery to the consumer endpoint."""

    def __init__(
        self,
        *,
        token: str,
        qstash_url: str,
        destination_url: str,
        retries: int = DEFAULT_RETRIES,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token
        self._qstash_url = qstash_url.rstrip("/")
        self._destination_url = destination_url
        self._retries = retries
        self._client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS)

    async def publish(self, message: QueueMessage) -> None:
        url = f"{self._qstash_url}/v2/publish/{self._destination_url}"
        response = await self._client.post(
            url,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Upstash-Retries": str(self._retries),
                "Content-Type": "application/json",
            },
            content=message.model_dump_json(),
        )
        response.raise_for_status()
