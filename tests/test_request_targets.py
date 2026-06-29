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
