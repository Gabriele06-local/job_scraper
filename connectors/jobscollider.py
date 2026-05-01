"""JobsCollider connector — disabled (category RSS feed returns 404)."""

from __future__ import annotations

from collections.abc import Iterator

import structlog

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class JobsColliderConnector(BaseConnector):
    source_name = "JobsCollider"
    source_type = SourceType.rss
    rate_limit_seconds = 1.0

    def fetch(self) -> Iterator[dict]:
        # Disabled: category-specific RSS feed returns 404.
        log.warning("jobscollider.disabled")
        return
        yield  # make it a generator
