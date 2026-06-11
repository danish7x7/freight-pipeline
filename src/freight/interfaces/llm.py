"""The ``LLMClient`` contract. Implementations are selected by config."""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from freight.interfaces.types import LLMResult


@runtime_checkable
class LLMClient(Protocol):
    """Structured inference. Always returns an ``LLMResult``, never raw text.

    When ``schema`` is provided, implementations should decode the model output
    against it; the structured payload lands in ``LLMResult.data``.
    """

    async def complete(
        self, prompt: str, *, schema: type[BaseModel] | None = None
    ) -> LLMResult: ...
