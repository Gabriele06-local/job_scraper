"""CLI-level crash recovery — a single broken connector must not abort the run.

SDD §A.6 / §A.9. We exercise `import_service.cli.cmd_import` end-to-end with
two fake connectors (one crashes on iter; the second yields one good record).
The first connector's `ImportRunRecord` should land in `import_runs` with
`connector_crashed=True` and `crash_reason` populated, *and* the second
connector's jobs should be processed normally.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Iterator
from unittest.mock import MagicMock

import mongomock
import pytest

from connectors.base import BaseConnector, SourceType


class _CrashingConnector(BaseConnector):
    source_name = "crashy"
    source_type = SourceType.api

    def fetch(self) -> Iterator[dict]:  # type: ignore[override]
        yield {
            "url": "https://crashy.example.com/job/1",
            "title": "Dev",
            "description": "x" * 250,
            "company": {"name": "Crashy"},
            "source": "crashy",
            "posted_at": datetime.now(tz=timezone.utc),
        }
        raise RuntimeError("simulated connector blow-up")


class _GoodConnector(BaseConnector):
    source_name = "good"
    source_type = SourceType.api

    def fetch(self) -> Iterator[dict]:  # type: ignore[override]
        yield {
            "url": "https://good.example.com/job/1",
            "title": "Senior Backend Engineer at Good Co",
            "description": (
                "We need a senior Python engineer to ship production services daily. "
                "You will own the API surface across our distributed platform. "
                "You will mentor junior engineers across the wider engineering team. "
                "We offer fully remote work with competitive total compensation."
            ),
            "company": {"name": "Good Co"},
            "source": "good",
            "posted_at": datetime.now(tz=timezone.utc),
        }


@pytest.fixture
def fake_db() -> mongomock.Database:
    return mongomock.MongoClient()["devboards_test"]


def test_crashing_connector_does_not_abort_run(fake_db, monkeypatch) -> None:
    from import_service import cli as cli_mod
    from pipeline import orchestrator as orch

    # Make `get_db / get_jobs / get_companies / ensure_indexes` use mongomock.
    monkeypatch.setattr(cli_mod, "ensure_indexes", lambda: None)
    monkeypatch.setattr(cli_mod, "get_jobs", lambda: fake_db["jobs"])
    monkeypatch.setattr("database.repository.get_db", lambda: fake_db)
    monkeypatch.setattr("database.repository.get_companies", lambda: fake_db["companies"])

    # Replace connector registry with our pair.
    monkeypatch.setattr(
        "connectors.get_enabled_connectors",
        lambda: [_CrashingConnector(), _GoodConnector()],
    )

    # Stub URL validator to avoid real network.
    monkeypatch.setattr(
        "pipeline.url_validator.build_default",
        lambda: MagicMock(validate_many=lambda urls: _coroutine({})),
    )

    # Inject a classifier that always returns a high-confidence valid result.
    from models.job import (
        EmploymentType,
        JobClassification,
        RemoteMode,
        RoleFamily,
        Seniority,
    )

    real_classification = JobClassification(
        technical_skills=["Python", "Django", "Postgres", "AWS"],
        seniority=Seniority.SENIOR,
        role_family=RoleFamily.BACKEND,
        remote_mode=RemoteMode.REMOTE,
        employment_type=EmploymentType.FULL_TIME,
        salary_min=80000,
        salary_max=120000,
        currency="EUR",
        ai_confidence=0.92,
        quality_flags=["clear_jd", "has_requirements"],
    )

    orig_init = orch.ImportPipeline.__init__

    def _patched_init(self, *args, **kwargs):
        kwargs.setdefault("classifier", MagicMock(classify=lambda *a, **k: real_classification))
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(orch.ImportPipeline, "__init__", _patched_init)

    args = argparse.Namespace(
        dry_run=False,
        limit=0,
        limit_per_connector=0,
        connectors="",
    )
    rc = cli_mod.cmd_import(args)
    assert rc == 0

    # Crashing connector recorded with the crash flag.
    runs = list(fake_db["import_runs"].find({"provider_name": "crashy"}))
    assert len(runs) == 1
    assert runs[0]["connector_crashed"] is True
    assert "RuntimeError" in (runs[0]["crash_reason"] or "")
    # And good connector finished cleanly.
    good_runs = list(fake_db["import_runs"].find({"provider_name": "good"}))
    assert len(good_runs) == 1
    assert good_runs[0]["connector_crashed"] is False
    # Report doc exists and references both sources.
    reports = list(fake_db["import_reports"].find())
    assert len(reports) == 1
    assert reports[0]["total_sources"] == 2


# ---------------------------------------------------------------------------
# Small async glue: build_default().validate_many is awaited, so the stub
# returned by build_default must return an awaitable. We can't use async
# lambdas in 3.8+ without trickery — wrap a sync dict in a coroutine.
# ---------------------------------------------------------------------------


async def _coroutine(value):
    return value
