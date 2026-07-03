"""Tier 2: /api/request multi-target (request both formats) behavior."""
import pytest

import app as app_module


class _FakeClient:
    """Duck-typed stand-in for a backend client."""

    def __init__(self, add_raises=False, found=True):
        self.add_raises = add_raises
        self.found = found

    def _hit(self):
        return [{"title": "X", "author": {"authorName": "A"}, "foreignBookId": "1"}] if self.found else []

    def lookup_by_isbn(self, isbn):
        return self._hit()

    def search_books(self, query):
        return self._hit()

    def add_book(self, book, quality_profile_id, root_folder):
        if self.add_raises:
            raise RuntimeError("backend boom")
        return {"id": 42}


def test_single_request_success(monkeypatch):
    monkeypatch.setattr(app_module, "get_client", lambda st: _FakeClient())
    book = {"title": "Dune", "authors": ["Frank Herbert"], "isbn_13": "9780441013593"}
    entry = app_module._create_single_request("ebook", book, 1, "/books")
    assert entry["status"] == "processing"
    assert entry["server_type"] == "ebook"
    assert entry["readarr_book_id"] == 42


def test_single_request_add_book_error(monkeypatch):
    monkeypatch.setattr(app_module, "get_client", lambda st: _FakeClient(add_raises=True))
    book = {"title": "Dune", "authors": ["Frank Herbert"]}
    entry = app_module._create_single_request("audiobook", book, 2, "/audio")
    assert entry["status"] == "error"
    assert "boom" in entry["error"]


def test_single_request_no_client(monkeypatch):
    monkeypatch.setattr(app_module, "get_client", lambda st: None)
    entry = app_module._create_single_request("ebook", {"title": "X"}, 1, "/books")
    assert entry["status"] == "error"
    assert "not configured" in entry["error"]


@pytest.fixture
def auth_client(flask_app, monkeypatch):
    """Test client with auth bypassed and persistence/reload neutralized so the
    route mutates an in-memory requests_history we control."""
    monkeypatch.setitem(flask_app.config, "LOGIN_DISABLED", True)
    monkeypatch.setattr(app_module, "save_requests", lambda: None)
    monkeypatch.setattr(app_module, "load_requests", lambda: None)  # reload_state no-op
    monkeypatch.setattr(app_module, "requests_history", [])
    return flask_app.test_client()


_BOOK = {"title": "Dune", "authors": ["Frank Herbert"]}


def test_request_both_creates_two_entries(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_client", lambda st: _FakeClient())
    resp = auth_client.post("/api/request", json={
        "book": _BOOK,
        "targets": [
            {"server_type": "ebook", "quality_profile_id": 1, "root_folder": "/books"},
            {"server_type": "audiobook", "quality_profile_id": 1, "root_folder": "/audio"},
        ],
    })
    assert resp.status_code == 200
    entries = resp.get_json()
    assert len(entries) == 2
    assert {e["server_type"] for e in entries} == {"ebook", "audiobook"}
    assert all(e["status"] == "processing" for e in entries)
    assert entries[0]["id"] != entries[1]["id"]  # unique ids


def test_request_isolates_partial_failure(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_client",
                        lambda st: _FakeClient(add_raises=(st == "audiobook")))
    resp = auth_client.post("/api/request", json={
        "book": _BOOK,
        "targets": [
            {"server_type": "ebook", "quality_profile_id": 1, "root_folder": "/books"},
            {"server_type": "audiobook", "quality_profile_id": 1, "root_folder": "/audio"},
        ],
    })
    assert resp.status_code == 200
    by_type = {e["server_type"]: e for e in resp.get_json()}
    assert by_type["ebook"]["status"] == "processing"
    assert by_type["audiobook"]["status"] == "error"


def test_request_single_target_still_works(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_client", lambda st: _FakeClient())
    resp = auth_client.post("/api/request", json={
        "book": _BOOK,
        "targets": [{"server_type": "ebook", "quality_profile_id": 1, "root_folder": "/books"}],
    })
    assert resp.status_code == 200
    assert len(resp.get_json()) == 1


def test_request_empty_targets_400(auth_client):
    resp = auth_client.post("/api/request", json={"book": _BOOK, "targets": []})
    assert resp.status_code == 400


def test_request_unconfigured_slot_400(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_client",
                        lambda st: None if st == "audiobook" else _FakeClient())
    resp = auth_client.post("/api/request", json={
        "book": _BOOK,
        "targets": [{"server_type": "audiobook", "quality_profile_id": 1, "root_folder": "/a"}],
    })
    assert resp.status_code == 400
    assert "not configured" in resp.get_json()["error"]


def test_request_missing_profile_400(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_client", lambda st: _FakeClient())
    resp = auth_client.post("/api/request", json={
        "book": _BOOK,
        "targets": [{"server_type": "ebook", "root_folder": "/books"}],  # no quality_profile_id
    })
    assert resp.status_code == 400
