"""structlog JSON logging setup with pipeline_run_id context binding."""

from __future__ import annotations

import logging
import sys

import structlog

from config import settings


def configure_logging() -> None:
    """Configure structlog for JSON output to stdout."""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
    )

    logging.basicConfig(
        stream=sys.stdout,
        level=level,
        format="%(message)s",
    )


def bind_run_id(run_id: str) -> None:
    """Bind pipeline_run_id to all subsequent log calls in this thread/task."""
    structlog.contextvars.bind_contextvars(pipeline_run_id=run_id)


def clear_run_context() -> None:
    """Clear all contextvars (call at end of run)."""
    structlog.contextvars.clear_contextvars()
