"""Tier 1: the app boots and its routes don't 500.

This is the highest-value, lowest-maintenance check for a thin-router app: it
catches the "won't import / route crashes on boot" class that actually breaks
deploys, without mocking any external service.
"""


def test_app_imports():
    import app  # noqa: F401


def test_login_page_ok(client):
    resp = client.get("/login")
    assert resp.status_code == 200


def test_index_redirects_when_unauthenticated(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


def test_api_me_unauthenticated_is_401_not_500(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_auth_providers_public_endpoint(client):
    resp = client.get("/api/auth/providers")
    assert resp.status_code == 200
    assert resp.is_json
