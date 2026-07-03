"""Unit tests for recommendations.py (Hardcover personal rows).

Repo convention: canned payloads, no network. These mirror the shapes verified
in the design spec (docs/superpowers/specs/2026-07-03-...).
"""
import recommendations as rec

_LIB = {"me": [{"user_books": [
    # Read: The Way of Kings (series 1, pos 1), author 10
    {"status_id": 3, "date_added": "2026-06-01", "book": {
        "id": 100, "title": "The Way of Kings", "cached_image": {"url": "c1"},
        "cached_featured_series": {"series": {"id": 1, "name": "Stormlight"},
                                   "details": "1"},
        "contributions": [{"author": {"id": 10, "name": "Sanderson"}}]}},
    # Read: Words of Radiance (series 1, pos 2) -> furthest
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


def test_clean_series_name_strips_order_qualifier():
    assert rec.clean_series_name("Ender's Game (Publication Order)") == "Ender's Game"


def test_clean_series_name_strips_various_qualifiers():
    assert rec.clean_series_name("Discworld (Chronological Order)") == "Discworld"
    assert rec.clean_series_name("Foundation (Omnibus)") == "Foundation"


def test_clean_series_name_strips_trailing_series_word():
    assert rec.clean_series_name("Stormlight Archive Series") == "Stormlight Archive"


def test_clean_series_name_noop_on_clean_name():
    assert rec.clean_series_name("The Wheel of Time") == "The Wheel of Time"


def test_clean_series_name_empty_safe():
    assert rec.clean_series_name("") == ""
    assert rec.clean_series_name(None) == ""


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
        _bs(6, 105, "Beyond Primary"),            # > primary_books_count -> drop
        _bs(4, 700, "Stormlight, Books 1-4", users=5, compilation=True),  # less popular comp
    ],
}]}


def test_continue_series_next_after_furthest():
    lib = rec.parse_library(_LIB)
    out = rec.select_continue_series(lib, _EXPANSION)
    ids = [b["id"] for b in out]
    # furthest read = 2; primary run <=5; cap 3 per series -> positions 3,4,5
    assert ids == ["102", "103", "104"]


def test_continue_series_excludes_and_filters():
    lib = rec.parse_library(_LIB)
    out = rec.select_continue_series(lib, _EXPANSION)
    titles = [b["title"] for b in out]
    assert "Edgedancer" not in titles              # fractional dropped
    assert "Beyond Primary" not in titles          # beyond primary run
    assert all("Books 1-4" not in t for t in titles)  # compilation dropped


def test_continue_series_dedupes_position_keeping_popular():
    lib = rec.parse_library(_LIB)
    data = {"series": [{
        "id": 1, "name": "Stormlight", "books_count": 10, "primary_books_count": 5,
        "book_series": [_bs(3, 102, "Oathbringer", users=500),
                        _bs(3, 888, "Oathbringer (French)", users=3)],
    }]}
    out = rec.select_continue_series(lib, data)
    assert [b["id"] for b in out] == ["102"]


def test_continue_series_orders_series_by_recency():
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
    assert [b["id"] for b in out] == ["22", "11"]  # series B (more recent) first


def test_continue_series_excludes_whole_position_of_read_edition():
    # The user read the English edition at position 3 (id 50). A foreign edition
    # (id 51) shares position 3. Neither should be recommended — reading a book
    # means you've read that installment regardless of edition.
    lib = rec.parse_library({"me": [{"user_books": [
        {"status_id": 3, "date_added": "2026-01-01", "book": {
            "id": 50, "title": "Book Three (English)",
            "cached_featured_series": {"series": {"id": 9, "name": "S"}, "details": "1"}}},
    ]}]})
    data = {"series": [{"id": 9, "name": "S", "primary_books_count": 5, "book_series": [
        _bs(2, 60, "Book Two"),
        _bs(3, 50, "Book Three (English)"),   # already read -> excluded by id
        _bs(3, 51, "Buch Drei", users=0),     # foreign ed. of a read book -> drop
        _bs(4, 70, "Book Four"),
    ]}]}
    out = rec.select_continue_series(lib, data)
    ids = [b["id"] for b in out]
    assert "51" not in ids
    assert ids == ["60", "70"]


def test_continue_series_drops_position_when_canonical_is_compilation():
    # The most-popular edition at a position is a compilation (e.g. an omnibus
    # fix-up). A less-popular foreign edition shares the slot. The whole position
    # must be dropped — do NOT substitute the foreign edition.
    lib = rec.parse_library({"me": [{"user_books": [
        {"status_id": 3, "date_added": "2026-01-01", "book": {
            "id": 1, "title": "One",
            "cached_featured_series": {"series": {"id": 7, "name": "S"}, "details": "1"}}},
    ]}]})
    data = {"series": [{"id": 7, "name": "S", "primary_books_count": 5, "book_series": [
        _bs(2, 20, "Collected Two", users=687, compilation=True),  # canonical, compilation
        _bs(2, 21, "Zwei", users=0),                               # foreign, non-comp
    ]}]}
    out = rec.select_continue_series(lib, data)
    assert out == []


def test_continue_series_drops_unreleased_next_book():
    # An announced-but-unreleased next book (future release_date) is not
    # requestable and must not be recommended; a released one is kept.
    lib = rec.parse_library({"me": [{"user_books": [
        {"status_id": 3, "date_added": "2026-01-01", "book": {
            "id": 1, "title": "One",
            "cached_featured_series": {"series": {"id": 7, "name": "S"}, "details": "1"}}},
    ]}]})
    data = {"series": [{"id": 7, "name": "S", "primary_books_count": 5, "book_series": [
        {"position": 2, "book": {"id": 30, "title": "Untitled S #2", "users_count": 100,
                                 "release_date": "2999-01-01", "cached_image": {"url": "c"}}},
        {"position": 3, "book": {"id": 31, "title": "Real Three", "users_count": 50,
                                 "release_date": "2020-01-01", "cached_image": {"url": "c"}}},
    ]}]}
    out = rec.select_continue_series(lib, data)
    assert [b["id"] for b in out] == ["31"]


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
        if "book_series" in query:
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
