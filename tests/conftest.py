"""Shared pytest fixtures for the import service test suite."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "ground_truth"


# ---------------------------------------------------------------------------
# Ground truth loader
# ---------------------------------------------------------------------------

def _load_fixture(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)


@pytest.fixture(scope="session")
def ground_truth_all() -> list[dict[str, Any]]:
    """All 30 ground-truth fixtures."""
    fixtures = []
    for p in sorted(FIXTURES_DIR.glob("*.json")):
        data = _load_fixture(p)
        data["_fixture_name"] = p.stem
        fixtures.append(data)
    return fixtures


@pytest.fixture(scope="session")
def ground_truth_pass(ground_truth_all: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fixtures expected to reach valid or premium status."""
    return [
        f for f in ground_truth_all
        if f["expected_output"]["expected_status"] in ("valid", "premium")
    ]


@pytest.fixture(scope="session")
def ground_truth_reject_prefilter(ground_truth_all: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fixtures expected to be rejected at the pre-filter stage."""
    return [
        f for f in ground_truth_all
        if f["expected_output"]["expected_status"] == "rejected_prefilter"
    ]


@pytest.fixture(scope="session")
def ground_truth_reject_quality(ground_truth_all: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fixtures expected to be rejected at the quality gate."""
    return [
        f for f in ground_truth_all
        if f["expected_output"]["expected_status"] == "rejected_quality"
    ]


# ---------------------------------------------------------------------------
# MongoDB mock (mongomock)
# ---------------------------------------------------------------------------

@pytest.fixture
def mongo_client():
    """In-memory MongoDB client via mongomock."""
    mongomock = pytest.importorskip("mongomock")
    client = mongomock.MongoClient("mongodb://localhost:27017/")
    yield client
    client.close()


@pytest.fixture
def mongo_db(mongo_client):
    """itjobhub database on the mock client."""
    return mongo_client["itjobhub"]


@pytest.fixture
def jobs_collection(mongo_db):
    """Empty jobs collection with no indexes (indexes created by pipeline at boot)."""
    col = mongo_db["jobs"]
    col.drop()
    return col


# ---------------------------------------------------------------------------
# Groq client mock — returns fixture ai_output deterministically
# ---------------------------------------------------------------------------

def _make_groq_response(ai_output: dict[str, Any]) -> MagicMock:
    """Build a mock Groq ChatCompletion response for a given ai_output dict."""
    choice = MagicMock()
    choice.message.content = json.dumps(ai_output)
    response = MagicMock()
    response.choices = [choice]
    return response


@pytest.fixture
def mock_groq_client(ground_truth_all: list[dict[str, Any]]):
    """Mock Groq client that dispatches fixed responses by offer title."""
    title_to_ai_output: dict[str, Any] = {
        f["input"]["title"]: f["expected_output"].get("ai_output")
        for f in ground_truth_all
    }

    client = MagicMock()

    def _chat_create(**kwargs: Any) -> MagicMock:
        messages = kwargs.get("messages", [])
        user_content = next(
            (m["content"] for m in messages if m.get("role") == "user"), ""
        )
        matched_output: dict[str, Any] | None = None
        for title, ai_output in title_to_ai_output.items():
            if title in user_content:
                matched_output = ai_output
                break

        if matched_output is None:
            # Fallback: minimal valid response
            matched_output = {
                "skills": [],
                "category": "unknown",
                "seniority": "unknown",
                "role_family": "other",
                "employment_type": "unknown",
                "remote_mode": "unknown",
                "salary_min": None,
                "salary_max": None,
                "currency": None,
                "languages_required": [],
                "quality_flags": [],
                "confidence": 0.0,
            }

        return _make_groq_response(matched_output)

    client.chat.completions.create.side_effect = _chat_create
    return client


@pytest.fixture
def patched_groq(mock_groq_client):
    """Patch groq.Groq constructor to return mock_groq_client."""
    with patch("groq.Groq", return_value=mock_groq_client):
        yield mock_groq_client
