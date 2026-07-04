"""Hardcover-powered personal recommendation rows.

Kept separate from hardcover.py (the metadata/search role) and from app.py (thin
router). All Hardcover query strings, library parsing, and per-row filtering live
here as pure functions plus one ``build_all(client)`` orchestrator. The Hardcover
token is global (one account) — see the design spec — so no per-user cache key is
needed, but the recommendation logic is isolated here so a per-user token could be
threaded in later without a rewrite.
"""
import datetime
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
SERIES_ENTRIES_CAP = 60   # whole-series card guard; real primary series don't reach this
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


def _parse_position(value):
    """Series position as a number, or None if missing/unparseable. Whole numbers
    return an ``int`` (so labels read "3 / 5", not "3.0 / 5"); genuine fractional
    installments — prequels below book 1 (Witcher's 0.5/0.6/0.7) or novellas
    between books (Stormlight's Edgedancer 3.1) — keep their ``float`` position."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else f


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
                # A non-numeric position means this book isn't a real installment
                # of the series — it's an anthology guest story Hardcover cross-
                # links at a label like "- Gamer's End" ("Press Start to Play" ->
                # The Machineries of Empire). Reading it is not reading the series,
                # so don't seed progress (which would recommend an unread series).
                # A genuine fractional read (a 0.5 prequel) still parses and seeds.
                if _parse_position(details) is None:
                    continue
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


# Box sets / omnibus editions, detected by title — Hardcover's `compilation`
# boolean is unreliable (it flags some single novels), so the title is the signal:
# "..., Books 1-4", "6 Books Set", "Boxed Set", "Omnibus", "Bundle", "Collection",
# "The Complete <Series>".
_BOX_SET_RE = re.compile(
    r"(books?\s+\d+\s*[-–—]\s*\d+"   # "Books 1-4"
    r"|\d+\s+books?\b"                # "6 Books", "3 Books Set"
    r"|boxed?\s*set"                 # "Boxed Set" / "Box Set"
    r"|\bomnibus\b"
    r"|\bbundle\b"
    r"|\bcollection\b"               # "... Books Collection"
    r"|\bcomplete\s+\w)",            # "The Complete Witcher"
    re.I)
# Parenthetical alternate/foreign editions: "(German Edition)", "(French)", etc.
_ALT_EDITION_RE = re.compile(
    r"\([^)]*\b(?:edition|language|prime|translation)\b[^)]*\)", re.I)
# Series-name qualifiers to strip: "(Publication Order)", "(Chronological)", etc.
_SERIES_QUALIFIER_RE = re.compile(
    r"\s*\([^)]*\b(?:order|publication|chronolog|omnibus)\b[^)]*\)", re.I)
_TRAILING_SERIES_RE = re.compile(r"\s+series\s*$", re.I)


def clean_series_name(name) -> str:
    """Strip parenthetical order/publication/chronological/omnibus qualifiers and a
    trailing 'Series', then collapse whitespace. Pure, empty-safe."""
    if not name:
        return ""
    text = _SERIES_QUALIFIER_RE.sub("", str(name))
    text = _TRAILING_SERIES_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _has_cover(book):
    return bool(hardcover.cover_url(book.get("cached_image")))


def _rank(book):
    """Canonical-pick ordering: more readers wins, then a non-compilation edition
    over a box set (Hardcover's `compilation` flag as a tiebreaker only), then
    having a cover."""
    return (book.get("users_count") or 0,
            0 if book.get("compilation") else 1,
            1 if _has_cover(book) else 0)


def _is_noise(title):
    title = title or ""
    if title.strip().lower().startswith("untitled"):
        return True
    return bool(_BOX_SET_RE.search(title) or _ALT_EDITION_RE.search(title))


def _is_unreleased(book, today):
    """Future-dated (announced but not published) — not requestable."""
    rel = (book.get("release_date") or "")[:10]
    return bool(rel) and rel > today


def select_continue_series(library: Library, data: dict) -> list:
    """Grouped per partly-read series, ordered by most-recent read activity.
    Each group carries the WHOLE primary run so the frontend arrows can scroll the
    entire series::

        {"series_id": int, "series_name": str,  # cleaned
         "series_total": int,                    # label denominator
         "entries": [ { **normalized_book, "position": int,
                        "released": bool, "read": bool }, ... ]}

    Entries are every installment in the primary run — whole-numbered books plus
    Hardcover-``featured`` fractional prequels/novellas (0.5, 2.5, …), ascending;
    non-featured rows (translations, split "Part" volumes, box sets) are dropped
    unless they're the edition the user read. Read positions — a *whole-numbered*
    position at/below the furthest-read book, or any position whose edition the
    user has read (a fractional novella counts as read only if actually logged) —
    are INCLUDED and flagged ``read: True``, preferring the actually-read edition;
    these are the left-hand context the arrows scroll back through. Upcoming
    (not-read) positions keep the content filters (canonical edition, drop
    compilation/noise/already-read) and are flagged ``read: False``; unreleased
    ones are included and flagged ``released: False``. A series is emitted only if
    it has at least one upcoming installment to get, so which series appear is
    unchanged. Capped at the highest ``SERIES_ENTRIES_CAP`` positions (upcoming
    installments are always retained); series ordered by recency and capped at
    ``ROW_LIMIT``.
    """
    today = datetime.date.today().isoformat()
    groups_out = []  # (last_date, group dict)
    for s in data.get("series") or []:
        prog = library.series_progress.get(s.get("id"))
        if not prog:
            continue
        furthest = prog["furthest"]
        primary_count = s.get("primary_books_count") or 0
        # Positions where the user read *any* edition — treated as read even if the
        # specific edition below isn't the one they logged.
        read_positions = {
            _parse_position(bs.get("position"))
            for bs in s.get("book_series") or []
            if (bs.get("book") or {}).get("id") in library.excluded_ids
        }
        read_positions.discard(None)
        # Group all editions by position across the whole primary run. Fractional
        # installments (0.x prequels, mid-series novellas) keep their float position.
        editions_by_pos = {}
        for bs in s.get("book_series") or []:
            pos = _parse_position(bs.get("position"))
            if pos is None:
                continue
            if pos <= 0:
                continue  # position 0 = Hardcover's omnibus/anthology/foreign anchor
            if primary_count and pos > primary_count:
                continue
            book = bs.get("book") or {}
            # Two kinds of book_series rows are returned by the API but hidden from
            # Hardcover's own series page because they aren't real installments:
            #   • split "Part N" volumes  -> book.is_partial_book is True
            #   • alternate/translated editions merged into a canonical book
            #                             -> book.canonical_id is set
            # Drop both (verified against the live Stormlight/OMW/Little Women
            # pages), unless it's the edition the user actually read — then keep it
            # as their read-context cover. This replaces the `featured` flag, which
            # was both over-inclusive (kept "Oathbringer Part 1") and under-
            # inclusive (dropped the real "The Sagan Diary" novella).
            if book.get("id") not in library.excluded_ids and (
                    book.get("is_partial_book") or book.get("canonical_id") is not None):
                continue
            editions_by_pos.setdefault(pos, []).append(book)
        # Pick a canonical edition per position; (book, is_read) survives filtering.
        # Read positions are left-hand scroll context, so they intentionally skip
        # the compilation/noise filters below — the read edition is what to show.
        canonical_by_pos = {}
        for pos, editions in editions_by_pos.items():
            # "Everything below the furthest-read book is read" only holds for
            # whole-numbered installments; a fractional novella below furthest is
            # read only if the user actually logged that edition.
            whole = pos == int(pos)
            is_read = pos in read_positions or (whole and pos <= furthest)
            if is_read:
                # Prefer the edition the user actually read; else best-ranked.
                read_ed = next(
                    (b for b in editions if b.get("id") in library.excluded_ids), None)
                canonical_by_pos[pos] = (read_ed or max(editions, key=_rank), True)
                continue
            # Box sets are detected by title (_is_noise), not the unreliable
            # `compilation` flag — which wrongly deletes some single novels (e.g.
            # the Witcher's "The Time of Contempt"). Ranking already deprioritizes
            # box sets, so a real installment normally wins its position outright.
            canonical = max(editions, key=_rank)
            if canonical.get("id") in library.excluded_ids:
                continue
            if _is_noise(canonical.get("title", "")):
                continue
            canonical_by_pos[pos] = (canonical, False)
        # Gate: keep the series only if there's an upcoming installment to get.
        if not any(not is_read for _, is_read in canonical_by_pos.values()):
            continue
        # Keep the highest positions: upcoming installments sort highest, so
        # capping from the top keeps them (and the most recent read context) in
        # favour of early read books. Real primary series never exceed the cap,
        # so this only ever matters as a payload guard.
        positions = sorted(canonical_by_pos)[-SERIES_ENTRIES_CAP:]
        entries = []
        for pos in positions:
            book, is_read = canonical_by_pos[pos]
            entry = hardcover.normalize_book_row(book)
            entry["position"] = pos
            entry["released"] = not _is_unreleased(book, today)
            entry["read"] = is_read
            entries.append(entry)
        series_total = primary_count or (positions[-1] if positions else 0)
        groups_out.append((prog["last_date"], {
            "series_id": s.get("id"),
            "series_name": clean_series_name(s.get("name") or prog.get("name") or ""),
            "series_total": series_total,
            "entries": entries,
        }))
    groups_out.sort(key=lambda g: g[0], reverse=True)
    return [g for _, g in groups_out[:ROW_LIMIT]]


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
    book_series(order_by: {position: asc}, limit: 100) {
      position
      featured
      book {
        id title release_date cached_image compilation users_count
        is_partial_book canonical_id
        contributions(limit: 2) { author { name } }
      }
    }
  }
}
"""

BY_AUTHORS_QUERY = """
query ByAuthors($aids: [Int!]) {
  books(where: {contributions: {author_id: {_in: $aids}}, users_count: {_gte: __MIN_USERS__}},
        order_by: {users_count: desc}, limit: 60) {
    id title release_date users_count cached_image compilation
    contributions(limit: 2) { author { name } }
  }
}
""".replace("__MIN_USERS__", str(AUTHOR_MIN_USERS))


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
