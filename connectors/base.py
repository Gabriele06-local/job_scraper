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

    @abstractmethod
    def fetch(self) -> Iterator[dict]:
        """Yield raw job dicts from the source."""
