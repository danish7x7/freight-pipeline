"""The carrier MC eligibility gate.

Runs before `quoted` and `contract_signed` (CLAUDE.md). Behavior:
- NO MC identified (None/empty) → eligible. A rate enquiry commonly has no carrier MC
  yet; it proceeds, and the gate is re-run before contract_signed (carrier onboarding).
- MC active → eligible.
- MC blocked, table-status 'unknown', or not found → on_hold for a human.

The DB lookup is parameterized, so an injection-laden MC simply fails to match and
lands in on_hold (the safe default) — no special-casing needed.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from freight.db.repository import CarrierRecord

Eligibility = Literal["eligible", "on_hold"]


@dataclass(frozen=True)
class EligibilityDecision:
    """The gate's verdict and (when held) the reason."""

    eligibility: Eligibility
    reason: str | None  # "blocked_carrier" | "unknown_carrier" | None


class _CarrierLookup(Protocol):
    def get_carrier_by_mc(self, mc_number: str) -> CarrierRecord | None: ...


def evaluate(mc_number: str | None, repo: _CarrierLookup) -> EligibilityDecision:
    """Decide eligibility for a (possibly absent) carrier MC number."""
    if mc_number is None or not mc_number.strip():
        return EligibilityDecision("eligible", None)

    carrier = repo.get_carrier_by_mc(mc_number.strip())
    if carrier is None:
        return EligibilityDecision("on_hold", "unknown_carrier")
    if carrier.status == "active":
        return EligibilityDecision("eligible", None)
    if carrier.status == "blocked":
        return EligibilityDecision("on_hold", "blocked_carrier")
    return EligibilityDecision("on_hold", "unknown_carrier")
