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


def test_availability_misconfigured_never_500(auth_client, monkeypatch):
    class _Boom:
        def get_books(self):
            raise RuntimeError("backend down")

    monkeypatch.setattr(app_module, "get_client", lambda server_type: _Boom())
    monkeypatch.setattr(app_module, "requests_history", [])
    resp = auth_client.get("/api/availability")
    assert resp.status_code == 200
    assert resp.get_json()["ebook"] == {"isbns": [], "titles": []}
