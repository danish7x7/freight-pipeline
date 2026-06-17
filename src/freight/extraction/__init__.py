"""Extraction: LLM produces untrusted structured JSON; a deterministic gate validates.

The LLM's only output is structured data — it can never emit anything that triggers an
action. Every field passes the deterministic (non-LLM) validation gate before it can
reach the rate engine. Validation is the injection defense, not the model's behavior.
"""

from freight.extraction.confidence import (
    REVIEW_THRESHOLD,
    ConfidenceOutcome,
    Route,
    score,
)
from freight.extraction.pipeline import ExtractionOutcome, extract
from freight.extraction.prompts import build_extraction_prompt
from freight.extraction.schema import (
    Accessorial,
    Equipment,
    Intent,
    RawExtraction,
    ValidatedExtraction,
)
from freight.extraction.validation import ValidationFailure, validate

__all__ = [
    "REVIEW_THRESHOLD",
    "Accessorial",
    "ConfidenceOutcome",
    "Equipment",
    "ExtractionOutcome",
    "Intent",
    "RawExtraction",
    "Route",
    "ValidatedExtraction",
    "ValidationFailure",
    "build_extraction_prompt",
    "extract",
    "score",
    "validate",
]
