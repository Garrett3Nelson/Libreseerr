"""Unit tests for matching.py (pure, empty-safe title/author normalization)."""
import matching


def test_normalize_title_lowercases_and_collapses_whitespace():
    assert matching.normalize_title("  The   Way   Of  Kings ") == "way of kings"


def test_normalize_title_drops_subtitle_after_colon():
    assert matching.normalize_title("Dune: Part One") == "dune"


def test_normalize_title_drops_parenthetical_qualifier():
    assert matching.normalize_title("Oathbringer (The Stormlight Archive)") == "oathbringer"


def test_normalize_title_strips_leading_article():
    assert matching.normalize_title("The Hobbit") == "hobbit"
    assert matching.normalize_title("A Game of Thrones") == "game of thrones"
    assert matching.normalize_title("An Ember in the Ashes") == "ember in the ashes"


def test_normalize_title_keeps_article_when_not_leading():
    assert matching.normalize_title("Fear the Reaper") == "fear the reaper"


def test_normalize_title_strips_punctuation_and_unicode_dashes_quotes():
    assert matching.normalize_title("Ender’s Game—Special") == "enders game special"


def test_normalize_title_empty_and_none_safe():
    assert matching.normalize_title("") == ""
    assert matching.normalize_title(None) == ""
    assert matching.normalize_title("The") == ""


def test_match_key_composes_title_and_author():
    assert matching.match_key("The Hobbit", "J.R.R. Tolkien") == "hobbit|jrr tolkien"


def test_match_key_author_absent_is_title_only():
    assert matching.match_key("The Hobbit") == "hobbit|"
    assert matching.match_key("The Hobbit", "") == "hobbit|"
    assert matching.match_key("The Hobbit", None) == "hobbit|"
