"""Tests for ai/cache.py + router/classifier cache integration (SPEC 05 §4.5)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from ai.cache import InMemoryLRUCache, NullCache, default_cache, make_cache_key
from ai.provider import LLMResponse
from ai.router import ModelRouter, ParseError
from ai.tasks import AITask
from ai.telemetry import CostTracker


# -- key + backends --------------------------------------------------------


def test_make_cache_key_is_deterministic_and_sensitive():
    a = make_cache_key("EXTRACT", "v1", "payload")
    assert a == make_cache_key("EXTRACT", "v1", "payload")
    assert a != make_cache_key("EXTRACT", "v2", "payload")  # version matters
    assert a != make_cache_key("TRIAGE", "v1", "payload")  # task matters
    assert a != make_cache_key("EXTRACT", "v1", "other")  # input matters


def test_lru_eviction():
    cache = InMemoryLRUCache(maxsize=2)
    cache.put("a", {"v": 1})
    cache.put("b", {"v": 2})
    cache.get("a")  # touch a → b is now LRU
    cache.put("c", {"v": 3})  # evicts b
    assert cache.get("a") is not None
    assert cache.get("b") is None
    assert cache.get("c") is not None


def test_null_cache_never_stores():
    cache = NullCache()
    cache.put("a", {"v": 1})
    assert cache.get("a") is None


def test_default_cache_respects_setting(monkeypatch):
    monkeypatch.setattr("ai.cache.settings.ai_cache_enabled", False)
    assert isinstance(default_cache(), NullCache)
    monkeypatch.setattr("ai.cache.settings.ai_cache_enabled", True)
    assert isinstance(default_cache(), InMemoryLRUCache)


# -- router integration ----------------------------------------------------


class _CountingProvider:
    def __init__(self, confidence: float = 0.9) -> None:
        self.confidence = confidence
        self.calls = 0

    def complete(self, *, model, system, user, max_tokens, temperature, timeout):
        self.calls += 1
        return LLMResponse(
            content=json.dumps({"confidence": self.confidence}),
            model=model,
            tokens_in=10,
            tokens_out=5,
            latency_ms=1,
        )


def _parse(content):
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ParseError(str(exc)) from exc
    return data, float(data["confidence"])


def _build(_tier, correction):
    return "sys", "usr"


def test_router_second_identical_call_hits_cache():
    prov = _CountingProvider()
    tracker = CostTracker()
    router = ModelRouter(provider=prov, tracker=tracker, cache=InMemoryLRUCache())

    r1 = router.run(
        task=AITask.EXTRACT, build_prompt=_build, parse=_parse, cache_key="k1"
    )
    r2 = router.run(
        task=AITask.EXTRACT, build_prompt=_build, parse=_parse, cache_key="k1"
    )

    assert prov.calls == 1  # second call served from cache
    assert r1.confidence == r2.confidence
    assert tracker.cache_hits == 1


def test_router_no_cache_key_bypasses_cache():
    prov = _CountingProvider()
    router = ModelRouter(provider=prov, tracker=CostTracker(), cache=InMemoryLRUCache())
    router.run(task=AITask.EXTRACT, build_prompt=_build, parse=_parse)
    router.run(task=AITask.EXTRACT, build_prompt=_build, parse=_parse)
    assert prov.calls == 2


# -- classifier integration (AC-5) -----------------------------------------


def test_classifier_repeated_classify_is_cached():
    from ai.classifier import GroqClassifier

    output = {
        "skills": ["Python"],
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
        "confidence": 0.95,
    }
    choice = MagicMock()
    choice.message.content = json.dumps(output)
    usage = MagicMock()
    usage.prompt_tokens = 50
    usage.completion_tokens = 20
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    client = MagicMock()
    client.chat.completions.create.return_value = resp

    clf = GroqClassifier(client=client)
    clf._rate_limiter._min_interval = 0.0  # type: ignore[attr-defined]

    job = {
        "title": "Senior Backend Engineer",
        "company_name": "Acme",
        "location_raw": "Remote",
        "detected_language": "en",
        "description": "desc",
        "url": "https://x/1",
    }
    first = clf.classify(job)
    second = clf.classify(job)  # identical input → cache hit

    assert first is not None and second is not None
    assert first.seniority == second.seniority
    assert client.chat.completions.create.call_count == 1
