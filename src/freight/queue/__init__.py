"""Real queue implementations (selected by config in ``factories``)."""

from freight.queue.qstash import QStashQueue

__all__ = ["QStashQueue"]
