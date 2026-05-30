"""Multi-model AI telemetry — per-call records, token/cost tracking, tracing.

Generalizes the single-model ``_CostTracker`` that previously lived in
``ai/classifier.py`` (SPEC 05 §4.6). Every provider call produces one
:class:`AICallRecord`; the process-global :data:`cost_tracker` accumulates them
across models and emits a per-run summary.

The summary keeps the legacy keys ``groq_tokens_in`` / ``groq_tokens_out`` /
``groq_cost_usd`` so ``pipeline/orchestrator.py`` and existing tests keep
working, and adds per-model breakdown plus cache/escalation counters.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# USD per 1M tokens, (input, output), per model — 2025 Groq public rates.
# FAST mirrors the historical llama-3.1-8b-instant constants; STRUCT/REASON are
# the published qwen3-32b / llama-3.3-70b rates. Unknown models fall back to FAST.
_PRICING: dict[str, tuple[float, float]] = {
    "llama-3.1-8b-instant": (0.05, 0.08),
    "qwen/qwen3-32b": (0.29, 0.59),
    "llama-3.3-70b-versatile": (0.59, 0.79),
}
_DEFAULT_PRICE: tuple[float, float] = (0.05, 0.08)


def price_for(model: str) -> tuple[float, float]:
    """Return (input, output) USD-per-1M-tokens for a model (FAST rate if unknown)."""
    return _PRICING.get(model, _DEFAULT_PRICE)


def cost_for(model: str, tokens_in: int, tokens_out: int) -> float:
    """Compute USD cost for a single call given its model and token counts."""
    price_in, price_out = price_for(model)
    return tokens_in * price_in / 1_000_000 + tokens_out * price_out / 1_000_000


@dataclass
class AICallRecord:
    """One provider call's telemetry (SPEC 05 §4.6)."""

    task: str
    tier: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cache_hit: bool = False
    escalated_from: str | None = None
    confidence: float | None = None
    trace_id: str = ""

    @property
    def cost_usd(self) -> float:
        if self.cache_hit:
            return 0.0
        return cost_for(self.model, self.tokens_in, self.tokens_out)


class CostTracker:
    """Thread-safe, process-global accumulator across models and tasks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: int = 0
        self.cache_hits: int = 0
        self.escalations: int = 0
        # model -> {"calls", "tokens_in", "tokens_out"}
        self._by_model: dict[str, dict[str, int]] = {}

    def record(self, rec: AICallRecord) -> None:
        """Accumulate one call and emit a structured ``ai.call`` log line."""
        with self._lock:
            self.calls += 1
            if rec.cache_hit:
                self.cache_hits += 1
            if rec.escalated_from:
                self.escalations += 1
            if not rec.cache_hit:
                bucket = self._by_model.setdefault(
                    rec.model, {"calls": 0, "tokens_in": 0, "tokens_out": 0}
                )
                bucket["calls"] += 1
                bucket["tokens_in"] += rec.tokens_in
                bucket["tokens_out"] += rec.tokens_out
        logger.info(
            "ai.call",
            task=rec.task,
            tier=rec.tier,
            model=rec.model,
            tokens_in=rec.tokens_in,
            tokens_out=rec.tokens_out,
            latency_ms=rec.latency_ms,
            cost_usd=round(rec.cost_usd, 6),
            cache_hit=rec.cache_hit,
            escalated_from=rec.escalated_from,
            confidence=rec.confidence,
            trace_id=rec.trace_id,
        )

    @property
    def tokens_in(self) -> int:
        with self._lock:
            return sum(m["tokens_in"] for m in self._by_model.values())

    @property
    def tokens_out(self) -> int:
        with self._lock:
            return sum(m["tokens_out"] for m in self._by_model.values())

    @property
    def cost_usd(self) -> float:
        with self._lock:
            return sum(
                cost_for(model, m["tokens_in"], m["tokens_out"])
                for model, m in self._by_model.items()
            )

    def summary(self) -> dict[str, Any]:
        """Per-run summary; legacy ``groq_*`` keys preserved for orchestrator."""
        with self._lock:
            per_model = {
                model: {
                    "calls": m["calls"],
                    "tokens_in": m["tokens_in"],
                    "tokens_out": m["tokens_out"],
                    "cost_usd": round(
                        cost_for(model, m["tokens_in"], m["tokens_out"]), 6
                    ),
                }
                for model, m in self._by_model.items()
            }
            total_in = sum(m["tokens_in"] for m in self._by_model.values())
            total_out = sum(m["tokens_out"] for m in self._by_model.values())
            total_cost = sum(
                cost_for(model, m["tokens_in"], m["tokens_out"])
                for model, m in self._by_model.items()
            )
            return {
                # Legacy keys (back-compat with orchestrator + tests).
                "groq_tokens_in": total_in,
                "groq_tokens_out": total_out,
                "groq_cost_usd": round(total_cost, 6),
                # New multi-model observability.
                "ai_calls": self.calls,
                "ai_cache_hits": self.cache_hits,
                "ai_escalations": self.escalations,
                "ai_by_model": per_model,
            }

    def reset(self) -> None:
        """Clear all counters (used by tests for isolation)."""
        with self._lock:
            self.calls = 0
            self.cache_hits = 0
            self.escalations = 0
            self._by_model = {}


# Process-global tracker (mirrors the old module-level singleton).
cost_tracker = CostTracker()
