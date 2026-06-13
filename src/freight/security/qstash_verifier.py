"""The QStash signature-verification seam.

`/ingest` is QStash's push target and must reject anything that isn't a validly
signed QStash delivery. Verification is the auth boundary, so it is NOT hand-rolled:
the SDK-backed implementation delegates to the official ``qstash.Receiver`` (PyJWT
HS256 + body-hash check). Call sites depend only on the ``QStashVerifier`` Protocol;
Phase 8 swaps real signing keys and the real public URL behind ``build_qstash_verifier``
without touching the route.

The Protocol is expressed in **raw bytes** (the exact request body), because the
signed body-hash claim is computed over the raw bytes — any JSON re-serialization
would change the hash. The SDK wants a ``str``, so the impl decodes utf-8 at that
boundary only.
"""

from typing import Protocol, runtime_checkable

from qstash import Receiver
from qstash.errors import SignatureError

from freight.config import Settings

__all__ = [
    "QStashVerifier",
    "SDKQStashVerifier",
    "SignatureError",
    "build_qstash_verifier",
]


@runtime_checkable
class QStashVerifier(Protocol):
    """Verify that a request body carries a valid QStash signature.

    Returns ``None`` when the signature is valid; raises (``SignatureError`` for a
    genuine signature rejection, or any other exception on misconfiguration) when it
    is not. Implementations never return a bool — absence of a raise is the only
    success signal, so a caller cannot accidentally fail open.
    """

    def verify(self, *, body: bytes, signature: str) -> None: ...


class SDKQStashVerifier:
    """``QStashVerifier`` backed by the official ``qstash.Receiver``.

    The Receiver itself tries the current signing key, then the next, so a key
    rotation does not reject in-flight deliveries.
    """

    def __init__(self, receiver: Receiver, *, expected_url: str | None) -> None:
        self._receiver = receiver
        # The signed `sub` claim. None => the SDK does not match sub (the claim must
        # still be present). Set to the public /ingest URL in real deployments.
        self._expected_url = expected_url

    def verify(self, *, body: bytes, signature: str) -> None:
        # The Receiver hashes ``body.encode()``; QStash signed over the raw bytes, so
        # decode the exact bytes back to the str it expects (utf-8 round-trips).
        self._receiver.verify(
            signature=signature,
            body=body.decode("utf-8"),
            url=self._expected_url,
        )


def build_qstash_verifier(settings: Settings) -> SDKQStashVerifier:
    """Construct the SDK-backed verifier from config (the swap point for Phase 8)."""
    receiver = Receiver(
        current_signing_key=settings.qstash_current_signing_key,
        next_signing_key=settings.qstash_next_signing_key,
    )
    return SDKQStashVerifier(
        receiver,
        expected_url=settings.qstash_expected_url or None,
    )
