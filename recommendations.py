"""Hardcover-powered personal recommendation rows.

Kept separate from hardcover.py (the metadata/search role) and from app.py (thin
router). All Hardcover query strings, library parsing, and per-row filtering live
here as pure functions plus one ``build_all(client)`` orchestrator. The Hardcover
token is global (one account) — see the design spec — so no per-user cache key is
needed, but the recommendation logic is isolated here so a per-user token could be
threaded in later without a rewrite.
"""
import json
import logging
import re
from dataclasses import dataclass, field

import hardcover

logger = logging.getLogger(__name__)

PERSONALIZED_CATEGORIES = ("continue_series", "more_by_authors", "want_to_read")

# Hardcover user_books.status_id values (verified live).
STATUS_WANT = 1
STATUS_READING = 2
STATUS_READ = 3

ROW_LIMIT = 20
PER_SERIES_CAP = 3
AUTHOR_MIN_USERS = 50


@dataclass
class Library:
    read_ids: set = field(default_factory=set)
    reading_ids: set = field(default_factory=set)
    excluded_ids: set = field(default_factory=set)
    author_ids: list = field(default_factory=list)
    # series_id -> {"furthest": int, "last_date": str, "name": str}
    series_progress: dict = field(default_factory=dict)
    # [{"book": <book dict>, "date_added": str}]
    want: list = field(default_factory=list)


def _me(data: dict) -> dict:
    me = data.get("me")
    if isinstance(me, list):
        me = me[0] if me else {}
    return me or {}


def _series_info(cfs):
    """cached_featured_series -> (series_id, series_name, details) or None."""
    if isinstance(cfs, str):
        try:
            cfs = json.loads(cfs)
        except (ValueError, TypeError):
            return None
    if not isinstance(cfs, dict):
        return None
    series = cfs.get("series") or {}
    sid = series.get("id")
    if sid is None:
        return None
    return sid, series.get("name") or "", cfs.get("details")


def _parse_int_position(value):
    """Integer series position, or None for missing/fractional (e.g. 0.1)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != int(f):
        return None
    return int(f)


def parse_library(data: dict) -> Library:
    """Partition the raw ``me { user_books }`` response into a Library."""
    lib = Library()
    seen_authors = set()
    for ub in _me(data).get("user_books") or []:
        status = ub.get("status_id")
        book = ub.get("book") or {}
        bid = book.get("id")
        date_added = ub.get("date_added") or ""
        if bid is None:
            continue
        if status == STATUS_READ:
            lib.read_ids.add(bid)
            for c in book.get("contributions") or []:
                aid = (c.get("author") or {}).get("id")
                if aid is not None and aid not in seen_authors:
                    seen_authors.add(aid)
                    lib.author_ids.append(aid)
            info = _series_info(book.get("cached_featured_series"))
            if info:
                sid, sname, details = info
                entry = lib.series_progress.setdefault(
                    sid, {"furthest": 0, "last_date": "", "name": sname})
                pos = _parse_int_position(details)
                if pos is not None and pos > entry["furthest"]:
                    entry["furthest"] = pos
                if date_added > entry["last_date"]:
                    entry["last_date"] = date_added
        elif status == STATUS_READING:
            lib.reading_ids.add(bid)
        elif status == STATUS_WANT:
            lib.want.append({"book": book, "date_added": date_added})
    lib.excluded_ids = lib.read_ids | lib.reading_ids
    return lib


# Box sets / omnibus editions: "..., Books 1-4", "Boxed Set", "Omnibus", "Bundle".
_BOX_SET_RE = re.compile(
    r"(books?\s+\d+\s*[-–—]\s*\d+|boxed?\s*set|omnibus|\bbundle\b)", re.I)
# Parenthetical alternate/foreign editions: "(German Edition)", "(French)", etc.
_ALT_EDITION_RE = re.compile(
    r"\([^)]*\b(?:edition|language|prime|translation)\b[^)]*\)", re.I)


def _has_cover(book):
    return bool(hardcover.cover_url(book.get("cached_image")))


def _rank(book):
    """Canonical-pick ordering: more readers wins, then having a cover."""
    return (book.get("users_count") or 0, 1 if _has_cover(book) else 0)


def _is_noise(title):
    title = title or ""
    return bool(_BOX_SET_RE.search(title) or _ALT_EDITION_RE.search(title))


def select_continue_series(library: Library, data: dict) -> list:
    """Next unread primary entries per partly-read series, ordered by the user's
    most-recent read activity. Returns normalized book dicts (shared schema)."""
    blocks = []  # (last_date, [book dicts in position order])
    for s in data.get("series") or []:
        prog = library.series_progress.get(s.get("id"))
        if not prog:
            continue
        furthest = prog["furthest"]
        primary_count = s.get("primary_books_count") or 0
        by_pos = {}  # position -> canonical book
        for bs in s.get("book_series") or []:
            pos = _parse_int_position(bs.get("position"))
            if pos is None or pos <= furthest:
                continue
            if primary_count and pos > primary_count:
                continue
            book = bs.get("book") or {}
            if book.get("compilation"):
                continue
            if book.get("id") in library.excluded_ids:
                continue
            if _is_noise(book.get("title", "")):
                continue
            cur = by_pos.get(pos)
            if cur is None or _rank(book) > _rank(cur):
                by_pos[pos] = book
        positions = sorted(by_pos)[:PER_SERIES_CAP]
        if positions:
            blocks.append((prog["last_date"], [by_pos[p] for p in positions]))
    blocks.sort(key=lambda b: b[0], reverse=True)
    out, seen = [], set()
    for _, books in blocks:
        for book in books:
            bid = book.get("id")
            if bid in seen:
                continue
            seen.add(bid)
            out.append(hardcover.normalize_book_row(book))
            if len(out) >= ROW_LIMIT:
                return out
    return out


def select_more_by_authors(library: Library, data: dict) -> list:
    """Popular books by authors of the user's Read set, excluding books already
    read/reading and compilations. Returns normalized book dicts."""
    out, seen = [], set()
    for book in data.get("books") or []:
        bid = book.get("id")
        if bid in library.excluded_ids or bid in seen:
            continue
        if book.get("compilation"):
            continue
        seen.add(bid)
        out.append(hardcover.normalize_book_row(book))
        if len(out) >= ROW_LIMIT:
            break
    return out


def select_want_to_read(library: Library) -> list:
    """The account's Want-to-Read shelf, most-recently-added first."""
    items = sorted(library.want, key=lambda w: w.get("date_added") or "", reverse=True)
    return [hardcover.normalize_book_row(w["book"]) for w in items[:ROW_LIMIT]]


