"""Personalized discover rows: gated on a metadata client, never 500."""
import pytest

import app as app_module
import recommendations


@pytest.fixture
def auth_client(flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "LOGIN_DISABLED", True)
    monkeypatch.setattr(app_module, "load_requests", lambda: None)
    monkeypatch.setattr(app_module, "_discover_cache", {})
    return flask_app.test_client()


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
