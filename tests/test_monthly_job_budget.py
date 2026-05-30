"""Tests for the monthly per-source job budget (RapidAPI jobs-metered plans)."""

from __future__ import annotations

from unittest.mock import patch

import mongomock
import pytest

from pipeline.budget import DailyBudget, MonthlyJobBudget


@pytest.fixture
def budget() -> MonthlyJobBudget:
    client = mongomock.MongoClient()
    db = client["testdb"]
    return MonthlyJobBudget(db, "active_jobs_db", limit=250)


def test_initial_count_is_zero(budget: MonthlyJobBudget) -> None:
    assert budget.current() == 0
    assert budget.remaining() == 250
    assert not budget.is_exhausted()


def test_add_increases_total_and_shrinks_remaining(budget: MonthlyJobBudget) -> None:
    assert budget.add(100) == 100
    assert budget.add(50) == 150
    assert budget.current() == 150
    assert budget.remaining() == 100


def test_add_non_positive_is_noop(budget: MonthlyJobBudget) -> None:
    budget.add(10)
    assert budget.add(0) == 10
    assert budget.add(-5) == 10
    assert budget.current() == 10


def test_exhausted_at_limit(budget: MonthlyJobBudget) -> None:
    budget.add(250)
    assert budget.is_exhausted()
    assert budget.remaining() == 0


def test_exhausted_over_limit(budget: MonthlyJobBudget) -> None:
    budget.add(300)
    assert budget.is_exhausted()
    assert budget.remaining() == 0


def test_zero_limit_means_uncapped() -> None:
    client = mongomock.MongoClient()
    uncapped = MonthlyJobBudget(client["testdb"], "active_jobs_db", limit=0)
    uncapped.add(10_000)
    assert not uncapped.is_exhausted()
    assert uncapped.remaining() > 0


def test_months_are_isolated(budget: MonthlyJobBudget) -> None:
    with patch("pipeline.budget._month_key", return_value="2099-01"):
        budget.add(200)
        assert budget.current() == 200
    with patch("pipeline.budget._month_key", return_value="2099-02"):
        assert budget.current() == 0


def test_providers_are_isolated() -> None:
    db = mongomock.MongoClient()["testdb"]
    a = MonthlyJobBudget(db, "active_jobs_db", limit=250)
    b = MonthlyJobBudget(db, "workday_jobs", limit=250)
    a.add(100)
    assert a.current() == 100
    assert b.current() == 0


def test_coexists_with_daily_budget() -> None:
    """Monthly budget uses a separate collection — no (provider, date) clash."""
    db = mongomock.MongoClient()["testdb"]
    daily = DailyBudget(db, "active_jobs_db")
    monthly = MonthlyJobBudget(db, "active_jobs_db", limit=250)
    daily.increment()
    monthly.add(5)
    assert daily.current() == 1
    assert monthly.current() == 5
