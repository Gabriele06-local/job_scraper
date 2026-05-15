"""Regression: _dedupe_field must remove duplicates before unique index build.

Reproduces the prod failure of 2026-05-15:
    pymongo.errors.DuplicateKeyError on jobs.url_unique
"""

from __future__ import annotations

import mongomock
import pytest
from pymongo import ASCENDING
from pymongo.operations import IndexModel

from database.repository import _dedupe_field


@pytest.fixture
def jobs():
    client = mongomock.MongoClient()
    return client["testdb"]["jobs"]


def test_dedupe_keeps_newest_by_id(jobs):
    older = jobs.insert_one({"url": "https://x.io/a", "title": "old"}).inserted_id
    newer = jobs.insert_one({"url": "https://x.io/a", "title": "new"}).inserted_id

    deleted = _dedupe_field(jobs, "url")

    assert deleted == 1
    remaining = list(jobs.find({"url": "https://x.io/a"}))
    assert len(remaining) == 1
    assert remaining[0]["_id"] == newer
    assert older not in {d["_id"] for d in remaining}


def test_dedupe_idempotent_when_no_duplicates(jobs):
    jobs.insert_one({"url": "https://x.io/a"})
    jobs.insert_one({"url": "https://x.io/b"})

    assert _dedupe_field(jobs, "url") == 0
    assert jobs.count_documents({}) == 2


def test_dedupe_ignores_docs_without_field(jobs):
    jobs.insert_one({"title": "no url field"})
    jobs.insert_one({"url": None, "title": "explicit null"})
    jobs.insert_one({"url": "https://x.io/a", "title": "keep"})

    assert _dedupe_field(jobs, "url") == 0
    assert jobs.count_documents({}) == 3


def test_unique_sparse_index_builds_after_dedupe(jobs):
    jobs.insert_one({"url": "https://x.io/a", "title": "one"})
    jobs.insert_one({"url": "https://x.io/a", "title": "two"})
    jobs.insert_one({"url": "https://x.io/a", "title": "three"})

    _dedupe_field(jobs, "url")

    # Build the same index the production code builds — must NOT raise.
    jobs.create_indexes(
        [IndexModel([("url", ASCENDING)], unique=True, sparse=True, name="url_unique")]
    )
    assert "url_unique" in {idx["name"] for idx in jobs.list_indexes()}
