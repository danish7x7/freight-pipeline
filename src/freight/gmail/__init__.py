"""Real Gmail client (selected by config in ``factories``)."""

from freight.gmail.client import SCOPES, GmailApiClient

__all__ = ["SCOPES", "GmailApiClient"]
