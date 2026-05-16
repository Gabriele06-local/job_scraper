"""Multilingual expiration / soft-404 phrase library (SDD §A.2).

Three pattern surfaces:
  * `EXPIRY_BODY_PATTERNS` — per-source overrides for known dead-body shapes.
  * `EXPIRED_REDIRECT_PATTERNS` — URL fragments that signal an expired listing
    regardless of HTTP status.
  * `GENERIC_EXPIRY_PATTERNS` — per-language phrase list applied as a fallback
    when no per-source override matches and the response body is suspicious.

The per-language lists below contain ≥10 phrases each (IT/EN/ES/DE/FR) so soft-
404 detection works across the five locales the dashboard ships.
"""

from __future__ import annotations

import re

# fmt: off

# Per-source overrides — populated as boards with 200-dead-body shapes are
# identified. Key = source name (matches `source` field in MongoDB).
EXPIRY_BODY_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    # Example: "jobisjob": [re.compile(r"job.+no longer available", re.I)],
}

# fmt: on


# ---------------------------------------------------------------------------
# Redirect URL fragments — SDD §A.2 extension
# ---------------------------------------------------------------------------

EXPIRED_REDIRECT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"/expired", re.I),
    re.compile(r"/job-not-found", re.I),
    re.compile(r"/offre-expiree", re.I),
    re.compile(r"/annuncio-scaduto", re.I),
    re.compile(r"/oferta-cerrada", re.I),
    re.compile(r"/stelle-nicht-mehr-verfuegbar", re.I),
    # SDD §A.2 — composite pattern catching /job|position|stelle|oferta|offre|annuncio
    # followed by an expired-state segment in any of the 5 supported languages.
    re.compile(
        r"/(job|position|stelle|oferta|offre|annuncio)/"
        r"(expired|closed|filled|scaduto|chiusa|abgelaufen)",
        re.I,
    ),
]


# ---------------------------------------------------------------------------
# Per-language soft-404 phrase libraries (≥10 each)
# ---------------------------------------------------------------------------

_PHRASES_IT: tuple[str, ...] = (
    "annuncio scaduto",
    "posizione non più disponibile",
    "offerta chiusa",
    "annuncio rimosso",
    "candidature chiuse",
    "selezione chiusa",
    "posizione coperta",
    "annuncio non più attivo",
    "ricerca terminata",
    "offerta non disponibile",
    "ricerca conclusa",
)

_PHRASES_EN: tuple[str, ...] = (
    "this position is no longer available",
    "job has been filled",
    "no longer accepting applications",
    "this vacancy is closed",
    "posting has expired",
    "role has been filled",
    "we are no longer hiring for this role",
    "application window closed",
    "this listing has been removed",
    "opportunity is closed",
    "position has been closed",
)

_PHRASES_ES: tuple[str, ...] = (
    "oferta cerrada",
    "oferta expirada",
    "puesto cubierto",
    "oferta no disponible",
    "vacante cerrada",
    "proceso de selección cerrado",
    "ya no aceptamos candidaturas",
    "oferta eliminada",
    "anuncio caducado",
    "posición ya no disponible",
    "vacante ya no disponible",
)

_PHRASES_DE: tuple[str, ...] = (
    "stelle nicht mehr verfügbar",
    "stellenanzeige abgelaufen",
    "position besetzt",
    "bewerbungen geschlossen",
    "anzeige wurde entfernt",
    "stelle ist geschlossen",
    "vakanz beendet",
    "auswahlverfahren abgeschlossen",
    "anzeige nicht mehr aktiv",
    "stellenausschreibung gelöscht",
    "stelle wurde besetzt",
)

_PHRASES_FR: tuple[str, ...] = (
    "offre expirée",
    "poste pourvu",
    "annonce supprimée",
    "candidatures clôturées",
    "offre fermée",
    "recrutement terminé",
    "poste non disponible",
    "annonce non publiée",
    "offre clôturée",
    "ce poste n'est plus disponible",
    "offre non disponible",
)


def _compile_phrases(phrases: tuple[str, ...]) -> list[re.Pattern[str]]:
    return [re.compile(re.escape(p), re.IGNORECASE | re.UNICODE) for p in phrases]


# Public per-language compiled lists (also useful for tests).
PHRASES_BY_LANGUAGE: dict[str, list[re.Pattern[str]]] = {
    "it": _compile_phrases(_PHRASES_IT),
    "en": _compile_phrases(_PHRASES_EN),
    "es": _compile_phrases(_PHRASES_ES),
    "de": _compile_phrases(_PHRASES_DE),
    "fr": _compile_phrases(_PHRASES_FR),
}


# Flat list used by `pipeline/expiration.py` when source has no override and the
# response body looks dead. Compiled once at import.
GENERIC_EXPIRY_PATTERNS: list[re.Pattern[str]] = [
    p for patterns in PHRASES_BY_LANGUAGE.values() for p in patterns
]
