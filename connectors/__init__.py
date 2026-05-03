"""Connector registry — single source of truth for enabled/disabled connectors.

Usage:
    from connectors import get_enabled_connectors
    for connector in get_enabled_connectors():
        for job in connector.fetch():
            ...

Set DISABLED_CONNECTORS env var (comma-separated names) to disable at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from config import settings

from .adzuna import AdzunaConnector
from .arbeitnow import ArbeitnowConnector
from .base import BaseConnector, SourceType
from .iprogrammatori import IProgrammatoriConnector
from .jobicy import JobicyConnector
from .jobisjob import JobisJobConnector
from .jobscollider import JobsColliderConnector
from .jooble import JoobleConnector
from .linkedin import LinkedInConnector
from .remoteok import RemoteOKConnector
from .reteinformaticalavoro import ReteInformaticaLavoroConnector
from .rss import RSSConnector
from .techmap import TechMapConnector

if TYPE_CHECKING:
    pass

log = structlog.get_logger(__name__)


@dataclass
class ConnectorEntry:
    """Registry entry for a single connector."""

    cls: type[BaseConnector]
    enabled: bool = True
    disabled_reason: str = ""


# Static registry — enabled flag reflects code-level known-good state.
# Runtime override via settings.disabled_connectors.
REGISTRY: dict[str, ConnectorEntry] = {
    "linkedin": ConnectorEntry(cls=LinkedInConnector, enabled=True),
    "adzuna": ConnectorEntry(cls=AdzunaConnector, enabled=True),
    "jooble": ConnectorEntry(cls=JoobleConnector, enabled=True),
    "jobisjob": ConnectorEntry(cls=JobisJobConnector, enabled=True),
    "iprogrammatori": ConnectorEntry(cls=IProgrammatoriConnector, enabled=True),
    "arbeitnow": ConnectorEntry(cls=ArbeitnowConnector, enabled=True),
    "remoteok": ConnectorEntry(cls=RemoteOKConnector, enabled=True),
    "jobicy": ConnectorEntry(cls=JobicyConnector, enabled=True),
    "reteinformaticalavoro": ConnectorEntry(
        cls=ReteInformaticaLavoroConnector, enabled=True
    ),
    "rss": ConnectorEntry(cls=RSSConnector, enabled=True),
    # Disabled — issues tracked in docs/reports/02-connectors-status.md
    "techmap": ConnectorEntry(
        cls=TechMapConnector,
        enabled=False,
        disabled_reason="API spec incomplete; bearer token unverified",
    ),
    "jobscollider": ConnectorEntry(
        cls=JobsColliderConnector,
        enabled=False,
        disabled_reason="Category RSS feed returns 404",
    ),
}


def get_enabled_connectors() -> list[BaseConnector]:
    """Instantiate and return enabled connectors.

    Respects settings.disabled_connectors for runtime overrides.
    Logs a warning if instantiation fails; skips that connector.
    """
    runtime_disabled = {n.lower() for n in settings.disabled_connectors}
    result: list[BaseConnector] = []

    for name, entry in REGISTRY.items():
        if not entry.enabled:
            log.debug(
                "connector.skipped",
                name=name,
                reason=entry.disabled_reason,
            )
            continue
        if name in runtime_disabled:
            log.info("connector.runtime_disabled", name=name)
            continue
        try:
            result.append(entry.cls())
        except Exception as exc:
            log.warning("connector.init_failed", name=name, error=str(exc))

    return result


__all__ = [
    "BaseConnector",
    "ConnectorEntry",
    "REGISTRY",
    "SourceType",
    "get_enabled_connectors",
]
