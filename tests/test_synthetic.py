"""The synthetic corpus is well-formed, labeled, and deterministic."""

from collections import Counter

from freight.interfaces.types import InboundMessage
from freight.synthetic import SyntheticEmail, generate_dataset


def test_dataset_shape_and_category_split() -> None:
    data = generate_dataset()
    assert len(data) == 12
    assert dict(Counter(s.category for s in data)) == {
        "normal": 4,
        "malformed": 4,
        "adversarial": 4,
    }


def test_ids_unique_and_messages_are_boundary_type() -> None:
    data = generate_dataset()
    ids = [s.message.gmail_message_id for s in data]
    assert len(set(ids)) == len(ids)
    assert all(isinstance(s.message, InboundMessage) for s in data)


def test_dataset_is_deterministic() -> None:
    assert generate_dataset() == generate_dataset()


def test_adversarial_samples_are_labeled_with_true_intent() -> None:
    adversarial = [s for s in generate_dataset() if s.category == "adversarial"]
    assert adversarial
    for sample in adversarial:
        assert sample.is_adversarial is True
        assert sample.injection_technique
        # Ground truth is the TRUE intent, never the attacker's instruction.
        assert sample.expected_intent in {
            "rate_request",
            "negotiation",
            "rc",
            "contract",
            "other",
        }


def test_non_adversarial_samples_are_not_flagged() -> None:
    for sample in generate_dataset():
        if sample.category != "adversarial":
            assert sample.is_adversarial is False
            assert sample.injection_technique is None


def test_normal_samples_carry_expected_fields() -> None:
    normal = [s for s in generate_dataset() if s.category == "normal"]
    # At least the rate request should expose a full structured route.
    rate_requests = [s for s in normal if s.expected_intent == "rate_request"]
    assert rate_requests
    fields = rate_requests[0].expected_fields
    for key in ("origin_city", "origin_state", "dest_city", "dest_state", "equipment"):
        assert key in fields


def test_synthetic_email_is_pydantic() -> None:
    sample = generate_dataset()[0]
    assert isinstance(sample, SyntheticEmail)
