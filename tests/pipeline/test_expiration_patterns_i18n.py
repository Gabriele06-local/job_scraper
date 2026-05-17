"""Per-language soft-404 pattern coverage (SDD §A.2 / §A.9).

For each of the 5 supported languages (it, en, es, de, fr) we exercise the
compiled phrase list against 3 positive samples and 2 negative samples.
"""

from __future__ import annotations

import pytest

from pipeline.expiration_patterns import (
    EXPIRED_REDIRECT_PATTERNS,
    PHRASES_BY_LANGUAGE,
)


def _matches_any(text: str, lang: str) -> bool:
    return any(p.search(text) for p in PHRASES_BY_LANGUAGE[lang])


# ---------------------------------------------------------------------------
# Positive samples (3 per language) — must trigger at least one pattern.
# ---------------------------------------------------------------------------


POSITIVE_SAMPLES: dict[str, list[str]] = {
    "it": [
        "Annuncio scaduto - non disponibile",
        "La posizione coperta da un altro candidato",
        "Le candidature chiuse da ieri sera",
    ],
    "en": [
        "This position is no longer available",
        "This role has been filled, sorry!",
        "We are no longer accepting applications for this role",
    ],
    "es": [
        "Oferta cerrada el 01/05/2026",
        "El puesto cubierto ya",
        "Vacante cerrada por la empresa",
    ],
    "de": [
        "Diese Stelle nicht mehr verfügbar",
        "Position besetzt seit gestern",
        "Bewerbungen geschlossen - vielen Dank",
    ],
    "fr": [
        "Cette offre expirée depuis 3 jours",
        "Poste pourvu, merci de votre intérêt",
        "Annonce supprimée par l'employeur",
    ],
}


NEGATIVE_SAMPLES: dict[str, list[str]] = {
    "it": [
        "Senior Python developer per il team backend di una scaleup",
        "Cerchiamo sviluppatore esperto in React e TypeScript",
    ],
    "en": [
        "Senior Python developer wanted for our backend team",
        "Join our team as a Frontend Engineer with React experience",
    ],
    "es": [
        "Buscamos desarrollador Python senior para nuestro equipo",
        "Únete a nuestro equipo de ingeniería de datos",
    ],
    "de": [
        "Senior Java Backend Entwickler gesucht für unser Team",
        "Wir suchen einen erfahrenen DevOps Engineer mit Kubernetes",
    ],
    "fr": [
        "Développeur Python senior recherché pour notre équipe backend",
        "Rejoignez notre équipe en tant qu'ingénieur frontend",
    ],
}


@pytest.mark.parametrize("lang", sorted(PHRASES_BY_LANGUAGE.keys()))
def test_positive_samples_match(lang: str) -> None:
    misses: list[str] = []
    for sample in POSITIVE_SAMPLES[lang]:
        if not _matches_any(sample, lang):
            misses.append(sample)
    assert not misses, f"{lang}: should have matched -> {misses}"


@pytest.mark.parametrize("lang", sorted(PHRASES_BY_LANGUAGE.keys()))
def test_negative_samples_do_not_match(lang: str) -> None:
    spurious: list[str] = []
    for sample in NEGATIVE_SAMPLES[lang]:
        if _matches_any(sample, lang):
            spurious.append(sample)
    assert not spurious, f"{lang}: false positive -> {spurious}"


@pytest.mark.parametrize("lang", sorted(PHRASES_BY_LANGUAGE.keys()))
def test_each_language_has_at_least_10_patterns(lang: str) -> None:
    assert len(PHRASES_BY_LANGUAGE[lang]) >= 10


def test_composite_redirect_pattern_matches_known_dead_paths() -> None:
    composite_hits = [
        "/job/expired",
        "/position/closed",
        "/stelle/abgelaufen",
        # composite alternation per SDD §A.2:
        # (expired|closed|filled|scaduto|chiusa|abgelaufen)
        "/oferta/closed",
        "/offre/expired",
        "/annuncio/scaduto",
    ]
    for path in composite_hits:
        assert any(p.search(path) for p in EXPIRED_REDIRECT_PATTERNS), (
            f"composite pattern missed {path}"
        )


def test_composite_redirect_pattern_does_not_match_live_paths() -> None:
    live_paths = [
        "/job/12345",
        "/position/backend-engineer",
        "/jobs",
        "/careers",
    ]
    for path in live_paths:
        # Only the composite line should be considered; individual /expired etc.
        # naturally don't match these paths either.
        assert not any(p.search(path) for p in EXPIRED_REDIRECT_PATTERNS), (
            f"false positive on {path}"
        )
