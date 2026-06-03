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

from .active_jobs_db import ActiveJobsDbConnector
from .adzuna import AdzunaConnector
from .arbeitnow import ArbeitnowConnector
from .careerjet import CareerJetConnector
from .ashby import AshbyConnector
from .base import BaseConnector, SourceType
from .faang_watch import FaangWatchConnector
from .greenhouse import GreenhouseConnector
from .himalayas import HimalayasConnector
from .hn_hiring import HNHiringConnector
from .hn_realtime import HNRealtimeConnector
from .iprogrammatori import IProgrammatoriConnector
from .jobicy import JobicyConnector
from .jooble import JoobleConnector
from .jsearch import JSearchConnector
from .lever import LeverConnector
from .personio import PersonioConnector
from .reed import ReedConnector
from .remoteok import RemoteOKConnector
from .remotive import RemotiveConnector
from .rss import RSSConnector
from .startup_jobs import StartupJobsConnector
from .themuse import TheMuseConnector
from .workday_jobs import WorkdayJobsConnector
from .yc_jobs import YCJobsConnector

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
    "adzuna": ConnectorEntry(cls=AdzunaConnector, enabled=True),
    "careerjet": ConnectorEntry(cls=CareerJetConnector, enabled=True),
    "jooble": ConnectorEntry(cls=JoobleConnector, enabled=True),
    "iprogrammatori": ConnectorEntry(cls=IProgrammatoriConnector, enabled=True),
    "arbeitnow": ConnectorEntry(cls=ArbeitnowConnector, enabled=True),
    "remoteok": ConnectorEntry(cls=RemoteOKConnector, enabled=True),
    "jobicy": ConnectorEntry(cls=JobicyConnector, enabled=True),
    "rss": ConnectorEntry(cls=RSSConnector, enabled=True),
    "himalayas": ConnectorEntry(cls=HimalayasConnector, enabled=True),
    "remotive": ConnectorEntry(cls=RemotiveConnector, enabled=True),
    "themuse": ConnectorEntry(cls=TheMuseConnector, enabled=True),
    "reed": ConnectorEntry(cls=ReedConnector, enabled=True),
    "jsearch": ConnectorEntry(cls=JSearchConnector, enabled=True),
    "greenhouse": ConnectorEntry(cls=GreenhouseConnector, enabled=True),
    "lever": ConnectorEntry(cls=LeverConnector, enabled=True),
    "ashby": ConnectorEntry(cls=AshbyConnector, enabled=True),
    "personio": ConnectorEntry(cls=PersonioConnector, enabled=True),
    # New RapidAPI connectors — code-level enabled, DB-gated (disabled by
    # default in seed_providers; enable per-slug from the backoffice).
    "active_jobs_db": ConnectorEntry(cls=ActiveJobsDbConnector, enabled=True),
    "workday_jobs": ConnectorEntry(cls=WorkdayJobsConnector, enabled=True),
    "startup_jobs": ConnectorEntry(cls=StartupJobsConnector, enabled=True),
    "hn_hiring": ConnectorEntry(cls=HNHiringConnector, enabled=True),
    "yc_jobs": ConnectorEntry(cls=YCJobsConnector, enabled=True),
    "faang_watch": ConnectorEntry(cls=FaangWatchConnector, enabled=True),
    "hn_realtime": ConnectorEntry(cls=HNRealtimeConnector, enabled=True),
}


def get_enabled_connectors() -> list[BaseConnector]:
    """Instantiate and return enabled connectors.

    Gate order (highest priority first):
      1. `settings.disabled_connectors` env override — skip if listed.
      2. `providers.enabled` flag in MongoDB — skip if False. Un-seeded slugs
         fall back to a legacy whitelist (see `is_provider_enabled`).
      3. `REGISTRY[name].enabled` code-level flag — skip if False
         (kept as a hard kill-switch for known-broken connectors).
    Logs a warning if instantiation fails; skips that connector.
    """
    # Local import to keep this module importable in unit tests that don't
    # have MongoDB available; the DB call only happens at run time.
    from database.repository import is_provider_enabled

    runtime_disabled = {n.lower() for n in settings.disabled_connectors}
    result: list[BaseConnector] = []

    for name, entry in REGISTRY.items():
        if name in runtime_disabled:
            log.info("connector.runtime_disabled", name=name)
            continue
        if not is_provider_enabled(name):
            log.info("connector.db_disabled", name=name)
            continue
        if not entry.enabled:
            log.debug(
                "connector.skipped",
                name=name,
                reason=entry.disabled_reason,
            )
            continue
        try:
            instance = entry.cls()
            # REGISTRY key is the canonical slug — stamp it on the instance so
            # telemetry (import_runs.provider_slug) and the providers catalog
            # share one identifier.
            instance.slug = name
            result.append(instance)
        except Exception as exc:
            log.warning("connector.init_failed", name=name, error=str(exc))

    return result


__all__ = [
    "ActiveJobsDbConnector",
    "AdzunaConnector",
    "ArbeitnowConnector",
    "AshbyConnector",
    "BaseConnector",
    "CareerJetConnector",
    "ConnectorEntry",
    "FaangWatchConnector",
    "GreenhouseConnector",
    "HimalayasConnector",
    "HNHiringConnector",
    "HNRealtimeConnector",
    "IProgrammatoriConnector",
    "JobicyConnector",
    "JoobleConnector",
    "JSearchConnector",
    "LeverConnector",
    "PersonioConnector",
    "REGISTRY",
    "ReedConnector",
    "RemoteOKConnector",
    "RemotiveConnector",
    "RSSConnector",
    "SourceType",
    "StartupJobsConnector",
    "TheMuseConnector",
    "WorkdayJobsConnector",
    "YCJobsConnector",
    "get_enabled_connectors",
]
