# Hardcover Recommendation Rows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:test-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three Hardcover-driven personal discovery rows (Continue the Series, More From Authors You've Read, On Your Want-to-Read List) to the top of the discovery view, gated on a configured Hardcover token and relevant history.

**Architecture:** New `recommendations.py` holds all query strings, library parsing, and per-row selection/filtering as pure, unit-testable functions plus a `build_all(client)` orchestrator that fetches the Hardcover library once and builds all three rows (never raises). `app.py` stays a thin router: `/api/discover` routes the three personalized keys to a small helper that gates on `get_metadata_client()`, caches via the existing `_discover_cache`, and returns `[]` on any failure. Normalization reuses the shared book schema via a module-level `hardcover.normalize_book_row`. Frontend prepends three category objects when a new `hardcover_enabled` flag (from `/api/config`) is true; empty rows are already hidden by existing loop logic.

**Tech Stack:** Python 3.12, Flask, `requests` (existing); vanilla JS frontend; pytest + ruff (CI gates). No new dependencies.

## Global Constraints

- No new third-party dependencies.
- Do not touch the request/download flow, auth, or ebook/audiobook slot logic.
- Do not expose the Hardcover token to the frontend (only a boolean).
- Keep `app.py` a thin router; logic lives in `recommendations.py`.
- Hardcover status ids: 1 = Want to Read, 2 = Currently Reading, 3 = Read.
- `me` may come back as a list — take `[0]`.
- Max GraphQL query depth 3 (JSON scalar columns like `cached_featured_series` don't add depth; the verified queries in the spec are authoritative).
- Tests use fakes/canned payloads, never real network. `conftest.py` redirects the data dir.
- Row cap ~20 items; per-series cap 3; author-row `users_count >= 50`.
- ruff: py312, line-length 100, rules E4/E7/E9/F/I/B/UP. Both `ruff check .` and `pytest` gate CI.

---

### Task 1: `hardcover.normalize_book_row` module-level helper

**Files:**
- Modify: `hardcover.py` (extract instance method to a module-level function; instance/static methods delegate)
- Test: `tests/test_normalize.py` (add a case)

**Interfaces:**
- Produces: `hardcover.normalize_book_row(book: dict) -> dict` and `hardcover.cover_url(value) -> str`, returning the shared schema keys `{id,title,authors,publishedDate,description,pageCount,categories,isbn_13,isbn_10,cover,language}`. `recommendations.py` consumes `normalize_book_row`.

- [ ] **Step 1: Write the failing test** in `tests/test_normalize.py`:

```python
def test_hardcover_normalize_book_row_module_level():
    from hardcover import normalize_book_row
    out = normalize_book_row({
        "id": 42, "title": "Words of Radiance", "release_date": "2014-03-04",
        "cached_image": {"url": "http://c/x.jpg"},
        "contributions": [{"author": {"name": "Brandon Sanderson"}}],
    })
    assert out["id"] == "42"
    assert out["title"] == "Words of Radiance"
    assert out["authors"] == ["Brandon Sanderson"]
    assert out["publishedDate"] == "2014"
    assert out["cover"] == "http://c/x.jpg"
    assert set(out) == {
        "id", "title", "authors", "publishedDate", "description", "pageCount",
        "categories", "isbn_13", "isbn_10", "cover", "language",
    }
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_normalize.py::test_hardcover_normalize_book_row_module_level -v`
Expected: FAIL with `ImportError: cannot import name 'normalize_book_row'`.

- [ ] **Step 3: Refactor `hardcover.py`** — add module-level functions and delegate from the class:

```python
def cover_url(value) -> str:
    """cached_image / image can be a {url:...} object or a bare string."""
    if isinstance(value, dict):
        return value.get("url", "") or ""
    if isinstance(value, str):
        return value
    return ""


def normalize_book_row(book: dict) -> dict:
    authors = [
        c["author"]["name"]
        for c in (book.get("contributions") or [])
        if c.get("author") and c["author"].get("name")
    ]
    return {
        "id": str(book.get("id", "")),
        "title": book.get("title", "Unknown"),
        "authors": authors,
        "publishedDate": (book.get("release_date") or "")[:4],
        "description": "",
        "pageCount": 0,
        "categories": [],
        "isbn_13": "",
        "isbn_10": "",
        "cover": cover_url(book.get("cached_image")),
        "language": "en",
    }
```
Change the class methods to delegate: `_cover_url` → `return cover_url(value)` (keep as `@staticmethod`), and `_normalize_book_row(self, book)` → `return normalize_book_row(book)`.

- [ ] **Step 4: Run tests to verify pass**
Run: `pytest tests/test_normalize.py -v`
Expected: PASS (new test + existing normalize tests unaffected).

- [ ] **Step 5: Commit**
```bash
git add hardcover.py tests/test_normalize.py
git commit -m "refactor: expose hardcover.normalize_book_row at module level"
```

---

### Task 2: `recommendations.parse_library` + constants

**Files:**
- Create: `recommendations.py`
- Test: `tests/test_recommendations.py`

**Interfaces:**
- Produces:
  - `PERSONALIZED_CATEGORIES = ("continue_series", "more_by_authors", "want_to_read")`
  - `STATUS_WANT=1, STATUS_READING=2, STATUS_READ=3`, `ROW_LIMIT=20`, `PER_SERIES_CAP=3`
  - `parse_library(data: dict) -> Library` where `Library` is a dataclass with fields:
    `read_ids: set[int]`, `reading_ids: set[int]`, `excluded_ids: set[int]`,
    `author_ids: list[int]`, `series_progress: dict[int, dict]`
    (`{series_id: {"furthest": int, "last_date": str, "name": str}}`),
    `want: list[dict]` (`[{"book": <book dict>, "date_added": str}]`).
- Consumes: raw `me { user_books }` GraphQL response.

- [ ] **Step 1: Write failing tests** in `tests/test_recommendations.py`:

```python
import recommendations as rec

_LIB = {"me": [{"user_books": [
    # Read: Way of Kings (series 1, pos 1), author 10
    {"status_id": 3, "date_added": "2026-06-01", "book": {
        "id": 100, "title": "The Way of Kings", "cached_image": {"url": "c1"},
        "cached_featured_series": {"series": {"id": 1, "name": "Stormlight"},
                                   "details": "1"},
        "contributions": [{"author": {"id": 10, "name": "Sanderson"}}]}},
    # Read: Words of Radiance (series 1, pos 2)
    {"status_id": 3, "date_added": "2026-06-05", "book": {
        "id": 101, "title": "Words of Radiance", "cached_image": {"url": "c2"},
        "cached_featured_series": {"series": {"id": 1, "name": "Stormlight"},
                                   "details": "2"},
        "contributions": [{"author": {"id": 10, "name": "Sanderson"}}]}},
    # Currently reading
    {"status_id": 2, "date_added": "2026-06-10", "book": {"id": 200, "title": "R"}},
    # Want to read (two, out of date order)
    {"status_id": 1, "date_added": "2026-05-01", "book": {
        "id": 300, "title": "Old Want", "contributions": [{"author": {"name": "X"}}]}},
    {"status_id": 1, "date_added": "2026-06-20", "book": {
        "id": 301, "title": "New Want", "contributions": [{"author": {"name": "Y"}}]}},
]}]}


def test_parse_library_partitions_by_status():
    lib = rec.parse_library(_LIB)
    assert lib.read_ids == {100, 101}
    assert lib.reading_ids == {200}
    assert lib.excluded_ids == {100, 101, 200}
    assert lib.author_ids == [10]
    assert {w["book"]["id"] for w in lib.want} == {300, 301}


def test_parse_library_series_progress_furthest():
    lib = rec.parse_library(_LIB)
    prog = lib.series_progress[1]
    assert prog["furthest"] == 2
    assert prog["name"] == "Stormlight"
    assert prog["last_date"] == "2026-06-05"


def test_parse_library_handles_me_as_object_and_empty():
    assert rec.parse_library({"me": {"user_books": []}}).read_ids == set()
    assert rec.parse_library({"me": []}).want == []
    assert rec.parse_library({}).author_ids == []
```

- [ ] **Step 2: Run to verify fail**
Run: `pytest tests/test_recommendations.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'recommendations'`.

- [ ] **Step 3: Implement** `recommendations.py` head + `parse_library`:

```python
"""Hardcover-powered personal recommendation rows.

Kept separate from hardcover.py (the metadata/search role) and from app.py (thin
router). All Hardcover query strings, library parsing, and per-row filtering live
here as pure functions plus one `build_all(client)` orchestrator. The Hardcover
token is global (one account) — see the design spec.
"""
import json
import logging
import re
from dataclasses import dataclass, field

import hardcover

logger = logging.getLogger(__name__)

PERSONALIZED_CATEGORIES = ("continue_series", "more_by_authors", "want_to_read")

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
    series_progress: dict = field(default_factory=dict)
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
```

- [ ] **Step 4: Run to verify pass**
Run: `pytest tests/test_recommendations.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add recommendations.py tests/test_recommendations.py
git commit -m "feat: add recommendations.parse_library for Hardcover library"
```

---

### Task 3: `select_continue_series` filtering

**Files:**
- Modify: `recommendations.py`
- Test: `tests/test_recommendations.py`

**Interfaces:**
- Produces: `select_continue_series(library: Library, data: dict) -> list[dict]` where `data` is the raw `series(where:{id:{_in}})` expansion response; returns normalized book dicts (shared schema), next-in-series only.
- Consumes: `Library` from Task 2, `hardcover.normalize_book_row`.

- [ ] **Step 1: Write failing tests**. Append to `tests/test_recommendations.py`:

```python
def _bs(pos, bid, title, users=100, cover="c", compilation=False):
    book = {"id": bid, "title": title, "users_count": users, "compilation": compilation}
    if cover:
        book["cached_image"] = {"url": cover}
    return {"position": pos, "book": book}

_EXPANSION = {"series": [{
    "id": 1, "name": "Stormlight", "books_count": 10, "primary_books_count": 5,
    "book_series": [
        _bs(1, 100, "The Way of Kings"),          # already read
        _bs(2, 101, "Words of Radiance"),         # already read (furthest)
        _bs(2, 999, "Words of Radiance (German Edition)", users=5),  # dup pos, foreign
        _bs(3, 102, "Oathbringer"),               # NEXT
        _bs(3.1, 900, "Edgedancer"),              # fractional -> drop
        _bs(4, 103, "Rhythm of War"),             # NEXT
        _bs(5, 104, "Wind and Truth"),            # NEXT
        _bs(6, 105, "Beyond Primary", ),          # > primary_books_count -> drop
        _bs(4, 700, "Stormlight, Books 1-4", compilation=True),  # compilation -> drop
    ],
}]}


def test_continue_series_next_after_furthest():
    lib = rec.parse_library(_LIB)
    out = rec.select_continue_series(lib, _EXPANSION)
    ids = [b["id"] for b in out]
    # furthest read = 2; primary run <=5; cap 3 per series; pos 3,4,5
    assert ids == ["102", "103", "104"]


def test_continue_series_excludes_and_filters():
    lib = rec.parse_library(_LIB)
    out = rec.select_continue_series(lib, _EXPANSION)
    titles = [b["title"] for b in out]
    assert "Edgedancer" not in titles            # fractional dropped
    assert "Beyond Primary" not in titles         # beyond primary run
    assert all("Books 1-4" not in t for t in titles)  # compilation dropped


def test_continue_series_dedupes_position_keeping_popular():
    lib = rec.parse_library(_LIB)
    # Craft a series where pos 3 has two entries; higher users_count wins.
    data = {"series": [{
        "id": 1, "name": "Stormlight", "books_count": 10, "primary_books_count": 5,
        "book_series": [_bs(3, 102, "Oathbringer", users=500),
                        _bs(3, 888, "Oathbringer (French)", users=3)],
    }]}
    out = rec.select_continue_series(lib, data)
    assert [b["id"] for b in out] == ["102"]


def test_continue_series_orders_series_by_recency():
    # Two series; the one read more recently comes first.
    lib = rec.parse_library({"me": [{"user_books": [
        {"status_id": 3, "date_added": "2026-01-01", "book": {
            "id": 1, "title": "A1",
            "cached_featured_series": {"series": {"id": 1, "name": "A"}, "details": "1"}}},
        {"status_id": 3, "date_added": "2026-06-01", "book": {
            "id": 2, "title": "B1",
            "cached_featured_series": {"series": {"id": 2, "name": "B"}, "details": "1"}}},
    ]}]})
    data = {"series": [
        {"id": 1, "name": "A", "primary_books_count": 3,
         "book_series": [_bs(2, 11, "A2")]},
        {"id": 2, "name": "B", "primary_books_count": 3,
         "book_series": [_bs(2, 22, "B2")]},
    ]}
    out = rec.select_continue_series(lib, data)
    assert [b["id"] for b in out] == ["22", "11"]  # series B (recent) first
```

- [ ] **Step 2: Run to verify fail**
Run: `pytest tests/test_recommendations.py -k continue_series -v`
Expected: FAIL `AttributeError: module 'recommendations' has no attribute 'select_continue_series'`.

- [ ] **Step 3: Implement** in `recommendations.py`:

```python
_BOX_SET_RE = re.compile(
    r"(books?\s+\d+\s*[-–—]\s*\d+|boxed?\s*set|omnibus|\bbundle\b)", re.I)
_ALT_EDITION_RE = re.compile(
    r"\([^)]*\b(?:edition|language|prime|translation)\b[^)]*\)", re.I)


def _has_cover(book):
    return bool(hardcover.cover_url(book.get("cached_image")))


def _rank(book):
    return (book.get("users_count") or 0, 1 if _has_cover(book) else 0)


def _is_noise(title, series_name):
    title = title or ""
    if _BOX_SET_RE.search(title):
        return True
    if _ALT_EDITION_RE.search(title):
        return True
    return False


def select_continue_series(library: Library, data: dict) -> list:
    blocks = []  # (last_date, [book dicts in position order])
    for s in data.get("series") or []:
        prog = library.series_progress.get(s.get("id"))
        if not prog:
            continue
        furthest = prog["furthest"]
        primary_count = s.get("primary_books_count") or 0
        by_pos = {}
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
            if _is_noise(book.get("title", ""), prog["name"]):
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
```

- [ ] **Step 4: Run to verify pass**
Run: `pytest tests/test_recommendations.py -k continue_series -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add recommendations.py tests/test_recommendations.py
git commit -m "feat: next-in-series selection with box-set/edition filtering"
```

---

### Task 4: `select_more_by_authors` and `select_want_to_read`

**Files:**
- Modify: `recommendations.py`
- Test: `tests/test_recommendations.py`

**Interfaces:**
- Produces:
  - `select_more_by_authors(library: Library, data: dict) -> list[dict]` — `data` is the raw `books(where:{contributions...})` response; excludes already-read/reading books and compilations; caps `ROW_LIMIT`.
  - `select_want_to_read(library: Library) -> list[dict]` — status-1 books, most-recently-added first, cap `ROW_LIMIT`.

- [ ] **Step 1: Write failing tests**:

```python
def test_more_by_authors_excludes_read_and_compilations():
    lib = rec.parse_library(_LIB)
    data = {"books": [
        {"id": 101, "title": "Words of Radiance"},          # already read -> drop
        {"id": 200, "title": "R"},                          # currently reading -> drop
        {"id": 500, "title": "Warbreaker", "cached_image": {"url": "c"},
         "contributions": [{"author": {"name": "Sanderson"}}]},   # keep
        {"id": 501, "title": "Sanderson Omnibus", "compilation": True},  # drop
    ]}
    out = rec.select_more_by_authors(lib, data)
    assert [b["id"] for b in out] == ["500"]
    assert out[0]["authors"] == ["Sanderson"]


def test_more_by_authors_caps_and_dedupes():
    lib = rec.parse_library({"me": [{"user_books": []}]})
    books = [{"id": i, "title": f"B{i}"} for i in range(30)]
    books.append({"id": 0, "title": "dup"})  # duplicate id
    out = rec.select_more_by_authors(lib, {"books": books})
    assert len(out) == rec.ROW_LIMIT
    assert len({b["id"] for b in out}) == rec.ROW_LIMIT


def test_want_to_read_recency_order_and_cap():
    lib = rec.parse_library(_LIB)
    out = rec.select_want_to_read(lib)
    assert [b["id"] for b in out] == ["301", "300"]  # newest date_added first
    assert out[0]["authors"] == ["Y"]
```

- [ ] **Step 2: Run to verify fail**
Run: `pytest tests/test_recommendations.py -k "authors or want_to_read" -v`
Expected: FAIL (attributes missing).

- [ ] **Step 3: Implement**:

```python
def select_more_by_authors(library: Library, data: dict) -> list:
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
    items = sorted(library.want, key=lambda w: w.get("date_added") or "", reverse=True)
    return [hardcover.normalize_book_row(w["book"]) for w in items[:ROW_LIMIT]]
```

- [ ] **Step 4: Run to verify pass**
Run: `pytest tests/test_recommendations.py -v`
Expected: PASS (all recommendations tests).

- [ ] **Step 5: Commit**
```bash
git add recommendations.py tests/test_recommendations.py
git commit -m "feat: more-by-authors and want-to-read row selection"
```

---

### Task 5: `build_all` orchestrator + queries

**Files:**
- Modify: `recommendations.py`
- Test: `tests/test_recommendations.py`

**Interfaces:**
- Produces: `build_all(client) -> dict[str, list]` with keys exactly `PERSONALIZED_CATEGORIES`. Fetches the library once via `client._post(LIBRARY_QUERY)`; runs the series-expansion and by-authors queries; each row degrades to `[]` on failure; a library-fetch failure yields all-empty. Never raises.
- Consumes: any object exposing `_post(query, variables=None) -> dict` (the real `HardcoverClient`, or a fake in tests).

- [ ] **Step 1: Write failing tests**:

```python
class _FakeHC:
    """Dispatches _post by query content; returns canned payloads."""
    def __init__(self, expansion=None, authors=None, raise_on=None):
        self.expansion = expansion if expansion is not None else _EXPANSION
        self.authors = authors if authors is not None else {"books": []}
        self.raise_on = raise_on or set()

    def _post(self, query, variables=None):
        if "user_books" in query:
            if "library" in self.raise_on:
                raise ValueError("boom")
            return _LIB
        if "series(" in query or "book_series" in query:
            if "series" in self.raise_on:
                raise ValueError("boom")
            return self.expansion
        if "books(" in query:
            return self.authors
        return {}


def test_build_all_returns_three_rows():
    out = rec.build_all(_FakeHC())
    assert set(out) == set(rec.PERSONALIZED_CATEGORIES)
    assert [b["id"] for b in out["continue_series"]] == ["102", "103", "104"]
    assert [b["id"] for b in out["want_to_read"]] == ["301", "300"]


def test_build_all_library_failure_all_empty():
    out = rec.build_all(_FakeHC(raise_on={"library"}))
    assert out == {c: [] for c in rec.PERSONALIZED_CATEGORIES}


def test_build_all_row_failure_isolated():
    out = rec.build_all(_FakeHC(raise_on={"series"}))
    assert out["continue_series"] == []          # this row degraded
    assert [b["id"] for b in out["want_to_read"]] == ["301", "300"]  # others fine
```

- [ ] **Step 2: Run to verify fail**
Run: `pytest tests/test_recommendations.py -k build_all -v`
Expected: FAIL (`build_all` missing).

- [ ] **Step 3: Implement** query constants + `build_all`:

```python
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
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - a row failure must never 500 the page
        logger.warning("recommendation row %s failed: %s", name, e)
        return []


def build_all(client) -> dict:
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
```

- [ ] **Step 4: Run to verify pass**
Run: `pytest tests/test_recommendations.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add recommendations.py tests/test_recommendations.py
git commit -m "feat: build_all orchestrator fetches library once, degrades safely"
```

---

### Task 6: Wire `/api/discover` + `hardcover_enabled` in `app.py`

**Files:**
- Modify: `app.py` (import `recommendations`; `discover_books` route; `_discover_personalized` helper; `get_config` payload)
- Test: `tests/test_discover_recommendations.py` (new)

**Interfaces:**
- Consumes: `recommendations.PERSONALIZED_CATEGORIES`, `recommendations.build_all`.
- Produces: `GET /api/discover?category=<personal key>` returns a normalized list (`[]` when no token), cached in `_discover_cache` keyed `("hardcover", category)`; `GET /api/config` includes `"hardcover_enabled": bool`.

- [ ] **Step 1: Write failing tests** in `tests/test_discover_recommendations.py`:

```python
"""Personalized discover rows: gated on a metadata client, never 500."""
import app as app_module
import recommendations


@pytest.fixture
def auth_client(flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "LOGIN_DISABLED", True)
    monkeypatch.setattr(app_module, "load_requests", lambda: None)
    monkeypatch.setattr(app_module, "_discover_cache", {})
    return flask_app.test_client()


import pytest  # noqa: E402


def test_personalized_gated_off_without_token(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_metadata_client", lambda: None)
    resp = auth_client.get("/api/discover?category=continue_series")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_personalized_returns_rows_with_client(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_metadata_client", lambda: object())
    monkeypatch.setattr(recommendations, "build_all", lambda client: {
        "continue_series": [{"id": "1", "title": "Next"}],
        "more_by_authors": [], "want_to_read": [],
    })
    resp = auth_client.get("/api/discover?category=continue_series")
    assert resp.status_code == 200
    assert [b["id"] for b in resp.get_json()] == ["1"]


def test_personalized_never_500(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_metadata_client", lambda: object())
    # build_all itself never raises, but assert the route surfaces []-safe data.
    monkeypatch.setattr(recommendations, "build_all",
                        lambda client: {c: [] for c in recommendations.PERSONALIZED_CATEGORIES})
    resp = auth_client.get("/api/discover?category=want_to_read")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_config_exposes_hardcover_enabled(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "config", {
        "ebook": {}, "audiobook": {}, "hardcover": {"token": "eyX"}})
    resp = auth_client.get("/api/config")
    assert resp.get_json()["hardcover_enabled"] is True
```

- [ ] **Step 2: Run to verify fail**
Run: `pytest tests/test_discover_recommendations.py -v`
Expected: FAIL (route returns 400 for unknown category / no `hardcover_enabled` key).

- [ ] **Step 3: Implement in `app.py`.** Add `import recommendations` with the other local imports. In `discover_books`, before the existing category validation:

```python
    category = request.args.get("category", "").strip()
    if category in recommendations.PERSONALIZED_CATEGORIES:
        return _discover_personalized(category)
    if not category or category not in _DISCOVER_CATEGORIES:
        return jsonify({"error": "Invalid category"}), 400
```

Add the helper next to `discover_books`:

```python
def _discover_personalized(category):
    """Hardcover-only personal rows. Gated on a token; failures degrade to []."""
    client = get_metadata_client()
    if not client:
        return jsonify([])
    cache_key = ("hardcover", category)
    cached = _discover_cache.get(cache_key)
    if cached and (time.time() - cached[0]) < _DISCOVER_CACHE_TTL:
        return jsonify(cached[1])
    rows = recommendations.build_all(client)  # never raises
    now = time.time()
    for cat, items in rows.items():
        _discover_cache[("hardcover", cat)] = (now, items)
    return jsonify(rows.get(category, []))
```

In `get_config`, add to the returned dict:

```python
        "hardcover_enabled": bool(config.get("hardcover", {}).get("token")),
```

- [ ] **Step 4: Run to verify pass**
Run: `pytest tests/test_discover_recommendations.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add app.py tests/test_discover_recommendations.py
git commit -m "feat: route personalized discover rows and expose hardcover_enabled"
```

---

### Task 7: Frontend rows (gated, hide-on-empty)

**Files:**
- Modify: `static/js/app.js`

**Interfaces:**
- Consumes: `/api/config` `hardcover_enabled`; `/api/discover?category=<personal key>`.

- [ ] **Step 1: Add** the personalized category list + flag near `DISCOVERY_CATEGORIES`:

```javascript
const PERSONAL_CATEGORIES = [
    { key: "continue_series", title: "Continue the Series" },
    { key: "more_by_authors", title: "More From Authors You've Read" },
    { key: "want_to_read", title: "On Your Want-to-Read List" },
];
let hardcoverEnabled = false;
```

- [ ] **Step 2: Set the flag** in `loadConfig` (after `serverConfigured = ...`):

```javascript
        hardcoverEnabled = !!data.hardcover_enabled;
```

- [ ] **Step 3: Prepend rows** in `loadDiscovery` — replace `DISCOVERY_CATEGORIES.map` with:

```javascript
    const categories = hardcoverEnabled
        ? [...PERSONAL_CATEGORIES, ...DISCOVERY_CATEGORIES]
        : DISCOVERY_CATEGORIES;
    const promises = categories.map(async (cat) => {
```

(The existing `if (data.error || !data.length) return null;` already hides empty rows; the existing cross-row dedupe keeps personalized rows first, so they win over the generic rows.)

- [ ] **Step 4: Await config before discovery** in the init block so the flag is set first:

```javascript
loadCurrentUser().then(async () => {
    await loadConfig();
    loadDiscovery();
});
```

- [ ] **Step 5: Commit**
```bash
git add static/js/app.js
git commit -m "feat: render Hardcover personal rows at top of discovery"
```

---

### Task 8: Full gate + browser verification

**Files:** none (verification only; fixes folded into the relevant task above if found)

- [ ] **Step 1: Lint** — `ruff check .` → expect clean.
- [ ] **Step 2: Tests** — `pytest -v -rA` → expect all pass.
- [ ] **Step 3: Run the app** — `python app.py` (data/config.json already has a Hardcover token; never print it).
- [ ] **Step 4: Browser** — log in, open Discover, confirm the three rows render at the top with real books; spot-check Continue-the-Series shows next-in-series (not already-read) entries and no obvious box sets/foreign editions. Capture what is actually on screen.
- [ ] **Step 5:** If a row is unexpectedly empty or wrong, debug against live data (log the raw query result, do not print the token); fold the fix into the owning task and re-run steps 1–4. If it can't be resolved in scope, add a `TODO.md` entry (what/where/what tried) and report.

---

## Self-Review

- **Spec coverage:** three rows (Tasks 3–5, 7) ✓; gating on token + history (Task 6 `_discover_personalized`, Task 7 flag, empty-row hide) ✓; next-in-series + primary/compilation/foreign/already-read filtering (Task 3) ✓; dedupe by id and (series,position) (Task 3 `by_pos` + `seen`) ✓; author row excludes read + compilations (Task 4) ✓; want-to-read recency (Task 4) ✓; shared normalized schema (Task 1 reuse) ✓; library fetched once (Task 5 `build_all`) ✓; existing cache reuse keyed `(source, category)` (Task 6) ✓; failures → empty, never 500 (Task 5 `_safe_row` + Task 6) ✓; no token exposure (Task 6 boolean only) ✓; no new deps ✓; app.py thin (Task 6) ✓; unit tests per spec Testing section (Tasks 2–6) ✓; endpoint gated/present tests (Task 6) ✓; browser verification (Task 8) ✓.
- **Type consistency:** `Library` fields and `build_all` dict keys (`PERSONALIZED_CATEGORIES`) match across Tasks 2–6; `normalize_book_row` name consistent (Tasks 1,3,4); ids compared as ints in selectors, emitted as str by normalize (dedupe in JS uses the str ids consistently).
- **Placeholder scan:** all code steps contain concrete code; no TODO/TBD.
