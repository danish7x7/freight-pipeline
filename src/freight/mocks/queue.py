"""In-memory ``Queue`` mock — publish-only, matching the push-based Protocol.

Records published messages. Delivery (retry + DLQ) is NOT this object's concern; that
lives in ``LocalDispatcher`` (the separate consume-side abstraction), mirroring how
QStash owns delivery server-side while ``Queue`` only publishes.
"""

from freight.interfaces.types import QueueMessage


class InMemoryQueue:
    """Records every published message."""

    def __init__(self) -> None:
        self.published: list[QueueMessage] = []

    async def publish(self, message: QueueMessage) -> None:
        self.published.append(message)
