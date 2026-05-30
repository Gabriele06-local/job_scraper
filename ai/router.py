"""Model router with confidence-escalation ladder (SPEC 05 §4.2).

The router is the **only** module that maps a task tier to a concrete model
name. It owns the control flow that the old ``GroqClassifier`` carried inline:

* **Within-tier retry** (3 attempts): transient provider errors back off and
  retry; invalid/parse errors re-prompt the same tier with a corrective hint.
  Exhausting a tier returns ``None`` and does **not** escalate — failures are
  not made more expensive (preserves the historical 3-call behaviour).
* **Cross-tier escalation**: a *successful* parse whose confidence is below
  ``AI_CONFIDENCE_THRESHOLD`` escalates one tier up (capped by the task's
  ceiling, ``AI_MAX_ESCALATION`` hops, and ``AI_ENABLE_REASON`` for REASON),
  re-issuing with :data:`ai.tasks.ESCALATION_HINT`.

Prompt construction and response parsing are injected by the caller so the
router stays generic and schema-agnostic.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Callable

import structlog

from ai.cache import CacheBackend, default_cache
from ai.provider import GroqProvider, LLMError, LLMProvider, LLMTransientError
from ai.tasks import ESCALATION_HINT, TIER_ORDER, AITask, TaskConfig, Tier, TASK_CONFIG
from ai.telemetry import AICallRecord, CostTracker, cost_tracker
from config import settings

logger = structlog.get_logger(__name__)

_MAX_ATTEMPTS_PER_TIER = 3


class ParseError(Exception):
    """Raised by a caller's parse callback when content is unusable.

    The router treats this as a recoverable validation error and re-prompts the
    same tier with a corrective hint (up to the per-tier attempt limit).
    """


# (system_prompt, user_prompt) for a given tier + correction string.
BuildPrompt = Callable[[Tier, str], tuple[str, str]]
# raw content -> (parsed_data, confidence); raises ParseError on bad content.
ParseFn = Callable[[str], tuple[Any, float]]


@dataclass
class RouterResult:
    """Outcome of a routed task call."""

    data: Any
    confidence: float
    model: str
    tier: str
    escalated_from: str | None = None


class ModelRouter:
    """Routes a task through tiers, escalating on low confidence."""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        tracker: CostTracker | None = None,
        cache: CacheBackend | None = None,
    ) -> None:
        self._provider = provider or GroqProvider()
        self._tracker = tracker or cost_tracker
        self._cache = cache if cache is not None else default_cache()

    # -- public ------------------------------------------------------------

    def run(
        self,
        *,
        task: AITask,
        build_prompt: BuildPrompt,
        parse: ParseFn,
        trace_id: str = "",
        allow_escalation: bool = True,
        cache_key: str | None = None,
    ) -> RouterResult | None:
        """Execute ``task``, escalating on low confidence. None on failure.

        When ``cache_key`` is supplied and the cache is enabled, an identical
        prior result is returned with zero provider calls (SPEC 05 §4.5).
        """
        cfg = TASK_CONFIG[task]

        cached = self._cache_lookup(cache_key, task, parse, trace_id)
        if cached is not None:
            return cached

        tier = cfg.entry_tier
        escalated_from: str | None = None
        correction = ""
        max_hops = settings.ai_max_escalation if allow_escalation else 0
        hops = 0

        while True:
            model = self._model_for_tier(tier)
            outcome = self._call_tier(
                cfg=cfg,
                tier=tier,
                model=model,
                build_prompt=build_prompt,
                parse=parse,
                correction=correction,
                trace_id=trace_id,
                escalated_from=escalated_from,
            )
            if outcome is None:
                return None  # failures do not escalate

            data, confidence, content = outcome
            if confidence < settings.ai_confidence_threshold and hops < max_hops:
                nxt = self._next_tier(tier, cfg.ceiling_tier)
                if nxt is not None and self._tier_enabled(nxt):
                    logger.info(
                        "ai.escalate",
                        task=task.value,
                        from_tier=tier.value,
                        to_tier=nxt.value,
                        confidence=round(confidence, 2),
                        trace_id=trace_id,
                    )
                    escalated_from = tier.value
                    tier = nxt
                    correction = ESCALATION_HINT
                    hops += 1
                    continue

            if cache_key is not None:
                self._cache.put(
                    cache_key,
                    {
                        "content": content,
                        "model": model,
                        "tier": tier.value,
                        "confidence": confidence,
                    },
                )
            return RouterResult(
                data=data,
                confidence=confidence,
                model=model,
                tier=tier.value,
                escalated_from=escalated_from,
            )

    # -- internals ---------------------------------------------------------

    def _cache_lookup(
        self,
        cache_key: str | None,
        task: AITask,
        parse: ParseFn,
        trace_id: str,
    ) -> RouterResult | None:
        """Return a cached result (re-validated) or None on miss/bad entry."""
        if cache_key is None:
            return None
        entry = self._cache.get(cache_key)
        if entry is None:
            return None
        try:
            data, confidence = parse(entry["content"])
        except (ParseError, KeyError):
            return None  # stale/corrupt entry — treat as miss
        self._tracker.record(
            AICallRecord(
                task=task.value,
                tier=entry.get("tier", ""),
                model=entry.get("model", ""),
                tokens_in=0,
                tokens_out=0,
                latency_ms=0,
                cache_hit=True,
                confidence=confidence,
                trace_id=trace_id,
            )
        )
        return RouterResult(
            data=data,
            confidence=confidence,
            model=entry.get("model", ""),
            tier=entry.get("tier", ""),
            escalated_from=None,
        )

    def _model_for_tier(self, tier: Tier) -> str:
        if tier is Tier.FAST:
            return settings.groq_model_fast
        if tier is Tier.STRUCT:
            return settings.groq_model_struct
        return settings.groq_model_reason

    def _next_tier(self, tier: Tier, ceiling: Tier) -> Tier | None:
        idx = TIER_ORDER.index(tier)
        ceiling_idx = TIER_ORDER.index(ceiling)
        if idx + 1 > ceiling_idx:
            return None
        return TIER_ORDER[idx + 1]

    def _tier_enabled(self, tier: Tier) -> bool:
        if tier is Tier.REASON:
            return settings.ai_enable_reason
        return True

    def _call_tier(
        self,
        *,
        cfg: TaskConfig,
        tier: Tier,
        model: str,
        build_prompt: BuildPrompt,
        parse: ParseFn,
        correction: str,
        trace_id: str,
        escalated_from: str | None,
    ) -> tuple[Any, float, str] | None:
        """One tier's call with 3 attempts (transient backoff + parse retry).

        Returns ``(parsed_data, confidence, raw_content)`` on success.
        """
        current_correction = correction
        for attempt in range(1, _MAX_ATTEMPTS_PER_TIER + 1):
            system, user = build_prompt(tier, current_correction)
            try:
                resp = self._provider.complete(
                    model=model,
                    system=system,
                    user=user,
                    max_tokens=cfg.max_tokens,
                    temperature=settings.groq_temperature,
                    timeout=settings.groq_timeout,
                )
            except LLMTransientError as exc:
                wait = min(2**attempt, 30) + random.random()
                logger.warning(
                    "ai.transient_retry",
                    task=cfg.task.value,
                    tier=tier.value,
                    attempt=attempt,
                    error=str(exc)[:120],
                    wait_s=round(wait, 1),
                    trace_id=trace_id,
                )
                if attempt < _MAX_ATTEMPTS_PER_TIER:
                    time.sleep(wait)
                    continue
                logger.error(
                    "ai.exhausted_transient",
                    task=cfg.task.value,
                    tier=tier.value,
                    trace_id=trace_id,
                )
                return None
            except LLMError as exc:
                logger.error(
                    "ai.provider_error",
                    task=cfg.task.value,
                    tier=tier.value,
                    error=str(exc)[:120],
                    trace_id=trace_id,
                )
                return None

            try:
                data, confidence = parse(resp.content)
            except ParseError as exc:
                current_correction = (
                    f"Your previous response was invalid. Error: {str(exc)[:200]}"
                )
                logger.warning(
                    "ai.validation_retry",
                    task=cfg.task.value,
                    tier=tier.value,
                    attempt=attempt,
                    error=str(exc)[:120],
                    trace_id=trace_id,
                )
                if attempt == _MAX_ATTEMPTS_PER_TIER:
                    logger.error(
                        "ai.exhausted_validation",
                        task=cfg.task.value,
                        tier=tier.value,
                        trace_id=trace_id,
                    )
                    return None
                continue

            self._tracker.record(
                AICallRecord(
                    task=cfg.task.value,
                    tier=tier.value,
                    model=model,
                    tokens_in=resp.tokens_in,
                    tokens_out=resp.tokens_out,
                    latency_ms=resp.latency_ms,
                    escalated_from=escalated_from,
                    confidence=confidence,
                    trace_id=trace_id,
                )
            )
            return data, confidence, resp.content

        return None
