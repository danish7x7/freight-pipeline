"""QStashQueue — real ``Queue`` implementation (structural slice).

Publishes a message to Upstash QStash, which then delivers it (push, at-least-once) to
the consumer endpoint and DLQs it after retries are exhausted.

⚠️ VERIFY AGAINST CURRENT UPSTASH QSTASH DOCS AT LIVE-WIRING (Phase 8). The following
are plausible and correct enough for a MockTransport-tested slice, but are NOT confirmed
against a live account here:
  - publish path ``/v2/publish/{destination_url}``
  - retry header name ``Upstash-Retries`` (counts retries AFTER the first attempt)
  - automatic DLQ after retries are exhausted
Do not treat these strings as verified until checked live.
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
