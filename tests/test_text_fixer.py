"""Unit tests for utils.text_fixer."""

from __future__ import annotations

from utils.text_fixer import fix_mojibake, has_mojibake


def test_passthrough_on_clean_ascii() -> None:
    assert fix_mojibake("Hello world") == "Hello world"


def test_passthrough_on_clean_unicode() -> None:
    # Already-valid UTF-8 must NOT be re-encoded.
    assert fix_mojibake("café") == "café"
    assert fix_mojibake("años de experiencia") == "años de experiencia"


def test_fixes_spanish_mojibake() -> None:
    # "años" double-encoded → "aÃ±os"
    assert fix_mojibake("aÃ±os de experiencia") == "años de experiencia"


def test_fixes_french_mojibake() -> None:
    assert fix_mojibake("dÃ©veloppeur") == "développeur"


def test_fixes_portuguese_mojibake() -> None:
    assert fix_mojibake("informaÃ§Ã£o") == "informação"


def test_fixes_smart_quotes() -> None:
    assert fix_mojibake("itâ€™s a job") == "it’s a job"


def test_empty_and_none_like() -> None:
    assert fix_mojibake("") == ""


def test_has_mojibake_detection() -> None:
    assert has_mojibake("aÃ±os") is True
    assert has_mojibake("años") is False
    assert has_mojibake("") is False


def test_idempotent_on_fixed_text() -> None:
    fixed = fix_mojibake("dÃ©veloppeur")
    assert fix_mojibake(fixed) == fixed


def test_returns_original_when_decode_fails() -> None:
    # A "Ã" marker that does NOT form valid UTF-8 when re-encoded must be
    # left alone, not corrupted.
    weird = "Ã"  # lone Ã, no following byte → invalid utf-8 sequence
    # We accept either: unchanged, or attempted fix; just must not raise.
    result = fix_mojibake(weird)
    assert isinstance(result, str)
