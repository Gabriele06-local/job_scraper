"""TechMap connector — disabled (API spec incomplete, bearer token unverified)."""

from __future__ import annotations

from collections.abc import Iterator

import structlog

from .base import BaseConnector, SourceType

log = structlog.get_logger(__name__)


class TechMapConnector(BaseConnector):
    source_name = "TechMap"
    source_type = SourceType.api
    rate_limit_seconds = 1.0

    def fetch(self) -> Iterator[dict]:
        # Disabled: API endpoint and auth spec not verified.
        log.warning("techmap.disabled")
        return
        yield  # make it a generator
