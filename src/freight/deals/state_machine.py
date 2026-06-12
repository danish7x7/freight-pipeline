"""The deal state machine (pure logic, no DB).

new_enquiry → quoted → negotiating ⇄ quoted → rc_received → contract_signed → scheduled,
with rejected / on_hold reachable from the active states. ``advance`` rejects skips.

RESUME: on_hold carries no history in a pure ``advance``, so resume requires the caller
to pass ``held_from`` — the active state the deal was held from. 4.6 records it on the
deal row when a ``hold`` occurs and passes it back on ``resume``. No ``held_from`` (or a
non-holdable one) → TransitionError; a deal can't resume into nowhere.
"""

from typing import Literal

DealState = Literal[
    "new_enquiry",
    "quoted",
    "negotiating",
    "rc_received",
    "contract_signed",
    "scheduled",
    "rejected",
    "on_hold",
]

DealEvent = Literal[
    "quote_sent",
    "counter_received",
    "requote",
    "rc_received",
    "contract_signed",
    "scheduled",
    "reject",
    "hold",
    "resume",
]


class TransitionError(Exception):
    """An illegal state transition was attempted."""

    def __init__(self, state: DealState, event: DealEvent) -> None:
        super().__init__(f"illegal transition: {event!r} from {state!r}")
        self.state = state
        self.event = event


# States a deal can be held from (and therefore resumed into).
_HOLDABLE: frozenset[DealState] = frozenset(
    {"new_enquiry", "quoted", "negotiating", "rc_received", "contract_signed"}
)

# Deterministic transitions. `resume` is handled specially (target = held_from).
# `hold`/`reject` are added to every holdable state; scheduled/rejected are terminal.
_TRANSITIONS: dict[DealState, dict[DealEvent, DealState]] = {
    "new_enquiry": {"quote_sent": "quoted", "hold": "on_hold", "reject": "rejected"},
    "quoted": {
        "counter_received": "negotiating",
        "rc_received": "rc_received",
        "hold": "on_hold",
        "reject": "rejected",
    },
    "negotiating": {
        "requote": "quoted",
        "rc_received": "rc_received",
        "hold": "on_hold",
        "reject": "rejected",
    },
    "rc_received": {
        "contract_signed": "contract_signed",
        "hold": "on_hold",
        "reject": "rejected",
    },
    "contract_signed": {
        "scheduled": "scheduled",
        "hold": "on_hold",
        "reject": "rejected",
    },
    "scheduled": {},  # terminal (success)
    "rejected": {},  # terminal
    "on_hold": {"reject": "rejected"},  # resume handled below
}


def is_holdable(state: DealState) -> bool:
    """Whether a deal in ``state`` can be held (and resumed back into it)."""
    return state in _HOLDABLE


def advance(
    state: DealState, event: DealEvent, *, held_from: DealState | None = None
) -> DealState:
    """Return the next state for ``event`` from ``state``, or raise TransitionError.

    ``held_from`` is required only for resuming from ``on_hold``.
    """
    if state == "on_hold" and event == "resume":
        if held_from is None or not is_holdable(held_from):
            raise TransitionError(state, event)
        return held_from
    try:
        return _TRANSITIONS[state][event]
    except KeyError:
        raise TransitionError(state, event) from None
