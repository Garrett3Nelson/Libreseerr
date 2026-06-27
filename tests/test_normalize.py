"""Tier 2: Open Library normalizers (pure functions, no network).

Inputs are representative Open Library API payloads; we assert the shape our
code produces. These are not mocks of OL behavior, just fixtures fed to our
transform, so they don't go brittle when OL's service changes.
"""
from app import _normalize_ol_doc, _normalize_ol_subject_work

EXPECTED_KEYS = {
    "id", "title", "authors", "publishedDate", "description", "pageCount",
    "categories", "isbn_13", "isbn_10", "cover", "language",
}


def test_normalize_ol_doc_full():
    doc = {
        "key": "/works/OL45804W",
        "title": "Fantastic Mr Fox",
        "author_name": ["Roald Dahl"],
        "first_publish_year": 1970,
        "number_of_pages_median": 96,
        "subject": ["Foxes", "Fiction", "Farmers", "Hunting", "Animals", "Extra"],
        "isbn": ["9780140328721", "0140328726"],
        "cover_i": 8739161,
        "language": ["eng"],
    }
    out = _normalize_ol_doc(doc)
    assert set(out) == EXPECTED_KEYS
    assert out["id"] == "OL45804W"
    assert out["title"] == "Fantastic Mr Fox"
    assert out["authors"] == ["Roald Dahl"]
    assert out["publishedDate"] == "1970"
    assert out["pageCount"] == 96
    assert out["isbn_13"] == "9780140328721"
    assert out["isbn_10"] == "0140328726"
    assert out["cover"] == "https://covers.openlibrary.org/b/id/8739161-M.jpg"
    assert out["language"] == "eng"
    assert len(out["categories"]) == 5  # capped at 5


def test_normalize_ol_doc_minimal_defaults():
    out = _normalize_ol_doc({})
    assert set(out) == EXPECTED_KEYS
    assert out["title"] == "Unknown"
    assert out["authors"] == []
    assert out["publishedDate"] == ""
    assert out["pageCount"] == 0
    assert out["categories"] == []
    assert out["isbn_13"] == ""
    assert out["isbn_10"] == ""
    assert out["cover"] == ""
    assert out["language"] == "en"  # fallback


def test_normalize_ol_doc_isbn_fallback_when_no_10_or_13():
    # An ISBN that is neither length 10 nor 13 still populates isbn_13.
    out = _normalize_ol_doc({"isbn": ["123456"]})
    assert out["isbn_13"] == "123456"


def test_normalize_ol_subject_work():
    work = {
        "key": "/works/OL123W",
        "title": "Dune",
        "authors": [{"name": "Frank Herbert"}, {"name": ""}],
        "first_publish_year": 1965,
        "cover_id": 999,
    }
    out = _normalize_ol_subject_work(work)
    assert set(out) == EXPECTED_KEYS
    assert out["id"] == "OL123W"
    assert out["title"] == "Dune"
    assert out["authors"] == ["Frank Herbert"]  # blank name dropped
    assert out["publishedDate"] == "1965"
    assert out["cover"] == "https://covers.openlibrary.org/b/id/999-M.jpg"


def test_normalize_ol_subject_work_minimal():
    out = _normalize_ol_subject_work({})
    assert set(out) == EXPECTED_KEYS
    assert out["title"] == "Unknown"
    assert out["authors"] == []
    assert out["cover"] == ""
    assert out["language"] == "en"
