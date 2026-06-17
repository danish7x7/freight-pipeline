"""The deterministic validation gate — the injection defense.

ALLOWLIST-REJECT for security-relevant fields: anything that doesn't match the
allowlist / format / range is REJECTED (→ review), never "sanitized into" a safe value.
Canonicalization is applied only for known-good normalization (case/whitespace, a
bounded equipment synonym → enum). We never strip injection out of a field and keep the
remainder — a field carrying injection punctuation is rejected outright.

No LLM output reaches this module's output except as a value that survived the gate.
"""

import re
from dataclasses import dataclass

from freight.extraction.schema import (
    Equipment,
    Intent,
    RawExtraction,
    ValidatedExtraction,
)

_INTENTS: frozenset[str] = frozenset(
    {"rate_request", "negotiation", "rc", "contract", "other"}
)

# Full state name -> USPS abbreviation (known-good canonicalization input).
_US_STATE_NAMES: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}
_US_STATES: frozenset[str] = frozenset(_US_STATE_NAMES.values())

# City: a name, not free text. Reject injection punctuation / newlines / over-length.
_CITY_RE = re.compile(r"^[A-Za-z .,'\-]{1,64}$")
# Equipment: bounded, name-like; rejected before keyword canonicalization if it carries
# anything but letters/digits/space/'/"/./-/_ (so "reefer; DROP" is rejected, not kept,
# while the canonical "dry_van" and natural "53' reefer" both pass).
_EQUIPMENT_RE = re.compile(r"^[A-Za-z0-9 '\"._-]{1,32}$")
# Weight: digits with optional commas and an optional lbs suffix — nothing else.
_WEIGHT_RE = re.compile(r"^([\d,]+)\s*(lbs?\.?)?$")

_MIN_WEIGHT_LBS = 1
_MAX_WEIGHT_LBS = 80_000  # legal gross max ballpark; reject above as implausible


@dataclass(frozen=True)
class ValidationFailure:
    """Why a raw extraction was rejected (routes the email to needs_review)."""

    reasons: list[str]


def validate(raw: RawExtraction) -> ValidatedExtraction | ValidationFailure:
    """Validate raw LLM output: the canonical record, or a failure with reasons."""
    reasons: list[str] = []
    intent = _validate_intent(raw.intent, reasons)
    origin_state = _validate_state(raw.origin_state, "origin_state", reasons)
    dest_state = _validate_state(raw.dest_state, "dest_state", reasons)
    origin_city = _validate_city(raw.origin_city, "origin_city", reasons)
    dest_city = _validate_city(raw.dest_city, "dest_city", reasons)
    equipment = _validate_equipment(raw.equipment, reasons)
    weight = _validate_weight(raw.weight_lbs, reasons)
    mc_number = _validate_mc(raw.mc_number)

    if reasons or intent is None:
        return ValidationFailure(reasons=reasons)
    return ValidatedExtraction(
        intent=intent,
        origin_city=origin_city,
        origin_state=origin_state,
        dest_city=dest_city,
        dest_state=dest_state,
        equipment=equipment,
        weight_lbs=weight,
        mc_number=mc_number,
    )


def _validate_intent(value: str | None, reasons: list[str]) -> Intent | None:
    if value is None or not value.strip():
        reasons.append("missing_intent")
        return None
    norm = value.strip().lower()
    if norm in _INTENTS:
        return norm  # type: ignore[return-value]  # narrowed by the allowlist
    reasons.append(f"invalid_intent:{value!r}")
    return None


def _validate_state(value: str | None, field: str, reasons: list[str]) -> str | None:
    if value is None or not value.strip():
        return None  # absent is allowed
    stripped = value.strip()
    if stripped.upper() in _US_STATES:
        return stripped.upper()
    full = _US_STATE_NAMES.get(stripped.lower())
    if full is not None:
        return full
    reasons.append(f"invalid_{field}:{value!r}")
    return None


def _validate_city(value: str | None, field: str, reasons: list[str]) -> str | None:
    if value is None or not value.strip():
        return None
    stripped = value.strip()
    if not _CITY_RE.match(stripped):
        reasons.append(f"invalid_{field}:{value!r}")
        return None
    return stripped


def _validate_equipment(value: str | None, reasons: list[str]) -> Equipment | None:
    if value is None or not value.strip():
        return None
    stripped = value.strip()
    if not _EQUIPMENT_RE.match(stripped):
        reasons.append(f"invalid_equipment:{value!r}")
        return None
    canon = _canon_equipment(stripped.lower())
    if canon is not None:
        return canon
    reasons.append(f"invalid_equipment:{value!r}")
    return None


def _canon_equipment(key: str) -> Equipment | None:
    # Keyword canonicalization over an already format-gated value (so e.g. "53' reefer"
    # and "refrigerated" map to reefer, but "reefer; DROP" was already rejected above).
    if "reefer" in key or "refriger" in key:
        return "reefer"
    if "flat" in key:
        return "flatbed"
    if "step" in key:
        return "step_deck"
    if "power" in key:
        return "power_only"
    if "container" in key or "dray" in key or "intermodal" in key:
        return "container"
    if "van" in key or "dry" in key:
        return "dry_van"
    return None


def _validate_mc(value: str | None) -> str | None:
    """Normalize an MC number, or drop it (None) if malformed.

    A malformed MC is dropped, NOT a hard reject of the whole extraction: the deal then
    proceeds as if no MC was given (the fork-2 default), and the carriers table is the
    real allowlist — the eligibility gate maps an unknown MC to on_hold.
    """
    if value is None or not value.strip():
        return None
    cleaned = value.strip().upper().replace(" ", "")
    match = re.fullmatch(r"(?:MC)?(\d{4,8})", cleaned)
    return f"MC{match.group(1)}" if match is not None else None


def _validate_weight(value: str | int | None, reasons: list[str]) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        number = value
    else:
        match = _WEIGHT_RE.match(value.strip().lower())
        if match is None:
            reasons.append(f"invalid_weight:{value!r}")
            return None
        number = int(match.group(1).replace(",", ""))
    if not (_MIN_WEIGHT_LBS <= number <= _MAX_WEIGHT_LBS):
        reasons.append(f"weight_out_of_range:{number}")
        return None
    return number
