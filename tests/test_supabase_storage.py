"""SupabaseStorageReader (8.3b) — unit tests with a mocked Storage client.

The live bucket is exercised in the e2e step, not here.
"""

from collections.abc import Callable

import httpx
import pytest

from freight.config import Settings
from freight.pdf import StorageReader
from freight.storage import StorageError, SupabaseStorageReader

URL = "https://proj.supabase.co"
KEY = "service-role-key"
BUCKET = "attachments"

Handler = Callable[[httpx.Request], httpx.Response]


def _reader(handler: Handler) -> SupabaseStorageReader:
    return SupabaseStorageReader(
        supabase_url=URL,
        service_role_key=KEY,
        bucket=BUCKET,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_read_returns_bytes_and_hits_storage_object_url() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["apikey"] = request.headers.get("apikey")
        return httpx.Response(200, content=b"%PDF-1.4 bytes")

    data = _reader(handler).read("msg-123/rc.pdf")
    assert data == b"%PDF-1.4 bytes"
    assert seen["url"] == f"{URL}/storage/v1/object/{BUCKET}/msg-123/rc.pdf"
    assert seen["auth"] == f"Bearer {KEY}"  # service-role authorizes the private bucket
    assert seen["apikey"] == KEY


def test_non_200_raises_storage_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"not found")

    with pytest.raises(StorageError):
        _reader(handler).read("missing.pdf")


def test_network_error_raises_storage_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(StorageError):
        _reader(handler).read("x.pdf")


def test_from_settings_wires_url_key_bucket_and_strips_trailing_slash() -> None:
    reader = SupabaseStorageReader.from_settings(
        Settings(
            supabase_url=URL + "/",
            supabase_service_role_key=KEY,
            supabase_storage_bucket=BUCKET,
        )
    )
    assert reader._bucket == BUCKET
    assert reader._key == KEY
    assert reader._base_url == URL  # trailing slash stripped


def test_satisfies_storage_reader_protocol() -> None:
    # Structural check: usable wherever the consumer expects a StorageReader.
    reader: StorageReader = _reader(lambda _r: httpx.Response(200, content=b""))
    assert callable(reader.read)
