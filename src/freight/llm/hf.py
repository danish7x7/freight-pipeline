"""HFLLMClient — real ``LLMClient`` over HF serverless inference (structural slice).

Targets the OpenAI-compatible chat-completions surface on the HF router. The httpx
client is injectable so the slice is MockTransport-tested with no network.

Failure model (so the consumer can map transient→retry, content→review):
- 429 / 5xx (incl. 503 cold-start) / network errors → ``HFTransientError`` (retry);
- a 2xx whose model content is not valid JSON → a LOW-confidence ``LLMResult`` (never
  crash) → routes to review downstream.

CONFIRMED against the live Inference Providers API (Phase 8.2, 2026-06-15; see
DECISIONS): base ``https://router.huggingface.co`` + ``/v1/chat/completions``; request
``{model, messages, response_format}``; response ``choices[0].message.content``; auth
``Bearer``. ``response_format={"type": "json_object"}`` is HONORED server-side
(enforced) on the pinned model, so no ``json_schema``/strict is needed. The pin is
``HF_MODEL=meta-llama/Llama-3.3-70B-Instruct``; a bare ``org/model`` auto-routes to the
fastest provider (``:fastest`` default) — a ``:provider``/``:cheapest`` suffix is an
optional reproducibility lever (Phase 9).
"""

import json
from typing import Any

import httpx
from pydantic import BaseModel

from freight.config import Settings
from freight.interfaces.types import LLMResult

_CHAT_PATH = "/v1/chat/completions"
_DEFAULT_TIMEOUT_SECONDS = 30.0


class HFTransientError(Exception):
    """A retryable HF failure (cold-start 503, rate-limit 429, 5xx, or network)."""


class HFLLMClient:
    """Structured inference via HF chat-completions; always returns an LLMResult."""

    def __init__(
        self,
        *,
        token: str,
        base_url: str,
        model: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS)

    @classmethod
    def from_settings(cls, settings: Settings) -> "HFLLMClient":
        return cls(
            token=settings.hf_token,
            base_url=settings.hf_base_url,
            model=settings.hf_model,
        )

    async def complete(
        self, prompt: str, *, schema: type[BaseModel] | None = None
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if schema is not None:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = await self._client.post(
                f"{self._base_url}{_CHAT_PATH}",
                headers={"Authorization": f"Bearer {self._token}"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise HFTransientError(f"HF request failed: {exc}") from exc

        status = response.status_code
        if status == 429 or status >= 500:
            raise HFTransientError(f"HF transient status {status}")
        if status >= 400:
            response.raise_for_status()  # permanent 4xx — surfaces loudly

        return self._parse(response)

    @staticmethod
    def _parse(response: httpx.Response) -> LLMResult:
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            return LLMResult(data={}, raw=response.text, confidence=None)

        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            return LLMResult(data={}, raw=str(content), confidence=None)
        if not isinstance(parsed, dict):
            return LLMResult(data={}, raw=str(content), confidence=None)

        reported = parsed.get("confidence")
        confidence = (
            float(reported)
            if isinstance(reported, int | float) and not isinstance(reported, bool)
            else None
        )
        return LLMResult(data=parsed, raw=str(content), confidence=confidence)
