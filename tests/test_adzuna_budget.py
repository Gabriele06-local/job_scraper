"""Tests for Adzuna daily call budget tracking."""

from __future__ import annotations

from unittest.mock import patch

import mongomock
import pytest

from pipeline.budget import _ADZUNA_DAILY_LIMIT, DailyBudget


@pytest.fixture
def budget() -> DailyBudget:
    client = mongomock.MongoClient()
    db = client["testdb"]
    return DailyBudget(db, "adzuna")


def test_initial_count_is_zero(budget: DailyBudget) -> None:
    assert budget.current() == 0


def test_increment_increases_count(budget: DailyBudget) -> None:
    c1 = budget.increment()
    c2 = budget.increment()
    assert c1 == 1
    assert c2 == 2


def test_not_exhausted_below_limit(budget: DailyBudget) -> None:
    for _ in range(10):
        budget.increment()
    assert not budget.is_exhausted()


def test_exhausted_at_limit(budget: DailyBudget) -> None:
    # Jump counter to limit via direct insert.
    budget._col.insert_one(
        {"provider": "adzuna", "date": "2099-01-01", "calls": _ADZUNA_DAILY_LIMIT}
    )
    with patch("pipeline.budget._today_key", return_value="2099-01-01"):
        assert budget.is_exhausted()


def test_consume_returns_true_within_limit(budget: DailyBudget) -> None:
    for _ in range(5):
        assert budget.consume(limit=10)


def test_consume_returns_false_over_limit(budget: DailyBudget) -> None:
    # Fill to limit.
    budget._col.insert_one(
        {"provider": "adzuna", "date": "2099-12-31", "calls": 10}
    )
    with patch("pipeline.budget._today_key", return_value="2099-12-31"):
        result = budget.consume(limit=10)
    assert not result


def test_different_providers_isolated() -> None:
    client = mongomock.MongoClient()
    db = client["testdb"]
    b_adzuna = DailyBudget(db, "adzuna")
    b_reed = DailyBudget(db, "reed")
    b_adzuna.increment()
    b_adzuna.increment()
    assert b_adzuna.current() == 2
    assert b_reed.current() == 0
