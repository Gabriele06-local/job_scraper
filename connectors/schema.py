"""Canonical job schema — every connector must return dicts matching this shape.

All scrapers MUST use these exact keys so the normalizer (cli.py:_dict_to_raw_job)
can map directly without fallback chains. Extra keys beyond this set are tolerated
but ignored by the normalizer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NotRequired, TypedDict


class CanonicalJob(TypedDict, total=False):
    # Required fields — every job MUST have these
    title: str
    company_name: str
    description: str
    url: str
    source: str

    # Optional fields — set when available
    published_at: NotRequired[str | None]
    location_raw: NotRequired[str | None]
    salary_min: NotRequired[float | int | None]
    salary_max: NotRequired[float | int | None]
    currency: NotRequired[str | None]
    external_id: NotRequired[str | None]
    original_language: NotRequired[str | None]
    # Structured fields the source already provides (employment_type, remote_mode,
    # seniority, skills, …), used as authoritative AI hints to cut token cost.
    source_hints: NotRequired[dict[str, str] | None]


REQUIRED_KEYS: frozenset[str] = frozenset(
    {"title", "company_name", "description", "url", "source"}
)


def validate(job: Mapping[str, Any]) -> list[str]:
    """Return a list of missing required keys. Empty list = valid."""
    return [k for k in REQUIRED_KEYS if not job.get(k)]
