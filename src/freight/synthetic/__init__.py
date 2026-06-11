"""Synthetic, labeled email corpus with ground truth.

Reused by the Phase 2 mock ``GmailClient`` (serves these as inbound emails) and the
Phase 3/9 extraction + injection-containment eval.
"""

from freight.synthetic.emails import SyntheticEmail, generate_dataset

__all__ = ["SyntheticEmail", "generate_dataset"]
