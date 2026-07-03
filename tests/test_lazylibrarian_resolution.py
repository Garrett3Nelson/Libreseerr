"""Tier 2: LazyLibrarian request-resolution hardening (pure functions, no network).

Covers the upstream fix for picking the real book over summary/study-guide
editions and rejecting non-LazyLibrarian ids instead of silently grabbing the
wrong book. See zamnzim/Libreseerr PR #11.
"""
from lazylibrarian import _is_junk_title, _looks_like_ll_id, _rank


def test_is_junk_title_flags_summaries_and_guides():
    assert _is_junk_title("Book Summary of THE MARTIAN by Andy Weir")
    assert _is_junk_title("Study Guide: Dune")
    assert _is_junk_title("Key Takeaways from Atomic Habits")
    assert not _is_junk_title("The Martian")
    assert not _is_junk_title("Dune")


def test_looks_like_ll_id_only_accepts_numeric_ids():
    assert _looks_like_ll_id("18007564")
    assert not _looks_like_ll_id("OL20823239W")  # Open Library work id
    assert not _looks_like_ll_id("")
    assert not _looks_like_ll_id(None)


def _book(title, author="", book_id="1"):
    return {
        "title": title,
        "author": {"authorName": author},
        "foreignBookId": book_id,
    }


def test_rank_drops_junk_and_prefers_real_match():
    books = [
        _book("Book Summary of THE MARTIAN", "Instaread", "111"),
        _book("The Martian", "Andy Weir", "18007564"),
    ]
    ranked = _rank(books, "The Martian Andy Weir")
    assert len(ranked) == 1  # summary dropped
    assert ranked[0]["foreignBookId"] == "18007564"


def test_rank_skips_entries_without_book_id():
    books = [_book("The Martian", "Andy Weir", "")]
    assert _rank(books, "The Martian") == []
