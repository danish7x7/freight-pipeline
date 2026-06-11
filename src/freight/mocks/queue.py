"""In-memory ``Queue`` mock.

Mirrors the push model: published messages are recorded, and an optional ``Handler``
is invoked per message to stand in for QStash's HTTP delivery.
"""

from freight.interfaces.queue import Handler
from freight.interfaces.types import QueueMessage


class InMemoryQueue:
    """Records published messages; optionally dispatches them to a handler."""

    def __init__(self, handler: Handler | None = None) -> None:
        self._handler = handler
        self.published: list[QueueMessage] = []

    async def publish(self, message: QueueMessage) -> None:
        self.published.append(message)
        if self._handler is not None:
            await self._handler(message)
