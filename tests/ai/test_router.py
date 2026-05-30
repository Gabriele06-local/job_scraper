"""Tests for ai/router.py — escalation ladder, retry, telemetry (SPEC 05 §4.2)."""

from __future__ import annotations

import json

import pytest

from ai.provider import LLMError, LLMResponse, LLMTransientError
from ai.router import ModelRouter, ParseError, RouterResult
from ai.tasks import AITask
from ai.telemetry import CostTracker


class _FakeProvider:
    """Returns queued (confidence) payloads or raises queued exceptions."""

    def __init__(self, script: list) -> None:
        # Each item: a float confidence, an Exception instance, or a raw str.
        self._script = list(script)
        self.calls: list[str] = []  # model used per call

    def complete(self, *, model, system, user, max_tokens, temperature, timeout):
        self.calls.append(model)
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        content = item if isinstance(item, str) else json.dumps({"confidence": item})
        return LLMResponse(content=content, model=model, tokens_in=10, tokens_out=5, latency_ms=1)


def _build_prompt(_tier, correction):
    return "sys", f"user {correction}"


def _parse(content):
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ParseError(str(exc)) from exc
    return data, float(data["confidence"])


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("ai.router.time.sleep", lambda *_: None)


def _router(script):
    tracker = CostTracker()
    prov = _FakeProvider(script)
    return ModelRouter(provider=prov, tracker=tracker), prov, tracker


def test_high_confidence_single_fast_call_no_escalation():
    router, prov, tracker = _router([0.9])
    result = router.run(task=AITask.EXTRACT, build_prompt=_build_prompt, parse=_parse)
    assert isinstance(result, RouterResult)
    assert result.tier == "FAST"
    assert result.escalated_from is None
    assert result.model == "llama-3.1-8b-instant"
    assert prov.calls == ["llama-3.1-8b-instant"]
    assert tracker.escalations == 0
    assert tracker.calls == 1


def test_low_confidence_escalates_fast_to_struct():
    router, prov, tracker = _router([0.4, 0.85])
    result = router.run(task=AITask.EXTRACT, build_prompt=_build_prompt, parse=_parse)
    assert result.tier == "STRUCT"
    assert result.escalated_from == "FAST"
    assert result.model == "qwen/qwen3-32b"
    assert result.confidence == pytest.approx(0.85)
    # FAST first, then STRUCT.
    assert prov.calls == ["llama-3.1-8b-instant", "qwen/qwen3-32b"]
    assert tracker.escalations == 1


def test_escalation_capped_at_ceiling_returns_struct_even_if_low():
    # EXTRACT ceiling is STRUCT + max 1 hop: still-low STRUCT result is returned.
    router, prov, tracker = _router([0.3, 0.4])
    result = router.run(task=AITask.EXTRACT, build_prompt=_build_prompt, parse=_parse)
    assert result.tier == "STRUCT"
    assert result.confidence == pytest.approx(0.4)
    assert len(prov.calls) == 2  # no third (REASON) hop


def test_allow_escalation_false_stays_on_fast():
    router, prov, tracker = _router([0.2])
    result = router.run(
        task=AITask.EXTRACT, build_prompt=_build_prompt, parse=_parse, allow_escalation=False
    )
    assert result.tier == "FAST"
    assert prov.calls == ["llama-3.1-8b-instant"]


def test_escalated_call_carries_hint():
    router, prov, tracker = _router([0.4, 0.9])
    captured = []

    def build(_tier, correction):
        captured.append(correction)
        return "sys", "user"

    router.run(task=AITask.EXTRACT, build_prompt=build, parse=_parse)
    assert captured[0] == ""  # entry call: no hint
    assert "unknowable from the text" in captured[1]  # escalated call: hint


def test_persistent_transient_failure_returns_none_no_escalation():
    router, prov, tracker = _router(
        [LLMTransientError("x"), LLMTransientError("x"), LLMTransientError("x")]
    )
    result = router.run(task=AITask.EXTRACT, build_prompt=_build_prompt, parse=_parse)
    assert result is None
    # 3 within-tier attempts, no escalation to a new tier.
    assert prov.calls == ["llama-3.1-8b-instant"] * 3
    assert tracker.calls == 0  # nothing recorded on failure


def test_non_transient_error_returns_none_immediately():
    router, prov, tracker = _router([LLMError("bad")])
    result = router.run(task=AITask.EXTRACT, build_prompt=_build_prompt, parse=_parse)
    assert result is None
    assert len(prov.calls) == 1  # no retry on hard error


def test_parse_error_retries_then_none():
    router, prov, tracker = _router(["not json", "still bad", "{broken"])
    result = router.run(task=AITask.EXTRACT, build_prompt=_build_prompt, parse=_parse)
    assert result is None
    assert len(prov.calls) == 3


def test_parse_error_recovers_within_tier():
    router, prov, tracker = _router(["not json", json.dumps({"confidence": 0.9})])
    result = router.run(task=AITask.EXTRACT, build_prompt=_build_prompt, parse=_parse)
    assert result is not None
    assert result.confidence == pytest.approx(0.9)
    assert len(prov.calls) == 2
