"""Lingua-based language detector.

Builds detector from supported languages only — faster and more accurate
than all-languages mode for the 6 target languages.
"""

from __future__ import annotations

from lingua import Language as LinguaLanguage
from lingua import LanguageDetectorBuilder

from models.job import Language

_LINGUA_TO_SCHEMA: dict[LinguaLanguage, Language] = {
    LinguaLanguage.ENGLISH: Language.EN,
    LinguaLanguage.ITALIAN: Language.IT,
    LinguaLanguage.SPANISH: Language.ES,
    LinguaLanguage.GERMAN: Language.DE,
    LinguaLanguage.FRENCH: Language.FR,
    LinguaLanguage.PORTUGUESE: Language.PT,
}

_SUPPORTED_LINGUA = set(_LINGUA_TO_SCHEMA.keys())

# All-language detector correctly identifies unsupported languages as OTHER.
# Using from_languages would force-map unsupported text to a supported bucket.
_detector = LanguageDetectorBuilder.from_all_languages().build()


def detect_language(text: str) -> tuple[Language, float]:
    """Detect language and return (Language, confidence 0..1).

    Returns (Language.OTHER, 0.0) when language is unsupported or inconclusive.
    """
    if not text or not text.strip():
        return Language.OTHER, 0.0

    detected = _detector.detect_language_of(text)
    if detected is None or detected not in _SUPPORTED_LINGUA:
        return Language.OTHER, 0.0

    values = _detector.compute_language_confidence_values(text)
    conf = next((v.value for v in values if v.language == detected), 0.0)
    return _LINGUA_TO_SCHEMA[detected], round(conf, 4)
