"""Application database access (SQLAlchemy Core).

The app reads/writes the DB but does NOT own the schema — migrations in
``supabase/migrations`` are the single source of truth. Writes use a privileged
(service-role) connection that bypasses RLS, consistent with the Phase 1
server-side-write-only boundary.
"""

from freight.db.repository import (
    AttachmentRecord,
    CarrierRecord,
    EmailRecord,
    IngestRepository,
    LaneRate,
    RateKey,
    RateRecord,
    make_engine,
)

__all__ = [
    "AttachmentRecord",
    "CarrierRecord",
    "EmailRecord",
    "IngestRepository",
    "LaneRate",
    "RateKey",
    "RateRecord",
    "make_engine",
]
