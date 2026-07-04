"""Hardcover API metadata source.

Optional alternative to Open Library for the Discover search box and the
discovery carousels. Enabled when an API token is configured in Settings.

Hardcover exposes a single GraphQL (Hasura) endpoint. Notable constraints
(from https://docs.hardcover.app): 60 requests/minute, 30s timeout, a maximum
query depth of 3, and the token must be kept server-side (we only ever call it
from the Flask backend). Search is backed by Typesense and returned as a raw
JSON blob under `results`.
"""

import datetime
import logging

import requests

import matching

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.hardcover.app/v1/graphql"

# Free-text book search. Hardcover's defaults already sort by text match then
# users_count, which is the same ranking the website uses.
_SEARCH_QUERY = """
query Search($q: String!) {
  search(query: $q, query_type: "book", per_page: 20) { results }
}
"""

# Genre rows: there is no clean depth-3 genre filter on the books table, so we
# lean on the search index with the genre as the query, ranked by popularity.
_GENRE_QUERY = """
query Genre($q: String!) {
  search(query: $q, query_type: "book", per_page: 20, sort: "users_count:desc") { results }
}
"""

# Genre carousel key -> search term.
_GENRES = {
    "fiction": "fiction",
    "science_fiction": "science fiction",
    "mystery": "mystery thriller",
    "fantasy": "fantasy",
    "romance": "romance",
    "nonfiction": "nonfiction",
    "history": "history",
    "classics": "classics",
}

# Ordered (non-genre) carousels -> the books-table order_by clause. These run
# against the Hasura `books` table directly so we can sort by real signals.
_ORDERED = {
    "trending": "{users_count: desc_nulls_last}",
    "best_sellers": "{ratings_count: desc_nulls_last}",
    "new_releases": "{release_date: desc_nulls_last}",
}


def get_hardcover_defaults():
    return {"enabled": False, "token": ""}


def cover_url(value) -> str:
    """cached_image / image can be a {url:...} object or a bare string."""
    if isinstance(value, dict):
        return value.get("url", "") or ""
    if isinstance(value, str):
        return value
    return ""


def normalize_book_row(book: dict) -> dict:
    """Normalize a Hasura `books`-table row to the shared frontend book schema.

    Module-level so recommendations.py (and tests) can reuse it without a client
    instance. The `HardcoverClient` method delegates here.
    """
    authors = [
        c["author"]["name"]
        for c in (book.get("contributions") or [])
        if c.get("author") and c["author"].get("name")
    ]
    return {
        "id": str(book.get("id", "")),
        "title": book.get("title", "Unknown"),
        "authors": authors,
        "publishedDate": (book.get("release_date") or "")[:4],
        "description": "",
        "pageCount": 0,
        "categories": [],
        "isbn_13": "",
        "isbn_10": "",
        "cover": cover_url(book.get("cached_image")),
        "language": "en",
        "match_title": matching.match_key(
            book.get("title", ""), authors[0] if authors else ""),
    }


def _normalize_token(token: str) -> str:
    """Hardcover's account page shows the token already prefixed with 'Bearer '.
    Accept it with or without the prefix; add it for a bare JWT."""
    t = (token or "").strip()
    if t and not t.lower().startswith("bearer ") and t.startswith("ey"):
        t = "Bearer " + t
    return t


class HardcoverClient:
    """Client for the Hardcover GraphQL API."""

    def __init__(self, token: str):
        self.token = _normalize_token(token)
        self.session = requests.Session()
        self.session.headers.update({
            "authorization": self.token,
            "content-type": "application/json",
            "user-agent": "Libreseerr (book request manager)",
        })

    def _post(self, query: str, variables: dict | None = None) -> dict:
        resp = self.session.post(
            ENDPOINT, json={"query": query, "variables": variables or {}}, timeout=30
        )
        if resp.status_code == 401:
            raise ValueError("Invalid or expired Hardcover API token")
        if resp.status_code == 429:
            raise ValueError("Hardcover rate limit exceeded (60 requests/minute)")
        resp.raise_for_status()
        data = resp.json()
        if data.get("errors"):
            msg = "; ".join(e.get("message", "") for e in data["errors"])
            raise ValueError(f"Hardcover API error: {msg}")
        return data.get("data") or {}

    def test_connection(self) -> dict:
        """Canonical token test from the docs — returns the authed user."""
        data = self._post("query { me { username } }")
        me = data.get("me")
        # `me` may come back as a list or an object depending on the schema.
        if isinstance(me, list):
            me = me[0] if me else {}
        username = (me or {}).get("username", "")
        return {"username": username}

    # ─── Search ───

    def search_books(self, query: str) -> list:
        data = self._post(_SEARCH_QUERY, {"q": query})
        return self._docs_from_search(data)

    # ─── Discover ───

    def discover(self, category: str) -> list:
        if category in _GENRES:
            data = self._post(_GENRE_QUERY, {"q": _GENRES[category]})
            return self._docs_from_search(data)
        if category in _ORDERED:
            return self._discover_ordered(category)
        raise ValueError(f"Unsupported Hardcover category: {category}")

    def _discover_ordered(self, category: str) -> list:
        order_by = _ORDERED[category]
        # new_releases must exclude unreleased future-dated rows.
        where = ""
        if category == "new_releases":
            today = datetime.date.today().isoformat()
            where = f'where: {{release_date: {{_lte: "{today}"}}, users_count: {{_gte: 30}}}},'
        query = f"""
query Discover {{
  books({where} order_by: {order_by}, limit: 20) {{
    id
    title
    release_date
    cached_image
    contributions(limit: 3) {{ author {{ name }} }}
  }}
}}
"""
        data = self._post(query)
        return [self._normalize_book_row(b) for b in (data.get("books") or [])]

    # ─── Normalization (-> the shared book schema the frontend consumes) ───

    def _docs_from_search(self, data: dict) -> list:
        results = (data.get("search") or {}).get("results") or {}
        hits = results.get("hits") or []
        return [self._normalize_search_doc(h.get("document") or {}) for h in hits]

    @staticmethod
    def _cover_url(value) -> str:
        return cover_url(value)

    def _normalize_search_doc(self, doc: dict) -> dict:
        isbns = doc.get("isbns") or []
        isbn_13 = next((i for i in isbns if len(str(i)) == 13), "")
        isbn_10 = next((i for i in isbns if len(str(i)) == 10), "")
        cover = self._cover_url(doc.get("image")) or self._cover_url(doc.get("cached_image"))
        return {
            "id": str(doc.get("id", "") or doc.get("slug", "")),
            "title": doc.get("title", "Unknown"),
            "authors": doc.get("author_names") or [],
            "publishedDate": str(doc.get("release_year") or ""),
            "description": doc.get("description", "") or "",
            "pageCount": doc.get("pages", 0) or 0,
            "categories": (doc.get("genres") or [])[:5],
            "isbn_13": isbn_13,
            "isbn_10": isbn_10,
            "cover": cover,
            "language": "en",
        }

    def _normalize_book_row(self, book: dict) -> dict:
        return normalize_book_row(book)
