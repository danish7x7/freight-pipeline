"""Real GmailClient: least-privilege scopes + payload mapping (no live calls)."""

import base64
from datetime import UTC, datetime
from typing import Any

from freight.config import Settings
from freight.factories import build_gmail_client
from freight.gmail import SCOPES, GmailApiClient
from freight.interfaces.types import OutboundMessage


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


class _Exec:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def execute(self) -> dict[str, Any]:
        return self._result


class _FakeMessages:
    def __init__(
        self, listing: dict[str, Any], by_id: dict[str, dict[str, Any]]
    ) -> None:
        self._listing = listing
        self._by_id = by_id
        self.sent_bodies: list[dict[str, str]] = []

    # userId / id / format mirror the Gmail API's exact kwargs.
    def list(self, userId: str, **_kwargs: object) -> _Exec:  # noqa: N803
        return _Exec(self._listing)

    def get(self, **kwargs: object) -> _Exec:
        # All Gmail get kwargs (userId/id/format/metadataHeaders) arrive by keyword.
        return _Exec(self._by_id[str(kwargs["id"])])

    def send(self, userId: str, body: dict[str, str]) -> _Exec:  # noqa: N803
        self.sent_bodies.append(body)
        return _Exec({"id": "sent-abc"})


class _FakeUsers:
    def __init__(self, messages: _FakeMessages) -> None:
        self._messages = messages

    def messages(self) -> _FakeMessages:
        return self._messages


class _FakeService:
    def __init__(self, messages: _FakeMessages) -> None:
        self._messages = messages

    def users(self) -> _FakeUsers:
        return _FakeUsers(self._messages)


def test_scopes_are_least_privilege() -> None:
    assert SCOPES == [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ]
    # No read-write / destructive scopes.
    assert not any(("modify" in s or "gmail.full" in s) for s in SCOPES)


def test_list_messages_maps_payload() -> None:
    body_text = "dry van 42,000 lbs CHI->DAL"
    raw = {
        "id": "abc123",
        "threadId": "thread-1",
        "internalDate": "1700000000000",  # 2023-11-14T22:13:20Z
        "payload": {
            "headers": [
                {"name": "From", "value": "broker@example.com"},
                {"name": "Subject", "value": "Rate request CHI->DAL"},
            ],
            "body": {"data": _b64url(body_text)},
        },
    }
    messages = _FakeMessages({"messages": [{"id": "abc123"}]}, {"abc123": raw})
    client = GmailApiClient(_FakeService(messages))

    result = client.list_messages()

    assert len(result) == 1
    msg = result[0]
    assert msg.gmail_message_id == "abc123"
    assert msg.thread_id == "thread-1"
    assert msg.sender == "broker@example.com"
    assert msg.subject == "Rate request CHI->DAL"
    assert msg.body == body_text
    assert msg.received_at == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)


def test_list_messages_reads_text_plain_part() -> None:
    body_text = "reefer ATL->MIA 38k"
    raw = {
        "id": "p1",
        "threadId": "t",
        "internalDate": "1700000000000",
        "payload": {
            "headers": [{"name": "From", "value": "ops@x.com"}],
            "parts": [
                {"mimeType": "text/html", "body": {"data": _b64url("<p>ignored</p>")}},
                {"mimeType": "text/plain", "body": {"data": _b64url(body_text)}},
            ],
        },
    }
    client = GmailApiClient(
        _FakeService(_FakeMessages({"messages": [{"id": "p1"}]}, {"p1": raw}))
    )
    assert client.list_messages()[0].body == body_text


def test_send_builds_mime_and_returns_id() -> None:
    messages = _FakeMessages({"messages": []}, {})
    client = GmailApiClient(_FakeService(messages))
    out = OutboundMessage(
        to="a@b.c",
        subject="re: rate",
        body="Here is your quote.",
        in_reply_to="orig-1",
        headers={"X-Freight-Quote-Id": "quote-9"},
    )

    sent_id = client.send(out)

    assert sent_id == "sent-abc"
    raw = messages.sent_bodies[0]["raw"]
    decoded = base64.urlsafe_b64decode(raw + "===").decode()
    assert "To: a@b.c" in decoded
    assert "Subject: re: rate" in decoded
    assert "In-Reply-To: orig-1" in decoded
    assert "References: orig-1" in decoded  # both headers thread recipient-side
    assert "X-Freight-Quote-Id: quote-9" in decoded  # dedup marker present
    assert "Here is your quote." in decoded


def test_get_rfc_message_id_reads_header() -> None:
    raw = {
        "id": "m1",
        "payload": {
            "headers": [
                {"name": "Message-ID", "value": "<CABc123@mail.gmail.com>"},
                {"name": "From", "value": "x@y.z"},
            ]
        },
    }
    client = GmailApiClient(_FakeService(_FakeMessages({"messages": []}, {"m1": raw})))
    assert client.get_rfc_message_id("m1") == "<CABc123@mail.gmail.com>"


def test_get_rfc_message_id_none_when_absent() -> None:
    raw = {"id": "m2", "payload": {"headers": [{"name": "From", "value": "x@y.z"}]}}
    client = GmailApiClient(_FakeService(_FakeMessages({"messages": []}, {"m2": raw})))
    assert client.get_rfc_message_id("m2") is None


def test_send_sets_threadid_and_both_reply_headers_when_present() -> None:
    messages = _FakeMessages({"messages": []}, {})
    client = GmailApiClient(_FakeService(messages))
    client.send(
        OutboundMessage(
            to="a@b.c", subject="re", body="x",
            in_reply_to="<r@mail.gmail.com>", thread_id="thread-9",
        )
    )
    body = messages.sent_bodies[0]
    assert body["threadId"] == "thread-9"  # sender-side threading
    decoded = base64.urlsafe_b64decode(body["raw"] + "===").decode()
    assert "In-Reply-To: <r@mail.gmail.com>" in decoded
    assert "References: <r@mail.gmail.com>" in decoded


def test_send_omits_threadid_and_reply_headers_when_absent() -> None:
    messages = _FakeMessages({"messages": []}, {})
    client = GmailApiClient(_FakeService(messages))
    client.send(OutboundMessage(to="a@b.c", subject="re", body="x"))  # neither set
    body = messages.sent_bodies[0]
    assert "threadId" not in body
    decoded = base64.urlsafe_b64decode(body["raw"] + "===").decode()
    assert "In-Reply-To:" not in decoded
    assert "References:" not in decoded


def test_build_gmail_client_constructs_real_client() -> None:
    client = build_gmail_client(Settings(gmail_backend="gmail"))
    assert isinstance(client, GmailApiClient)
