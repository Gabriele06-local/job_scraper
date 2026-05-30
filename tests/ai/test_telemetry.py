"""Tests for ai/telemetry.py — pricing, AICallRecord, CostTracker."""

from __future__ import annotations

import pytest

from ai.telemetry import AICallRecord, CostTracker, cost_for, price_for


def test_price_for_known_and_unknown() -> None:
    assert price_for("llama-3.1-8b-instant") == (0.05, 0.08)
    assert price_for("qwen/qwen3-32b") == (0.29, 0.59)
    # Unknown model falls back to FAST pricing.
    assert price_for("some-future-model") == (0.05, 0.08)


def test_cost_for_uses_per_model_rate() -> None:
    # 1M in + 1M out on the 70b model = 0.59 + 0.79.
    assert cost_for("llama-3.3-70b-versatile", 1_000_000, 1_000_000) == pytest.approx(
        0.59 + 0.79
    )


def test_record_cost_is_zero_on_cache_hit() -> None:
    rec = AICallRecord(
        task="EXTRACT",
        tier="STRUCT",
        model="qwen/qwen3-32b",
        tokens_in=1000,
        tokens_out=1000,
        latency_ms=10,
        cache_hit=True,
    )
    assert rec.cost_usd == 0.0


def test_tracker_accumulates_across_models_and_keeps_legacy_keys() -> None:
    tracker = CostTracker()
    tracker.record(
        AICallRecord("EXTRACT", "FAST", "llama-3.1-8b-instant", 1000, 500, 12)
    )
    tracker.record(
        AICallRecord(
            "EXTRACT",
            "STRUCT",
            "qwen/qwen3-32b",
            2000,
            1000,
            40,
            escalated_from="FAST",
        )
    )
    tracker.record(
        AICallRecord("EXTRACT", "FAST", "llama-3.1-8b-instant", 0, 0, 1, cache_hit=True)
    )

    summary = tracker.summary()
    # Legacy keys still present for the orchestrator.
    assert summary["groq_tokens_in"] == 3000
    assert summary["groq_tokens_out"] == 1500
    assert summary["groq_cost_usd"] > 0
    # New observability.
    assert summary["ai_calls"] == 3
    assert summary["ai_cache_hits"] == 1
    assert summary["ai_escalations"] == 1
    assert set(summary["ai_by_model"]) == {
        "llama-3.1-8b-instant",
        "qwen/qwen3-32b",
    }
    # Cache hit must not inflate token totals for its model.
    assert summary["ai_by_model"]["llama-3.1-8b-instant"]["calls"] == 1


def test_tracker_reset() -> None:
    tracker = CostTracker()
    tracker.record(
        AICallRecord("EXTRACT", "FAST", "llama-3.1-8b-instant", 10, 10, 1)
    )
    tracker.reset()
    assert tracker.calls == 0
    assert tracker.summary()["groq_tokens_in"] == 0
