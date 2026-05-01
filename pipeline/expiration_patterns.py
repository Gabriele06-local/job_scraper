"""Per-source body patterns for expiration detection on HTTP 200 responses.

Bootstrapped empty. Add patterns as boards with 200-dead-body are identified.
Key = source name (matches source field in MongoDB). Value = list of compiled regex.
"""

from __future__ import annotations

import re

# fmt: off
EXPIRY_BODY_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    # Example: "jobisjob": [re.compile(r"job.+no longer available", re.I)],
}
# fmt: on

# Redirect URL fragments that signal an expired job regardless of status code.
EXPIRED_REDIRECT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"/expired", re.I),
    re.compile(r"/job-not-found", re.I),
    re.compile(r"/offre-expiree", re.I),
    re.compile(r"/annuncio-scaduto", re.I),
    re.compile(r"/oferta-cerrada", re.I),
    re.compile(r"/stelle-nicht-mehr-verfuegbar", re.I),
]

# Multilingual body patterns applied when source is NOT in EXPIRY_BODY_PATTERNS
# but we receive a 200 with a suspiciously short body (< 500 chars).
GENERIC_EXPIRY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"this (job|position) is no longer (available|accepting)", re.I),
    re.compile(r"job (has been|is) (removed|closed|expired|filled)", re.I),
    re.compile(r"annuncio (scaduto|non disponibile|rimosso)", re.I),
    re.compile(r"offre.+(expir|ferm|supprim)", re.I),
    re.compile(r"oferta (cerrada|expirada|eliminada)", re.I),
    re.compile(r"stelle.*(nicht mehr|abgelaufen|geschlossen)", re.I),
    re.compile(r"no longer accepting applications", re.I),
    re.compile(r"position has been filled", re.I),
    re.compile(r"vacancy.*closed", re.I),
]
