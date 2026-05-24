"""Mojibake fixer for double-encoded UTF-8 text.

Some upstream feeds return UTF-8 bytes that were mistakenly decoded as
Latin-1 at a previous hop, producing sequences like "Ã©" instead of "é".
`fix_mojibake` detects this pattern via well-known marker substrings and
round-trips the string through latin-1 → utf-8 to restore the original
characters. Strings without any marker are returned as-is to avoid
corrupting already-valid text.
"""

from __future__ import annotations

# Common double-encoded UTF-8 sequences. Each is the latin-1 rendering of
# the UTF-8 bytes of a non-ASCII character commonly found in EU/Latin
# language job postings (Spanish, French, Italian, Portuguese, German).
_MOJIBAKE_MARKERS: tuple[str, ...] = (
    "Ã©",  # é
    "Ã¨",  # è
    "Ãª",  # ê
    "Ã«",  # ë
    "Ã ",  # à
    "Ã¡",  # á
    "Ã¢",  # â
    "Ã£",  # ã
    "Ã¤",  # ä
    "Ã¥",  # å
    "Ã§",  # ç
    "Ã­",  # í
    "Ã®",  # î
    "Ã¯",  # ï
    "Ã²",  # ò
    "Ã³",  # ó
    "Ã´",  # ô
    "Ãµ",  # õ
    "Ã¶",  # ö
    "Ã¹",  # ù
    "Ãº",  # ú
    "Ã»",  # û
    "Ã¼",  # ü
    "Ã±",  # ñ
    "Ã‘",  # Ñ
    "Ã‰",  # É
    "Ã€",  # À
    "Ã",   # generic Ã prefix used as a final fallback
    "â€™",  # ’ (smart quote)
    "â€œ",  # “
    "â€",   # ”
    "â€“",  # –
    "â€”",  # —
    "â€¦",  # …
)


def has_mojibake(text: str) -> bool:
    """Return True if `text` contains at least one known mojibake marker."""
    if not text:
        return False
    return any(marker in text for marker in _MOJIBAKE_MARKERS)


def fix_mojibake(text: str) -> str:
    """Repair double-encoded UTF-8 text. Idempotent on clean strings.

    Detects mojibake via marker substrings and reverses the bad decode by
    re-encoding to cp1252 (or latin-1 as fallback) then decoding as utf-8.
    cp1252 is preferred because it covers Windows smart-quote sequences
    (â€™ → ’) that latin-1 cannot encode. If both round-trips fail (e.g.
    mixed encodings, marker is a false positive on a non-Latin string),
    returns the original text unchanged.
    """
    if not has_mojibake(text):
        return text
    for encoding in ("cp1252", "latin-1"):
        try:
            return text.encode(encoding, errors="strict").decode(
                "utf-8", errors="strict"
            )
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return text
