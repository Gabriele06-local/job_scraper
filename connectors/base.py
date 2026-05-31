"""BaseConnector ABC — common interface for all job source connectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from enum import Enum


class SourceType(str, Enum):
    api = "api"
    rss = "rss"
    html = "html"


class BaseConnector(ABC):
    """Abstract base for all job source connectors.

    Subclasses must set class-level attributes and implement fetch().
    """

    source_name: str
    source_type: SourceType
    rate_limit_seconds: float = 1.0
    # Canonical identifier, assigned from the REGISTRY key at instantiation by
    # `get_enabled_connectors` (single source of truth — connectors never
    # hardcode it). Written to `import_runs.provider_slug` so telemetry joins
    # the providers catalog by slug instead of the display name.
    slug: str = ""

    @abstractmethod
    def fetch(self) -> Iterator[dict]:
        """Yield raw job dicts from the source."""
