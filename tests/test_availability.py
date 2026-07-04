"""/api/availability returns normalized match keys plus ISBNs; empty-safe."""
import pytest

import app as app_module
import matching


@pytest.fixture
def auth_client(flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "LOGIN_DISABLED", True)
    monkeypatch.setattr(app_module, "load_requests", lambda: None)
    return flask_app.test_client()


class _FakeClient:
    def __init__(self, books):
        self._books = books

    def get_books(self):
        return self._books


def test_availability_returns_match_keys_and_isbns(auth_client, monkeypatch):
    ebook_books = [{
        "title": "The Way of Kings",
        "author": {"authorName": "Brandon Sanderson"},
        "editions": [{"isbn13": "9780765326355"}],
        "statistics": {"bookFileCount": 1},  # actually downloaded
    }]

    def fake_get_client(server_type):
        return _FakeClient(ebook_books) if server_type == "ebook" else None

    monkeypatch.setattr(app_module, "get_client", fake_get_client)
    monkeypatch.setattr(app_module, "requests_history", [])
    resp = auth_client.get("/api/availability")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "9780765326355" in data["ebook"]["isbns"]
    # Full match key AND title-only key are both present.
    assert matching.match_key("The Way of Kings", "Brandon Sanderson") in data["ebook"]["titles"]
    assert matching.match_key("The Way of Kings") in data["ebook"]["titles"]
    # Unconfigured slot is empty and safe.
    assert data["audiobook"] == {"isbns": [], "titles": []}


def test_availability_request_titles_are_match_keys(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_client", lambda server_type: None)
    monkeypatch.setattr(app_module, "requests_history", [
        {"status": "pending", "server_type": "audiobook",
         "title": "Words of Radiance", "author": "Brandon Sanderson", "isbn": ""},
    ])
    resp = auth_client.get("/api/availability")
    data = resp.get_json()
    keys = data["audiobook_requests"]["titles"]
    assert matching.match_key("Words of Radiance", "Brandon Sanderson") in keys
    assert matching.match_key("Words of Radiance") in keys


def test_availability_excludes_catalog_books_without_a_file(auth_client, monkeypatch):
    # Readarr/Bookshelf get_books() returns the whole catalog, including metadata
    # stubs a user never downloaded (often auto-created during an author refresh):
    # monitored may be False and statistics.bookFileCount == 0. Those are NOT owned
    # and must not appear in availability — otherwise a book shows a false
    # "eBook ✓" / "Audiobook ✓" badge. Regression: Murderbot 4.5 "Home".
    ebook_books = [
        {"title": "Owned Book", "author": {"authorName": "A"},
         "editions": [{"isbn13": "1111111111111"}], "statistics": {"bookFileCount": 2}},
        {"title": "Catalog Stub", "author": {"authorName": "B"},
         "editions": [{"isbn13": "2222222222222"}],
         "monitored": False, "statistics": {"bookFileCount": 0}},
        # Real live shape of an auto-created stub: editions is null (would fall
        # through to the flat branch), so the file gate must run before dispatch.
        {"title": "Null Editions Stub", "author": {"authorName": "C"},
         "editions": None, "monitored": False, "statistics": {"bookFileCount": 0}},
    ]
    monkeypatch.setattr(app_module, "get_client",
                        lambda s: _FakeClient(ebook_books) if s == "ebook" else None)
    monkeypatch.setattr(app_module, "requests_history", [])
    data = auth_client.get("/api/availability").get_json()
    titles = data["ebook"]["titles"]
    isbns = data["ebook"]["isbns"]
    assert matching.match_key("Owned Book") in titles       # has a file -> owned
    assert "1111111111111" in isbns
    assert matching.match_key("Catalog Stub") not in titles  # no file -> excluded
    assert "2222222222222" not in isbns
    assert matching.match_key("Null Editions Stub") not in titles  # null editions -> excluded


def test_availability_misconfigured_never_500(auth_client, monkeypatch):
    class _Boom:
        def get_books(self):
            raise RuntimeError("backend down")

    monkeypatch.setattr(app_module, "get_client", lambda server_type: _Boom())
    monkeypatch.setattr(app_module, "requests_history", [])
    resp = auth_client.get("/api/availability")
    assert resp.status_code == 200
    assert resp.get_json()["ebook"] == {"isbns": [], "titles": []}
