"""AI task catalogue + tier definitions (SPEC 05 §4.4).

Callers reference an :class:`AITask` capability; the router resolves the tier to
a concrete model (SPEC 05 constraint C-4 — no model literals above the router).
Each task declares an entry tier, an escalation ceiling, a prompt id+version
(for the registry / cache key), and a token budget.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from config import settings


class Tier(str, Enum):
    """Model capability tiers, ordered cheapest → most capable."""

    FAST = "FAST"
    STRUCT = "STRUCT"
    REASON = "REASON"


# Escalation order (index = strength).
TIER_ORDER: tuple[Tier, ...] = (Tier.FAST, Tier.STRUCT, Tier.REASON)


class AITask(str, Enum):
    """Capability-level tasks the pipeline can ask the router to perform."""

    EXTRACT = "EXTRACT"  # full job classification (the always-on call)
    TRIAGE = "TRIAGE"  # cheap pre-screen (opt-in)
    SPAM_CHECK = "SPAM_CHECK"  # advanced spam / fake-remote reasoning


# Hint appended on an escalated call (was ai/classifier._RETRY_HINT). Tells the
# stronger model not to fabricate values just to raise confidence.
ESCALATION_HINT: str = (
    "If a field is genuinely unknowable from the text, return the literal "
    "string 'unknown' (or null where the schema allows). Do not invent values "
    "to raise confidence."
)


@dataclass(frozen=True)
class TaskConfig:
    """Static routing config for one task."""

    task: AITask
    entry_tier: Tier
    ceiling_tier: Tier  # never escalate above this
    prompt_id: str
    prompt_version: str
    max_tokens: int


def _extract_max_tokens() -> int:
    """EXTRACT keeps the historical GROQ_MAX_TOKENS budget."""
    return settings.groq_max_tokens


TASK_CONFIG: dict[AITask, TaskConfig] = {
    AITask.EXTRACT: TaskConfig(
        task=AITask.EXTRACT,
        entry_tier=Tier.FAST,
        ceiling_tier=Tier.STRUCT,  # D-05-1: 8b default, escalate to 32b only
        prompt_id="extract",
        prompt_version="v1",
        max_tokens=_extract_max_tokens(),
    ),
    AITask.TRIAGE: TaskConfig(
        task=AITask.TRIAGE,
        entry_tier=Tier.FAST,
        ceiling_tier=Tier.FAST,  # triage never escalates
        prompt_id="triage",
        prompt_version="v1",
        max_tokens=256,
    ),
    AITask.SPAM_CHECK: TaskConfig(
        task=AITask.SPAM_CHECK,
        entry_tier=Tier.REASON,  # only invoked when suspicion already flagged
        ceiling_tier=Tier.REASON,
        prompt_id="spam_check",
        prompt_version="v1",
        max_tokens=512,
    ),
}
