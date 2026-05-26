"""Tests for ai/classifier.py — classify() and classify_job() methods."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import groq

from models.job import (
    JobClassification,
    RemoteMode,
    RoleFamily,
    Seniority,
)

_NOW = datetime.now(tz=timezone.utc)

_VALID_AI_OUTPUT = {
    "skills": ["Go", "Kubernetes", "PostgreSQL", "Kafka"],
    "category": "software_engineering",
    "seniority": "senior",
    "role_family": "backend",
    "employment_type": "full_time",
    "remote_mode": "remote",
    "salary_min": 90000,
    "salary_max": 130000,
    "currency": "EUR",
    "languages_required": ["en"],
    "quality_flags": ["clear_jd", "has_requirements"],
    "confidence": 0.94,
}

_JOB_RAW_DICT = {
    "title": "Senior Backend Engineer (Go)",
    "company_name": "Acme Cloud Ltd",
    "location_raw": "Remote (EU)",
    "detected_language": "en",
    "description": "We are hiring a Senior Backend Engineer with expertise in Go. "
    "You will build microservices on AWS EKS. Requirements: 4+ years Go, "
    "Kubernetes, PostgreSQL, Kafka. Salary: EUR 90k-130k. Full remote EU.",
    "url": "https://jobs.example.com/1",
}


def _make_groq_response(ai_output: dict) -> MagicMock:
    usage = MagicMock()
    usage.prompt_tokens = 100
    usage.completion_tokens = 50
    choice = MagicMock()
    choice.message.content = json.dumps(ai_output)
    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


def _make_client(ai_output: dict) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = _make_groq_response(ai_output)
    return client


# ---------------------------------------------------------------------------
# classify() — structured dict input (new SPEC 02 interface)
# ---------------------------------------------------------------------------


class TestClassifyDict:
    def test_returns_classification_on_success(self):
        from ai.classifier import GroqClassifier

        clf = GroqClassifier(client=_make_client(_VALID_AI_OUTPUT))
        clf._rate_limiter._min_interval = 0.0
        result = clf.classify(_JOB_RAW_DICT)

        assert isinstance(result, JobClassification)
        assert "Go" in result.technical_skills
        assert result.seniority == Seniority.SENIOR
        assert result.role_family == RoleFamily.BACKEND
        assert result.remote_mode == RemoteMode.REMOTE
        assert result.remote is True
        assert result.ai_confidence == 0.94
        assert result.salary_min == 90000
        assert result.currency == "EUR"

    def test_returns_none_on_persistent_api_failure(self):
        from ai.classifier import GroqClassifier

        client = MagicMock()
        client.chat.completions.create.side_effect = groq.RateLimitError(
            "rate limited", response=MagicMock(), body={}
        )
        clf = GroqClassifier(client=client)
        clf._rate_limiter._min_interval = 0.0

        result = clf.classify(_JOB_RAW_DICT)
        assert result is None
        assert client.chat.completions.create.call_count == 3

    def test_returns_none_on_persistent_json_error(self):
        from ai.classifier import GroqClassifier

        choice = MagicMock()
        choice.message.content = "not json at all"
        response = MagicMock()
        response.choices = [choice]
        response.usage = MagicMock()
        response.usage.prompt_tokens = 0
        response.usage.completion_tokens = 0

        client = MagicMock()
        client.chat.completions.create.return_value = response

        clf = GroqClassifier(client=client)
        clf._rate_limiter._min_interval = 0.0

        result = clf.classify(_JOB_RAW_DICT)
        assert result is None
        assert client.chat.completions.create.call_count == 3

    def test_retries_with_correction_on_json_error_then_succeeds(self):
        """Fail twice with bad JSON, succeed on third attempt."""
        from ai.classifier import GroqClassifier

        bad_choice = MagicMock()
        bad_choice.message.content = "{{broken"
        bad_response = MagicMock()
        bad_response.choices = [bad_choice]
        bad_response.usage = MagicMock()
        bad_response.usage.prompt_tokens = 0
        bad_response.usage.completion_tokens = 0

        good_response = _make_groq_response(_VALID_AI_OUTPUT)

        client = MagicMock()
        client.chat.completions.create.side_effect = [
            bad_response,
            bad_response,
            good_response,
        ]

        clf = GroqClassifier(client=client)
        clf._rate_limiter._min_interval = 0.0

        result = clf.classify(_JOB_RAW_DICT)
        assert result is not None
        assert result.seniority == Seniority.SENIOR
        assert client.chat.completions.create.call_count == 3

    def test_corrective_hint_sent_in_retry_prompt(self):
        """Third-attempt prompt must contain IMPORTANT correction hint."""
        from ai.classifier import GroqClassifier

        bad_choice = MagicMock()
        bad_choice.message.content = "not json"
        bad_response = MagicMock()
        bad_response.choices = [bad_choice]
        bad_response.usage = MagicMock()
        bad_response.usage.prompt_tokens = 0
        bad_response.usage.completion_tokens = 0

        captured_prompts: list[str] = []

        def capture(**kwargs):
            msgs = kwargs.get("messages", [])
            user_msg = next((m["content"] for m in msgs if m.get("role") == "user"), "")
            captured_prompts.append(user_msg)
            # Return valid JSON on 3rd attempt
            if len(captured_prompts) >= 3:
                return _make_groq_response(_VALID_AI_OUTPUT)
            return bad_response

        client = MagicMock()
        client.chat.completions.create.side_effect = capture

        clf = GroqClassifier(client=client)
        clf._rate_limiter._min_interval = 0.0
        clf.classify(_JOB_RAW_DICT)

        assert len(captured_prompts) >= 2
        assert "IMPORTANT" in captured_prompts[1]

    def test_structured_prompt_includes_title(self):
        """User prompt must include job title per SPEC 02 §4."""
        from ai.classifier import GroqClassifier

        captured: list[str] = []

        def capture(**kwargs):
            msgs = kwargs.get("messages", [])
            captured.append(next((m["content"] for m in msgs if m.get("role") == "user"), ""))
            return _make_groq_response(_VALID_AI_OUTPUT)

        client = MagicMock()
        client.chat.completions.create.side_effect = capture

        clf = GroqClassifier(client=client)
        clf._rate_limiter._min_interval = 0.0
        clf.classify(_JOB_RAW_DICT)

        assert "Senior Backend Engineer (Go)" in captured[0]
        assert "Acme Cloud Ltd" in captured[0]

    def test_ai_model_and_timestamp_populated(self):
        from ai.classifier import GroqClassifier

        clf = GroqClassifier(client=_make_client(_VALID_AI_OUTPUT))
        clf._rate_limiter._min_interval = 0.0
        result = clf.classify(_JOB_RAW_DICT)

        assert result is not None
        assert result.ai_model != ""
        assert result.ai_call_at is not None

    def test_unknown_enum_coerced_to_default(self):
        from ai.classifier import GroqClassifier

        bad = dict(_VALID_AI_OUTPUT, seniority="wizard", role_family="alien")
        clf = GroqClassifier(client=_make_client(bad))
        clf._rate_limiter._min_interval = 0.0

        result = clf.classify(_JOB_RAW_DICT)
        assert result is not None
        assert result.seniority == Seniority.UNKNOWN
        assert result.role_family == RoleFamily.OTHER

    def test_empty_job_raw_does_not_raise(self):
        """classify() must handle missing keys gracefully."""
        from ai.classifier import GroqClassifier

        clf = GroqClassifier(client=_make_client(_VALID_AI_OUTPUT))
        clf._rate_limiter._min_interval = 0.0
        result = clf.classify({})
        assert result is not None  # Falls back to empty strings in prompt

    def test_classify_returns_none_on_rate_limit_success_on_third(self):
        """Retry on API errors; succeed on 3rd attempt → not None."""
        from ai.classifier import GroqClassifier

        client = MagicMock()
        client.chat.completions.create.side_effect = [
            groq.RateLimitError("rl", response=MagicMock(), body={}),
            groq.RateLimitError("rl", response=MagicMock(), body={}),
            _make_groq_response(_VALID_AI_OUTPUT),
        ]
        clf = GroqClassifier(client=client)
        clf._rate_limiter._min_interval = 0.0

        result = clf.classify(_JOB_RAW_DICT)
        assert result is not None
        assert client.chat.completions.create.call_count == 3


# ---------------------------------------------------------------------------
# Ground truth: classify() via conftest mock_groq_client
# ---------------------------------------------------------------------------


class TestClassifyGroundTruth:
    def test_all_passing_fixtures_return_classification(self, ground_truth_pass, mock_groq_client):
        from ai.classifier import GroqClassifier

        clf = GroqClassifier(client=mock_groq_client)
        clf._rate_limiter._min_interval = 0.0

        for f in ground_truth_pass:
            inp = f["input"]
            job_raw = {
                "title": inp["title"],
                "company_name": inp["company_name"],
                "location_raw": inp.get("location_raw", "unknown"),
                "detected_language": inp.get("detected_language", "unknown"),
                "description": inp["description"],
                "url": inp["url"],
            }
            result = clf.classify(job_raw)
            assert result is not None, f"classify() returned None for {inp['title']}"
            assert isinstance(result, JobClassification)

    def test_prefilter_bad_fixtures_ai_output_is_null(self, ground_truth_reject_prefilter):
        """Fixtures marked rejected_prefilter have null ai_output — no classify call needed."""
        for f in ground_truth_reject_prefilter:
            assert f["expected_output"].get("ai_output") is None


# ---------------------------------------------------------------------------
# Static prompt assertions
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """Static checks on _SYSTEM_PROMPT to prevent accidental drift."""

    def test_contains_seniority_rules(self):
        from ai.classifier import _SYSTEM_PROMPT

        assert "Seniority rules" in _SYSTEM_PROMPT
        assert '"senior" only when the title' in _SYSTEM_PROMPT
        assert '"junior" when the title' in _SYSTEM_PROMPT
        assert '"unknown" when no experience' in _SYSTEM_PROMPT
