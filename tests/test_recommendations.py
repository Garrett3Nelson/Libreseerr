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
