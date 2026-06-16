"""Supabase Storage reader — the real ``StorageReader`` for PDF intake (8.3b).

Downloads attachment bytes from a PRIVATE Supabase Storage bucket via the Storage REST
API, authorized with the service-role key. Satisfies the ``freight.pdf.StorageReader``
Protocol (``read(storage_path) -> bytes``).

``storage_path`` is the object key WITHIN the bucket (e.g. ``<msg_id>/file.pdf``) — the
format the Phase 8.3c writer will store on the ``attachments`` row. A non-200 or a
network error raises ``StorageError``; the consumer maps a raise to 5xx → QStash retry →
DLQ, preserving the placeholder's raise-don't-drop posture (a transient Storage blip
never silently drops a document).

The WRITE side (Gmail attachment fetch → bucket upload → attachments row) is Phase 8.3c.
"""

import httpx

from freight.config import Settings

_DEFAULT_TIMEOUT_SECONDS = 10.0


class StorageError(Exception):
    """A Supabase Storage read failed (object missing, auth, or network)."""


class SupabaseStorageReader:
    """Reads attachment bytes from a private Supabase Storage bucket (service role)."""

    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        bucket: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = supabase_url.rstrip("/")
        self._key = service_role_key
        self._bucket = bucket
        self._client = client or httpx.Client(timeout=_DEFAULT_TIMEOUT_SECONDS)

    @classmethod
    def from_settings(cls, settings: Settings) -> "SupabaseStorageReader":
        return cls(
            supabase_url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
            bucket=settings.supabase_storage_bucket,
        )

    def read(self, storage_path: str) -> bytes:
        """Return the object's bytes, or raise StorageError (→ 5xx → retry/DLQ)."""
        url = f"{self._base_url}/storage/v1/object/{self._bucket}/{storage_path}"
        try:
            response = self._client.get(
                url,
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "apikey": self._key,
                },
            )
        except httpx.HTTPError as exc:
            raise StorageError(f"storage read failed: {exc}") from exc
        if response.status_code != 200:
            raise StorageError(
                f"storage read {self._bucket}/{storage_path} -> {response.status_code}"
            )
        return response.content
