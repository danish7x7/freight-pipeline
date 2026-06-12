"""HFLLMClient slice: parse, transient errors, malformed-JSON tolerance (no network)."""

import json
from collections.abc import Callable

import httpx
import pytest

from freight.config import Settings
from freight.factories import build_llm_client
from freight.llm import HFLLMClient, HFTransientError


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> HFLLMClient:
    transport = httpx.MockTransport(handler)
    return HFLLMClient(
        token="tok",
        base_url="https://router.example",
        model="some/model",
        client=httpx.AsyncClient(transport=transport),
    )


def _chat_response(content: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, json={"choices": [{"message": {"content": content}}]}
    )


async def test_valid_json_response_is_parsed() -> None:
    content = json.dumps(
        {"intent": "rate_request", "origin_state": "IL", "confidence": 0.8}
    )
    client = _client(lambda _req: _chat_response(content))
    result = await client.complete("extract this", schema=None)
    assert result.data["intent"] == "rate_request"
    assert result.data["origin_state"] == "IL"
    assert result.confidence == 0.8


async def test_503_raises_transient() -> None:
    client = _client(lambda _req: httpx.Response(503))
    with pytest.raises(HFTransientError):
        await client.complete("x")


async def test_429_raises_transient() -> None:
    client = _client(lambda _req: httpx.Response(429))
    with pytest.raises(HFTransientError):
        await client.complete("x")


async def test_network_error_raises_transient() -> None:
    def _boom(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    client = _client(_boom)
    with pytest.raises(HFTransientError):
        await client.complete("x")


async def test_malformed_model_json_is_low_confidence_not_a_crash() -> None:
    client = _client(lambda _req: _chat_response("this is not json"))
    result = await client.complete("x")
    assert result.data == {}
    assert result.confidence is None
    assert result.raw == "this is not json"


async def test_permanent_4xx_propagates() -> None:
    client = _client(lambda _req: httpx.Response(400, json={"error": "bad request"}))
    with pytest.raises(httpx.HTTPStatusError):
        await client.complete("x")


def test_build_llm_client_constructs_hf() -> None:
    assert isinstance(build_llm_client(Settings(llm_backend="hf")), HFLLMClient)
