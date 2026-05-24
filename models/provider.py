"""ProviderConfig — DB-backed enable/disable flag for each connector slug."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    """Backoffice-managed config for a single connector slug.

    The `slug` matches a key in `connectors.REGISTRY`. The `enabled` flag
    is the runtime gate consulted by `get_enabled_connectors()`.
    """

    slug: str
    name: str
    enabled: bool = False
    source_url: str = ""
    notes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
