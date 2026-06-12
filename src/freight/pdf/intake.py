"""PDF text-layer extraction (no OCR) + the storage-read seam.

Text-layer only: a scanned/image PDF with NO text layer returns ``None``, which the
consumer routes to needs_review (review_reason='no_text_layer') — a content limitation,
never a crash or DLQ. OCR for scanned PDFs is a deferred future task.

Extracted text goes through the SAME extraction + validation path as email body text.
"""

import io
from typing import Protocol

from pypdf import PdfReader


class StorageReader(Protocol):
    """Reads attachment bytes by storage path. Real Supabase Storage impl is Phase 8."""

    def read(self, storage_path: str) -> bytes: ...


class UnconfiguredStorageReader:
    """Placeholder until Supabase Storage is wired (Phase 8).

    Body-only emails never call ``read``; a PDF arriving before Phase 8 raises (→ 5xx →
    retry/DLQ) rather than silently dropping the document.
    """

    def read(self, storage_path: str) -> bytes:
        raise NotImplementedError("Supabase Storage is wired at Phase 8")


def extract_text(pdf_bytes: bytes) -> str | None:
    """Return the PDF's text-layer content, or None if there is no extractable text."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    parts = [
        text
        for page in reader.pages
        if (text := (page.extract_text() or "").strip())
    ]
    combined = "\n".join(parts).strip()
    return combined or None
