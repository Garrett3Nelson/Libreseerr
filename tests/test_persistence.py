"""Tier 2: config/requests/users persist and reload faithfully (round-trip)."""
import app as app_module


def test_config_round_trip(monkeypatch, tmp_path):
    f = tmp_path / "config.json"
    monkeypatch.setattr(app_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "CONFIG_FILE", str(f))
    sentinel = {"ebook": {"url": "http://e", "api_key": "k"}, "hardcover": {"token": "t"}}
    monkeypatch.setattr(app_module, "config", sentinel)

    app_module.save_config()
    # Wipe in-memory state, then reload from disk.
    monkeypatch.setattr(app_module, "config", {})
    app_module.load_config()
    assert app_module.config == sentinel


def test_requests_round_trip(monkeypatch, tmp_path):
    f = tmp_path / "requests.json"
    monkeypatch.setattr(app_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "REQUESTS_FILE", str(f))
    sentinel = [{"id": "1", "title": "Book"}]
    monkeypatch.setattr(app_module, "requests_history", sentinel)

    app_module.save_requests()
    monkeypatch.setattr(app_module, "requests_history", [])
    app_module.load_requests()
    assert app_module.requests_history == sentinel


def test_users_round_trip(monkeypatch, tmp_path):
    f = tmp_path / "users.json"
    monkeypatch.setattr(app_module, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "USERS_FILE", str(f))
    sentinel = [{"username": "alice", "role": "admin"}]
    monkeypatch.setattr(app_module, "users", sentinel)

    app_module.save_users()
    monkeypatch.setattr(app_module, "users", [])
    app_module.load_users()
    assert app_module.users == sentinel


def test_load_missing_file_keeps_state(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "CONFIG_FILE", str(tmp_path / "nope.json"))
    sentinel = {"ebook": {}}
    monkeypatch.setattr(app_module, "config", sentinel)
    app_module.load_config()  # file absent -> no-op
    assert app_module.config == sentinel
