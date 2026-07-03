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


def test_continue_series_returns_grouped_structure(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_metadata_client", lambda: object())
    monkeypatch.setattr(recommendations, "build_all", lambda client: {
        "continue_series": [{
            "series_id": 1, "series_name": "Stormlight",
            "entries": [{"id": "102", "title": "Oathbringer", "position": 3,
                         "released": True, "match_title": "oathbringer|"}],
        }],
        "more_by_authors": [], "want_to_read": [],
    })
    resp = auth_client.get("/api/discover?category=continue_series")
    assert resp.status_code == 200
    groups = resp.get_json()
    assert groups[0]["series_id"] == 1
    assert groups[0]["series_name"] == "Stormlight"
    assert groups[0]["entries"][0]["position"] == 3
    assert groups[0]["entries"][0]["released"] is True


def test_flat_rows_stay_flat(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_metadata_client", lambda: object())
    monkeypatch.setattr(recommendations, "build_all", lambda client: {
        "continue_series": [],
        "more_by_authors": [{"id": "500", "title": "Warbreaker", "match_title": "warbreaker|"}],
        "want_to_read": [],
    })
    resp = auth_client.get("/api/discover?category=more_by_authors")
    assert resp.status_code == 200
    rows = resp.get_json()
    assert rows[0]["id"] == "500"
    assert "entries" not in rows[0]


def test_continue_series_empty_is_empty_list(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_metadata_client", lambda: object())
    monkeypatch.setattr(recommendations, "build_all",
                        lambda client: {c: [] for c in recommendations.PERSONALIZED_CATEGORIES})
    resp = auth_client.get("/api/discover?category=continue_series")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_config_exposes_hardcover_enabled(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "config", {
        "ebook": {}, "audiobook": {}, "hardcover": {"token": "eyX"}})
    resp = auth_client.get("/api/config")
    assert resp.get_json()["hardcover_enabled"] is True
