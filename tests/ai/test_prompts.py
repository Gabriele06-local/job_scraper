"""Tests for ai/prompts.py — registry, versioning, schema, builders."""

from __future__ import annotations

import pytest

from ai.prompts import (
    EXTRACT_SCHEMA,
    FIELD_CONFIDENCE_KEYS,
    PROMPTS,
    build_extract_structured,
    get_prompt,
)


def test_registry_has_core_tasks():
    assert ("extract", "v1") in PROMPTS
    assert ("triage", "v1") in PROMPTS
    assert ("spam_check", "v1") in PROMPTS


def test_get_prompt_returns_template_with_schema_str():
    tpl = get_prompt("extract", "v1")
    assert tpl_id_ok(tpl)
    assert '"confidence"' in tpl.schema_str  # schema serialized


def tpl_id_ok(tpl) -> bool:
    return tpl.prompt_id == "extract" and tpl.version == "v1"


def test_get_prompt_unknown_version_raises():
    with pytest.raises(KeyError):
        get_prompt("extract", "v999")


def test_extract_schema_has_field_confidence():
    fc = EXTRACT_SCHEMA["properties"]["field_confidence"]
    assert fc["type"] == "object"
    assert set(fc["properties"]) == set(FIELD_CONFIDENCE_KEYS)


def test_extract_system_mentions_seniority_rules():
    # Drift guard parity with the legacy classifier test.
    tpl = get_prompt("extract", "v1")
    assert "Seniority rules" in tpl.system
    assert "field_confidence" in tpl.system


def test_build_extract_structured_includes_fields_and_hint():
    prompt = build_extract_structured(
        title="Senior Go Dev",
        company_name="Acme",
        location_raw="Milan",
        detected_language="en",
        description="desc",
        correction="fix it",
    )
    assert "Senior Go Dev" in prompt
    assert "Acme" in prompt
    assert "IMPORTANT: fix it" in prompt
    # Schema is embedded so the model knows the contract.
    assert "field_confidence" in prompt
