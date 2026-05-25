"""Tests for pipeline/expiration.py and import_service/cli.py scheduler commands."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pipeline.expiration import (
    ExpirationChecker,
    ExpirationResult,
    ExpirationVerdict,
    _body_matches_expiry,
    _redirect_is_expired,
)
from pipeline.expiration_patterns import EXPIRY_BODY_PATTERNS

_NOW = datetime.now(tz=timezone.utc)
_OLD = _NOW - timedelta(days=70)
_RECENT = _NOW - timedelta(hours=30)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _active_doc(
    url: str = "https://jobs.example.com/1",
    source: str = "test_source",
    posted_at: datetime | None = None,
    last_probed_at: datetime | None = None,
    status: str = "valid",
) -> dict:
    return {
        "_id": url,
        "url": url,
        "source": source,
        "status": status,
        "posted_at": posted_at or _RECENT,
        "last_probed_at": last_probed_at,
    }


def _mock_response(status_code: int, headers: dict | None = None, text: str = "") -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.headers = httpx.Headers(headers or {})
    r.text = text
    return r


# ---------------------------------------------------------------------------
# Unit: verdict helpers
# ---------------------------------------------------------------------------


class TestRedirectIsExpired:
    def test_expired_path_fragment(self):
        assert _redirect_is_expired("https://example.com/expired?ref=123")

    def test_annuncio_scaduto(self):
        assert _redirect_is_expired("https://example.it/annuncio-scaduto/123")

    def test_oferta_cerrada(self):
        assert _redirect_is_expired("https://example.es/oferta-cerrada/456")

    def test_benign_redirect(self):
        assert not _redirect_is_expired("https://example.com/jobs/senior-dev-123")

    def test_empty_location(self):
        assert not _redirect_is_expired("")


class TestBodyMatchesExpiry:
    def test_english_no_longer_available(self):
        # SDD §A.2 — phrase library uses "this position is no longer available"
        assert _body_matches_expiry("any_source", "This position is no longer available")

    def test_english_position_filled(self):
        assert _body_matches_expiry("any_source", "This role has been filled now.")

    def test_italian_annuncio_scaduto(self):
        assert _body_matches_expiry("any_source", "Annuncio scaduto - non disponibile")

    def test_french_offre_expiree(self):
        assert _body_matches_expiry("any_source", "Cette offre expirée depuis 3 jours")

    def test_spanish_oferta_cerrada(self):
        assert _body_matches_expiry("any_source", "Oferta cerrada el 01/05/2026")

    def test_german_stelle_nicht_mehr(self):
        assert _body_matches_expiry("any_source", "Diese stelle nicht mehr verfügbar")

    def test_long_body_no_match(self):
        long_body = "a" * 600  # > 500 chars, no pattern
        assert not _body_matches_expiry("any_source", long_body)

    def test_normal_job_listing(self):
        body = (
            "We are hiring a Senior Python Developer. You will build scalable APIs. "
            "Requirements: 5 years Python, REST experience."
        )
        assert not _body_matches_expiry("any_source", body)

    def test_per_source_pattern_takes_precedence(self, monkeypatch):
        import re

        monkeypatch.setitem(
            EXPIRY_BODY_PATTERNS, "my_source", [re.compile(r"custom_dead_signal", re.I)]
        )
        assert _body_matches_expiry("my_source", "Found custom_dead_signal here")
        long_body = "This job is no longer available (long body " + "x" * 600 + ")"
        assert not _body_matches_expiry("my_source", long_body)


# ---------------------------------------------------------------------------
# Unit: ExpirationChecker._probe (mocked httpx)
# ---------------------------------------------------------------------------


class TestExpirationCheckerProbe:
    @pytest.fixture
    def checker(self, jobs_collection) -> ExpirationChecker:
        return ExpirationChecker(jobs_col=jobs_collection, dry_run=True)

    def _run_probe(self, checker, url, source, response) -> ExpirationVerdict:
        async def _inner():
            async with httpx.AsyncClient() as client:
                with patch.object(client, "head", new=AsyncMock(return_value=response)):
                    return await checker._probe(url, source, client)

        return asyncio.run(_inner())

    def test_404_expired(self, checker):
        r = _mock_response(404)
        verdict = self._run_probe(checker, "https://a.com/job/1", "src", r)
        assert verdict.expired is True
        assert "404" in verdict.reason

    def test_410_expired(self, checker):
        r = _mock_response(410)
        verdict = self._run_probe(checker, "https://a.com/job/2", "src", r)
        assert verdict.expired is True
        assert "410" in verdict.reason

    def test_451_expired(self, checker):
        r = _mock_response(451)
        verdict = self._run_probe(checker, "https://a.com/job/3", "src", r)
        assert verdict.expired is True

    def test_200_alive(self, checker):
        r = _mock_response(200)
        verdict = self._run_probe(checker, "https://a.com/job/4", "src", r)
        assert verdict.expired is False

    def test_500_transient(self, checker):
        r = _mock_response(500)
        verdict = self._run_probe(checker, "https://a.com/job/5", "src", r)
        assert verdict.expired is None

    def test_503_transient(self, checker):
        r = _mock_response(503)
        verdict = self._run_probe(checker, "https://a.com/job/6", "src", r)
        assert verdict.expired is None

    def test_redirect_expired_location(self, checker):
        r = _mock_response(301, headers={"location": "https://a.com/job-not-found"})
        verdict = self._run_probe(checker, "https://a.com/job/7", "src", r)
        assert verdict.expired is True
        assert verdict.reason == "EXPIRED_REDIRECT"

    def test_redirect_benign(self, checker):
        r = _mock_response(301, headers={"location": "https://a.com/jobs/senior-dev"})
        verdict = self._run_probe(checker, "https://a.com/job/8", "src", r)
        assert verdict.expired is False

    def test_200_with_body_pattern_source_in_allowlist(self, checker, monkeypatch):
        import re

        source = "special_board"
        monkeypatch.setitem(EXPIRY_BODY_PATTERNS, source, [re.compile(r"job closed", re.I)])
        head_r = _mock_response(200)
        get_r = _mock_response(200, text="Sorry, this job closed last month.")

        async def _inner():
            async with httpx.AsyncClient() as client:
                with patch.object(client, "head", new=AsyncMock(return_value=head_r)):
                    with patch.object(client, "get", new=AsyncMock(return_value=get_r)):
                        return await checker._probe("https://a.com/j/1", source, client)

        verdict = asyncio.run(_inner())
        assert verdict.expired is True
        assert verdict.reason == "EXPIRED_PATTERN"

    def test_http_error_returns_transient(self, checker):
        async def _inner():
            async with httpx.AsyncClient() as client:
                with patch.object(
                    client,
                    "head",
                    new=AsyncMock(side_effect=httpx.ConnectError("timeout")),
                ):
                    url = "https://unreachable.example.com/job/1"
                    return await checker._probe(url, "src", client)

        verdict = asyncio.run(_inner())
        assert verdict.expired is None


# ---------------------------------------------------------------------------
# Integration: ExpirationChecker.run with mongomock
# ---------------------------------------------------------------------------


class TestExpirationCheckerRun:
    def _insert(self, col, docs: list[dict]) -> None:
        col.insert_many(docs)

    def _run(self, col, dry_run: bool = False, **kwargs) -> ExpirationResult:
        checker = ExpirationChecker(jobs_col=col, dry_run=dry_run)
        with patch("pipeline.expiration.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            # All probes return 404 (expired) by default
            mock_client.head = AsyncMock(return_value=_mock_response(404))
            return asyncio.run(checker.run(**kwargs))

    def test_no_candidates_returns_empty_result(self, jobs_collection):
        result = self._run(jobs_collection)
        assert result.counters.total == 0
        assert result.counters.probed == 0

    def test_active_job_probed_and_expired(self, jobs_collection):
        self._insert(jobs_collection, [_active_doc()])
        result = self._run(jobs_collection)
        assert result.counters.probed == 1
        assert result.counters.expired == 1
        doc = jobs_collection.find_one({"_id": "https://jobs.example.com/1"})
        assert doc["status"] == "expired"
        assert doc["reject_reason"] == "EXPIRED_404"

    def test_dry_run_no_db_writes(self, jobs_collection):
        self._insert(jobs_collection, [_active_doc()])
        result = self._run(jobs_collection, dry_run=True)
        assert result.counters.probed == 1
        doc = jobs_collection.find_one({"_id": "https://jobs.example.com/1"})
        # Status unchanged
        assert doc["status"] == "valid"

    def test_already_probed_within_24h_skipped(self, jobs_collection):
        doc = _active_doc(last_probed_at=datetime.now(tz=timezone.utc) - timedelta(hours=1))
        self._insert(jobs_collection, [doc])
        result = self._run(jobs_collection)
        assert result.counters.total == 0

    def test_max_age_jobs_expired_without_probe(self, jobs_collection):
        old_doc = _active_doc(url="https://old.example.com/1", posted_at=_OLD)
        self._insert(jobs_collection, [old_doc])
        result = self._run(jobs_collection, max_age_days=60)
        assert result.counters.max_age_expired == 1
        doc = jobs_collection.find_one({"_id": "https://old.example.com/1"})
        assert doc["status"] == "expired"
        assert doc["reject_reason"] == "EXPIRED_MAX_AGE"

    def test_alive_job_stays_active(self, jobs_collection):
        self._insert(jobs_collection, [_active_doc()])
        checker = ExpirationChecker(jobs_col=jobs_collection)
        with patch("pipeline.expiration.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.head = AsyncMock(return_value=_mock_response(200))
            result = asyncio.run(checker.run())
        assert result.counters.alive == 1
        doc = jobs_collection.find_one({"_id": "https://jobs.example.com/1"})
        assert doc["status"] == "valid"

    def test_limit_respected(self, jobs_collection):
        docs = [_active_doc(url=f"https://jobs.example.com/{i}") for i in range(10)]
        self._insert(jobs_collection, docs)
        result = self._run(jobs_collection, limit=3)
        assert result.counters.total == 3

    def test_transient_sets_last_probed_at_only(self, jobs_collection):
        self._insert(jobs_collection, [_active_doc()])
        checker = ExpirationChecker(jobs_col=jobs_collection)
        with patch("pipeline.expiration.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.head = AsyncMock(return_value=_mock_response(500))
            asyncio.run(checker.run())
        doc = jobs_collection.find_one({"_id": "https://jobs.example.com/1"})
        assert doc["status"] == "valid"  # not expired
        assert doc["last_probed_at"] is not None

    def test_multiple_jobs_processed(self, jobs_collection):
        docs = [_active_doc(url=f"https://jobs.example.com/{i}") for i in range(5)]
        self._insert(jobs_collection, docs)
        result = self._run(jobs_collection)
        assert result.counters.total == 5
        assert result.counters.expired == 5


# ---------------------------------------------------------------------------
# CLI: import_service.cli commands (unit-level, mocked DB)
# ---------------------------------------------------------------------------


class TestCLICommands:
    def test_reindex_command(self, jobs_collection):
        import argparse

        from import_service.cli import cmd_reindex

        args = argparse.Namespace()
        with (
            patch("import_service.cli.ensure_indexes") as mock_idx,
            patch("import_service.cli._write_health"),
        ):
            rc = cmd_reindex(args)
        assert rc == 0
        mock_idx.assert_called_once()

    def test_stats_command(self, jobs_collection, capsys):
        import argparse

        from import_service.cli import cmd_stats

        args = argparse.Namespace()
        with (
            patch("import_service.cli.ensure_indexes"),
            patch("import_service.cli.get_jobs", return_value=jobs_collection),
        ):
            rc = cmd_stats(args)
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert "total_jobs" in out
        assert "by_status" in out

    def test_expire_command_dry_run(self, jobs_collection):
        import argparse

        from import_service.cli import cmd_expire

        args = argparse.Namespace(dry_run=True, limit=0, max_age_days=0)
        with (
            patch("import_service.cli.ensure_indexes"),
            patch("import_service.cli.get_jobs", return_value=jobs_collection),
            patch("import_service.cli._write_health"),
        ):
            rc = cmd_expire(args)
        assert rc == 0

    def test_cli_parser_commands_exist(self):
        from import_service.cli import build_parser

        parser = build_parser()
        # import
        args = parser.parse_args(["import"])
        assert args.command == "import"
        assert args.dry_run is False

        # expire
        args = parser.parse_args(["expire", "--limit", "100", "--max-age-days", "30"])
        assert args.command == "expire"
        assert args.limit == 100
        assert args.max_age_days == 30

        # reindex
        args = parser.parse_args(["reindex"])
        assert args.command == "reindex"

        # stats
        args = parser.parse_args(["stats"])
        assert args.command == "stats"

    def test_dict_to_raw_job_valid(self):
        from import_service.cli import _dict_to_raw_job

        d = {
            "url": "https://jobs.example.com/123",
            "title": "Senior Python Dev",
            "description": "We need a Python developer with 5 years of experience.",
            "company_name": "Acme Corp",
            "source": "adzuna",
            "published_at": "2026-05-01",
        }
        raw = _dict_to_raw_job(d)
        assert raw is not None
        assert raw.url == "https://jobs.example.com/123"
        assert raw.company_name == "Acme Corp"

    def test_dict_to_raw_job_with_canonical_keys(self):
        from import_service.cli import _dict_to_raw_job

        d = {
            "url": "https://jobs.example.com/456",
            "title": "Dev",
            "description": "Desc",
            "company_name": "Corp",
            "source": "jooble",
        }
        raw = _dict_to_raw_job(d)
        assert raw is not None
        assert raw.url == "https://jobs.example.com/456"

    def test_dict_to_raw_job_missing_required_returns_none(self):
        from import_service.cli import _dict_to_raw_job

        # Missing url
        d = {"title": "Dev", "description": "Desc", "company_name": "Corp", "source": "x"}
        assert _dict_to_raw_job(d) is None
