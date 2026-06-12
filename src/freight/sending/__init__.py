"""The human-gated send: the only path that triggers an outbound email."""

from freight.sending.service import SendError, SendResult, reject_deal, send_quote

__all__ = ["SendError", "SendResult", "reject_deal", "send_quote"]
