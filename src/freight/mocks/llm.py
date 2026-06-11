"""In-memory ``LLMClient`` mock returning a canned ``LLMResult``."""

from pydantic import BaseModel

from freight.interfaces.types import LLMResult


class MockLLMClient:
    """Returns a fixed structured result regardless of prompt.

    Records every prompt it was asked to complete for test assertions.
    """

    def __init__(self, result: LLMResult | None = None) -> None:
        self._result = result or LLMResult(
            data={"intent": "rate_request"},
            raw='{"intent": "rate_request"}',
            confidence=0.9,
        )
        self.prompts: list[str] = []

    async def complete(
        self, prompt: str, *, schema: type[BaseModel] | None = None
    ) -> LLMResult:
        self.prompts.append(prompt)
        return self._result
