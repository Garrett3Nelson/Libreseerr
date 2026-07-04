"""Pure, tested title/author normalization for library matching.

One implementation, shared by the backend (/api/availability, discover payloads)
so the frontend can do dumb set-membership against precomputed keys — no
normalization logic duplicated in JS, so the two sides can't drift. All functions
are total and empty/None-safe.
"""
import re

# Apostrophes / periods join ("Ender's" -> "enders", "J.R.R." -> "jrr"); every
# other punctuation mark (dashes, colons, etc.) becomes a word separator.
_JOIN_PUNCT_RE = re.compile(r"['’.]")
_SEP_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\b\s*")
_WS_RE = re.compile(r"\s+")


def normalize_title(s) -> str:
    """Lowercase, drop subtitle/parenthetical qualifier, strip a leading article,
    strip punctuation, and collapse whitespace. Empty/None-safe (returns "")."""
    if not s:
        return ""
    text = str(s).lower()
    # Cut at the first ":" (subtitle) or " (" (parenthetical qualifier).
    for sep in (":", " ("):
        idx = text.find(sep)
        if idx != -1:
            text = text[:idx]
    text = _JOIN_PUNCT_RE.sub("", text)
    text = _SEP_PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    text = _LEADING_ARTICLE_RE.sub("", text)
    return text.strip()


def match_key(title, author="") -> str:
    """`normalize_title(title) + "|" + normalize_title(author)`.

    Author optional; when absent the key is "<normtitle>|". Used so a same-title /
    different-author collision within a library doesn't false-match."""
    return normalize_title(title) + "|" + normalize_title(author)
