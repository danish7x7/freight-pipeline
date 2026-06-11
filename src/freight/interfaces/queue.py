"""The ``Queue`` contract.

The real queue is Upstash QStash, which is **push-based**: it delivers each message
to an HTTP endpoint. There is deliberately no ``consume``/``subscribe`` pull method —
a pull model would not map onto QStash. The consumption side is expressed as a
``Handler`` the transport invokes per delivered message.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from freight.interfaces.types import QueueMessage

Handler = Callable[[QueueMessage], Awaitable[None]]
"""A coroutine the transport calls once per delivered message."""


@runtime_checkable
class Queue(Protocol):
    """Publish work for asynchronous processing (push-based delivery)."""

    async def publish(self, message: QueueMessage) -> None: ...
