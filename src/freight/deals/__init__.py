"""Deal state machine and service."""

from freight.deals.service import FinalizeResult, finalize, rate_key_from
from freight.deals.state_machine import (
    DealEvent,
    DealState,
    TransitionError,
    advance,
    is_holdable,
)

__all__ = [
    "DealEvent",
    "DealState",
    "FinalizeResult",
    "TransitionError",
    "advance",
    "finalize",
    "is_holdable",
    "rate_key_from",
]
