"""Confidence-retry coverage for `ai/classifier.GroqClassifier` (SDD §A.7 / §A.9)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from ai.classifier import GroqClassifier


def _make_response(ai_output: dict) -> MagicMock:
    choice = MagicMock()
    choice.message.content = json.dumps(ai_output)
    response = MagicMock()
    response.choices = [choice]
    usage = MagicMock()
    usage.prompt_tokens = 100
    usage.completion_tokens = 30
    response.usage = usage
    return response


def _payload(**overrides) -> dict:
    p = {
        "title": "Backend Dev",
        "company_name": "Acme",
        "location_raw": "Milan",
        "detected_language": "en",
        "description": "Job desc",
        "url": "https://x.com/job/1",
    }
    p.update(overrides)
    return p


def _ai_output(confidence: float) -> dict:
    return {
        "skills": ["Python", "Django"],
        "category": "software-engineering",
        "seniority": "senior",
        "role_family": "backend",
        "employment_type": "full_time",
        "remote_mode": "remote",
        "salary_min": None,
        "salary_max": None,
        "currency": None,
        "languages_required": [],
        "quality_flags": ["clear_jd"],
        "confidence": confidence,
    }


@pytest.fixture
def mock_client() -> MagicMock:
    return MagicMock()


def test_low_confidence_triggers_one_retry(mock_client) -> None:
    """First call 0.4 → second call 0.8; final result is the retry's payload."""
    mock_client.chat.completions.create.side_effect = [
        _make_response(_ai_output(0.4)),
        _make_response(_ai_output(0.8)),
    ]

    clf = GroqClassifier(client=mock_client)
    clf._rate_limiter._min_interval = 0.0  # type: ignore[attr-defined]

    result = clf.classify(_payload())
    assert result is not None
    assert result.ai_confidence == pytest.approx(0.8)
    assert mock_client.chat.completions.create.call_count == 2


def test_high_confidence_no_retry(mock_client) -> None:
    """confidence 0.9 → no retry, single Groq round-trip."""
    mock_client.chat.completions.create.return_value = _make_response(_ai_output(0.9))

    clf = GroqClassifier(client=mock_client)
    clf._rate_limiter._min_interval = 0.0  # type: ignore[attr-defined]

    result = clf.classify(_payload())
    assert result is not None
    assert result.ai_confidence == pytest.approx(0.9)
    assert mock_client.chat.completions.create.call_count == 1


def test_low_confidence_retry_still_low_returns_retry_result(mock_client) -> None:
    """Even if retry confidence stays low, we return it — gate decides reject."""
    mock_client.chat.completions.create.side_effect = [
        _make_response(_ai_output(0.4)),
        _make_response(_ai_output(0.5)),
    ]

    clf = GroqClassifier(client=mock_client)
    clf._rate_limiter._min_interval = 0.0  # type: ignore[attr-defined]

    result = clf.classify(_payload())
    assert result is not None
    assert result.ai_confidence == pytest.approx(0.5)
    assert mock_client.chat.completions.create.call_count == 2


def test_retry_flag_prevents_recursion(mock_client) -> None:
    """When `_retry=True` is passed, no second retry is issued."""
    mock_client.chat.completions.create.return_value = _make_response(_ai_output(0.3))

    clf = GroqClassifier(client=mock_client)
    clf._rate_limiter._min_interval = 0.0  # type: ignore[attr-defined]

    result = clf.classify(_payload(), _retry=True)
    assert result is not None
    assert mock_client.chat.completions.create.call_count == 1


def test_retry_hint_injected_into_prompt(mock_client) -> None:
    """The retry call must carry the hint string in the user message."""
    mock_client.chat.completions.create.side_effect = [
        _make_response(_ai_output(0.4)),
        _make_response(_ai_output(0.8)),
    ]
    clf = GroqClassifier(client=mock_client)
    clf._rate_limiter._min_interval = 0.0  # type: ignore[attr-defined]

    clf.classify(_payload())

    second_call_kwargs = mock_client.chat.completions.create.call_args_list[1].kwargs
    user_msg = next(m["content"] for m in second_call_kwargs["messages"] if m["role"] == "user")
    assert "unknowable from the text" in user_msg or "do not invent" in user_msg.lower()