# ─── Queries ───
#
# Depth constraint: Hardcover caps GraphQL query depth at 3, but JSON *scalar*
# columns (cached_image, cached_featured_series) don't add depth. These shapes
# were verified live against a real account during design (see the spec).

LIBRARY_QUERY = """
query Library {
  me {
    user_books {
      status_id
      date_added
      book {
        id
        title
        cached_image
        cached_featured_series
        contributions(limit: 2) { author { id name } }
      }
    }
  }
}
"""

SERIES_QUERY = """
query SeriesExpand($ids: [Int!]) {
  series(where: {id: {_in: $ids}}) {
    id name books_count primary_books_count
    book_series(order_by: {position: asc}, limit: 40) {
      position
      book {
        id title release_date cached_image compilation users_count
        contributions(limit: 2) { author { name } }
      }
    }
  }
}
"""

BY_AUTHORS_QUERY = """
query ByAuthors($aids: [Int!]) {
  books(where: {contributions: {author_id: {_in: $aids}}, users_count: {_gte: %d}},
        order_by: {users_count: desc}, limit: 30) {
    id title release_date users_count cached_image compilation
    contributions(limit: 2) { author { name } }
  }
}
""" % AUTHOR_MIN_USERS


def _safe_row(name, fn):
    """Run one row builder; a failure degrades to [] and must never 500 the page."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - deliberately broad: one row must not break the page
        logger.warning("recommendation row %s failed: %s", name, e)
        return []


def build_all(client) -> dict:
    """Build all three personal rows, fetching the Hardcover library once.

    ``client`` is any object exposing ``_post(query, variables=None) -> dict``
    (the real HardcoverClient, or a fake in tests). Never raises: a library-fetch
    failure yields all-empty rows; a single row's failure degrades only that row.
    """
    try:
        library = parse_library(client._post(LIBRARY_QUERY))
    except Exception as e:  # noqa: BLE001
        logger.warning("Hardcover library fetch failed: %s", e)
        return {c: [] for c in PERSONALIZED_CATEGORIES}

    def _continue():
        ids = list(library.series_progress)
        if not ids:
            return []
        return select_continue_series(library, client._post(SERIES_QUERY, {"ids": ids}))

    def _authors():
        if not library.author_ids:
            return []
        return select_more_by_authors(
            library, client._post(BY_AUTHORS_QUERY, {"aids": library.author_ids}))

    return {
        "continue_series": _safe_row("continue_series", _continue),
        "more_by_authors": _safe_row("more_by_authors", _authors),
        "want_to_read": _safe_row("want_to_read", lambda: select_want_to_read(library)),
    }
