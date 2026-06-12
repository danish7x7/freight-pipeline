"""Deal state machine: legal transitions allowed, skips rejected, resume → held_from."""

import pytest

from freight.deals import DealEvent, DealState, TransitionError, advance


@pytest.mark.parametrize(
    ("state", "event", "expected"),
    [
        ("new_enquiry", "quote_sent", "quoted"),
        ("quoted", "counter_received", "negotiating"),
        ("negotiating", "requote", "quoted"),  # negotiating ⇄ quoted
        ("quoted", "rc_received", "rc_received"),
        ("negotiating", "rc_received", "rc_received"),
        ("rc_received", "contract_signed", "contract_signed"),
        ("contract_signed", "scheduled", "scheduled"),
        # hold / reject reachable from active states
        ("new_enquiry", "hold", "on_hold"),
        ("contract_signed", "hold", "on_hold"),
        ("quoted", "reject", "rejected"),
        ("on_hold", "reject", "rejected"),
    ],
)
def test_legal_transitions(
    state: DealState, event: DealEvent, expected: DealState
) -> None:
    assert advance(state, event) == expected


@pytest.mark.parametrize(
    ("state", "event"),
    [
        ("new_enquiry", "scheduled"),  # skip the whole chain
        ("new_enquiry", "counter_received"),  # not quoted yet
        ("quoted", "contract_signed"),  # skip rc_received
        ("new_enquiry", "requote"),  # nothing to requote
        ("scheduled", "reject"),  # terminal
        ("rejected", "quote_sent"),  # terminal
    ],
)
def test_illegal_transitions_raise(state: DealState, event: DealEvent) -> None:
    with pytest.raises(TransitionError):
        advance(state, event)


def test_resume_returns_to_held_from() -> None:
    assert advance("on_hold", "resume", held_from="quoted") == "quoted"
    assert advance("on_hold", "resume", held_from="rc_received") == "rc_received"


def test_resume_without_held_from_raises() -> None:
    with pytest.raises(TransitionError):
        advance("on_hold", "resume")


def test_resume_into_non_holdable_state_raises() -> None:
    # Can't resume into a terminal/transient state.
    with pytest.raises(TransitionError):
        advance("on_hold", "resume", held_from="rejected")
    with pytest.raises(TransitionError):
        advance("on_hold", "resume", held_from="on_hold")


def test_hold_and_reject_reach_their_states() -> None:
    holdable: list[DealState] = [
        "new_enquiry",
        "quoted",
        "negotiating",
        "rc_received",
        "contract_signed",
    ]
    for state in holdable:
        assert advance(state, "hold") == "on_hold"
        assert advance(state, "reject") == "rejected"
