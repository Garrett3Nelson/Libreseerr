"""Tier 2: client factory selects the right client per configuration."""
import app as app_module
from app import get_client, get_metadata_client
from bookshelf import BookshelfClient
from hardcover import HardcoverClient
from lazylibrarian import LazyLibrarianClient
from readarr import ReadarrClient


def _set_config(monkeypatch, cfg):
    monkeypatch.setattr(app_module, "config", cfg)


def test_get_client_defaults_to_readarr(monkeypatch):
    _set_config(monkeypatch, {"ebook": {"url": "http://x", "api_key": "k"}})
    assert isinstance(get_client("ebook"), ReadarrClient)


def test_get_client_bookshelf(monkeypatch):
    _set_config(monkeypatch, {
        "ebook": {"url": "http://x", "api_key": "k", "server_software": "bookshelf"}
    })
    assert isinstance(get_client("ebook"), BookshelfClient)


def test_get_client_lazylibrarian(monkeypatch):
    _set_config(monkeypatch, {
        "audiobook": {"url": "http://x", "api_key": "k", "server_software": "lazylibrarian"}
    })
    assert isinstance(get_client("audiobook"), LazyLibrarianClient)


def test_get_client_none_when_unconfigured(monkeypatch):
    _set_config(monkeypatch, {"ebook": {}})
    assert get_client("ebook") is None


def test_get_client_none_when_partial(monkeypatch):
    _set_config(monkeypatch, {"ebook": {"url": "http://x"}})  # no api_key
    assert get_client("ebook") is None


def test_get_metadata_client_with_token(monkeypatch):
    _set_config(monkeypatch, {"hardcover": {"token": "abc"}})
    assert isinstance(get_metadata_client(), HardcoverClient)


def test_get_metadata_client_without_token(monkeypatch):
    _set_config(monkeypatch, {"hardcover": {}})
    assert get_metadata_client() is None
