"""Extraction schemas: the permissive LLM target and the canonical validated output."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Intent = Literal["rate_request", "negotiation", "rc", "contract", "other"]
# Closed allowlist of accessorial TYPES the engine prices. The LLM flags which apply;
# the per-type amount comes from the pricing_components row, never the model.
Accessorial = Literal["detention", "liftgate", "appointment", "chassis"]
Equipment = Literal[
    "dry_van", "reefer", "flatbed", "step_deck", "power_only", "container", "other"
]


class RawExtraction(BaseModel):
    """The permissive superset the LLM targets in ONE structured call.

    Intent + all fields come back together; fields irrelevant to the determined intent
    stay null. Everything here is UNTRUSTED until the deterministic gate runs — types
    are loose on purpose so malformed model output parses rather than crashing.
    """

    model_config = ConfigDict(extra="ignore")

    intent: str | None = None
    origin_city: str | None = None
    origin_state: str | None = None
    dest_city: str | None = None
    dest_state: str | None = None
    equipment: str | None = None
    weight_lbs: str | int | None = None
    mc_number: str | None = None
    # Loose (list[Any]) on purpose: a malformed ELEMENT must reach the deterministic
    # gate to be rejected per-element, not crash parsing. Each element is untrusted.
    accessorials: list[Any] | None = None


class ValidatedExtraction(BaseModel):
    """The canonical, typed output of the deterministic gate.

    The ONLY extraction type the rate engine (Phase 4) consumes. Route fields may be
    None (a valid extraction can be partial — e.g. a negotiation has no route);
    completeness drives confidence, not validity.
    """

    intent: Intent
    origin_city: str | None = None
    origin_state: str | None = None
    dest_city: str | None = None
    dest_state: str | None = None
    equipment: Equipment | None = None
    weight_lbs: int | None = None
    mc_number: str | None = None
    # Canonical accessorial types that survived the gate (None = absent; [] = none).
    accessorials: list[Accessorial] | None = None
