import json
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class BookshelfClient:
    """Client for interacting with a Bookshelf instance."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({"X-Api-Key": api_key})

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v1{path}"

    def test_connection(self) -> dict:
        """Test connection to the Bookshelf instance."""
        resp = self.session.get(self._url("/system/status"), timeout=10)
        resp.raise_for_status()
        return resp.json()

    def search_books(self, query: str) -> list:
        """Search for books using the Bookshelf lookup endpoint."""
        resp = self.session.get(
            self._url("/book/lookup"), params={"term": query}, timeout=15
        )
        resp.raise_for_status()
        return resp.json()

    def lookup_by_isbn(self, isbn: str) -> list:
        """Look up a book in Bookshelf by ISBN."""
        resp = self.session.get(
            self._url("/book/lookup"), params={"term": f"isbn:{isbn}"}, timeout=15
        )
        resp.raise_for_status()
        return resp.json()

    def lookup_author(self, name: str) -> list:
        """Look up an author in Bookshelf by name."""
        resp = self.session.get(
            self._url("/author/lookup"), params={"term": name}, timeout=15
        )
        resp.raise_for_status()
        return resp.json()

    def get_quality_profiles(self) -> list:
        """Get available quality profiles."""
        resp = self.session.get(self._url("/qualityprofile"), timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_metadata_profiles(self) -> list:
        """Get available metadata profiles."""
        resp = self.session.get(self._url("/metadataprofile"), timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_root_folders(self) -> list:
        """Get configured root folders."""
        resp = self.session.get(self._url("/rootfolder"), timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _get_metadata_profile_id(self) -> int:
        """Get the first available metadata profile ID."""
        profiles = self.get_metadata_profiles()
        if not profiles:
            raise ValueError("No metadata profiles configured in Bookshelf")
        return profiles[0].get("id")

    def _ensure_author(self, author_data: dict, quality_profile_id: int, root_folder: str) -> dict:
        """Ensure the author exists in Bookshelf. Returns the author record."""
        author_name = author_data.get("authorName", "")
        foreign_author_id = author_data.get("foreignAuthorId", "")

        if not author_name or author_name == "Unknown":
            raise ValueError(
                f"Cannot add author: no valid author name provided. "
                f"Got: {json.dumps(author_data)}"
            )

        logger.info(
            "Ensuring author: name='%s', foreignAuthorId='%s'", author_name, foreign_author_id
        )

        # Check existing authors in Bookshelf
        existing = self.session.get(self._url("/author"), timeout=15).json()

        # Match by foreignAuthorId first (most reliable)
        if foreign_author_id:
            match = next(
                (a for a in existing if a.get("foreignAuthorId") == foreign_author_id),
                None,
            )
            if match:
                logger.info("Author already exists (matched by ID): %s", match.get("authorName"))
                return match

        # Match by name
        match = next(
            (a for a in existing if a.get("authorName", "").lower() == author_name.lower()),
            None,
        )
        if match:
            logger.info("Author already exists (matched by name): %s", match.get("authorName"))
            return match

        # Author not in Bookshelf — need to add it
        # If we don't have a valid foreignAuthorId, look up the author by name
        # to get the correct metadata provider ID first.
        if not foreign_author_id:
            logger.info("No foreignAuthorId, looking up author by name: '%s'", author_name)
            lookup = self.session.get(
                self._url("/author/lookup"),
                params={"term": author_name},
                timeout=15,
            )
            if lookup.ok and lookup.json():
                all_results = lookup.json()
                for i, r in enumerate(all_results[:5]):
                    logger.info(
                        "  lookup[%d]: '%s' (foreignAuthorId='%s')",
                        i, r.get("authorName", ""), r.get("foreignAuthorId", ""),
                    )
                # Prefer exact name match
                exact = [
                    a for a in all_results
                    if a.get("authorName", "").lower() == author_name.lower()
                ]
                if exact:
                    author_data = exact[0]
                    foreign_author_id = author_data.get("foreignAuthorId", "")
                    logger.info("Using exact lookup match: foreignAuthorId='%s'", foreign_author_id)
                else:
                    raise ValueError(
                        f"Could not find author '{author_name}' in Bookshelf metadata"
                    )
            else:
                raise ValueError(
                    f"Could not find author '{author_name}' in Bookshelf metadata"
                )

        metadata_profile_id = self._get_metadata_profile_id()
        author_payload = {
            "authorName": author_data.get("authorName", author_name),
            "foreignAuthorId": foreign_author_id,
            "qualityProfileId": quality_profile_id,
            "metadataProfileId": metadata_profile_id,
            "rootFolderPath": root_folder,
            "monitored": True,
            "monitorNewItems": "none",
            "addOptions": {
                "monitor": "none",
                "searchForMissingBooks": False,
            },
        }
        for key in ("images", "overview", "links", "genres", "ratings"):
            if author_data.get(key):
                author_payload[key] = author_data[key]

        resp = self.session.post(
            self._url("/author"), json=author_payload, timeout=30
        )

        if resp.ok:
            return resp.json()

        # Still failing — check if author was added by another process
        updated = self.session.get(self._url("/author"), timeout=15).json()
        match = next(
            (a for a in updated if a.get("foreignAuthorId") == foreign_author_id),
            None,
        )
        if match:
            return match
        match = next(
            (a for a in updated if a.get("authorName", "").lower() == author_name.lower()),
            None,
        )
        if match:
            return match

        resp.raise_for_status()

    def _ensure_author_monitored(self, author: dict) -> None:
        """Ensure the author is monitored so RSS auto-grabs work for future books.

        Only toggles the author-level flag via /author/editor — it does NOT
        change monitoring of the author's individual books (verified: no
        cascade). Author records auto-created during a metadata refresh can come
        back unmonitored, which would block RSS sync for the requested book.
        """
        author_id = author.get("id")
        if not author_id or author.get("monitored"):
            return
        resp = self.session.put(
            self._url("/author/editor"),
            json={"authorIds": [author_id], "monitored": True},
            timeout=30,
        )
        if resp.ok:
            logger.info("Set monitored=True for author id=%s", author_id)
            author["monitored"] = True
        else:
            logger.warning(
                "Failed to set monitored for author id=%s (%d): %s",
                author_id, resp.status_code, resp.text[:200],
            )

    def _monitor_and_search(self, book: dict) -> dict:
        """Ensure an existing book is monitored and trigger a search for it.

        When Bookshelf auto-creates unmonitored edition records during the
        author metadata refresh, the book POST returns the existing (409) record
        without monitoring or searching it. This brings such a book up to the
        same state as a freshly-added one: monitored=True plus a BookSearch.
        """
        book_id = book.get("id")
        if not book_id:
            return book

        # Fetch a fresh copy so we patch against current state.
        resp = self.session.get(self._url(f"/book/{book_id}"), timeout=15)
        if resp.ok:
            book = resp.json()

        if not book.get("monitored"):
            # Use the dedicated bulk-monitor endpoint instead of PUT /book/{id}
            # with the full resource. Books auto-created by the author metadata
            # refresh come back with editions=null, and the full-resource PUT
            # then throws ArgumentNullException('source') server-side. The
            # /book/monitor endpoint only needs the id + flag.
            mon_resp = self.session.put(
                self._url("/book/monitor"),
                json={"bookIds": [book_id], "monitored": True},
                timeout=30,
            )
            if mon_resp.ok:
                logger.info("Set monitored=True for existing book id=%s", book_id)
                book["monitored"] = True
            else:
                logger.warning(
                    "Failed to set monitored for book id=%s (%d): %s",
                    book_id, mon_resp.status_code, mon_resp.text[:200],
                )

        search_resp = self.session.post(
            self._url("/command"),
            json={"name": "BookSearch", "bookIds": [book_id]},
            timeout=15,
        )
        if search_resp.ok:
            logger.info("Triggered BookSearch for existing book id=%s", book_id)
        else:
            logger.warning(
                "BookSearch command failed for book id=%s (%d): %s",
                book_id, search_resp.status_code, search_resp.text[:200],
            )

        return book

    def add_book(self, book_data: dict, quality_profile_id: int, root_folder: str) -> dict:
        """Add a book to Bookshelf for downloading."""
        added_author = self._ensure_author(
            book_data.get("author", {}),
            quality_profile_id,
            root_folder,
        )
        logger.info("Author for book '%s': %s (id=%s)", book_data.get("title"), added_author.get("authorName"), added_author.get("id"))
        self._ensure_author_monitored(added_author)

        foreign_book_id = book_data.get("foreignBookId", "")
        foreign_edition_id = book_data.get("foreignEditionId", "")
        title = book_data.get("title", "Unknown")

        # Check if the book already exists in Bookshelf
        if foreign_book_id:
            existing_books = self.session.get(self._url("/book"), timeout=15).json()
            match = next(
                (b for b in existing_books if b.get("foreignBookId") == foreign_book_id),
                None,
            )
            if match:
                logger.info("Book already exists: '%s' (id=%s)", match.get("title"), match.get("id"))
                return self._monitor_and_search(match)

        # Build the edition payload.
        edition = {
            "foreignEditionId": foreign_edition_id,
            "title": title,
            "monitored": True,
        }
        # Copy edition-level fields from the lookup if present
        for key in ("images", "links", "ratings", "disambiguation",
                    "remoteCover", "grabbed", "titleSlug"):
            if key in book_data:
                edition[key] = book_data[key]

        book_payload = {
            "foreignBookId": foreign_book_id,
            "foreignEditionId": foreign_edition_id,
            "title": title,
            "authorId": added_author.get("id"),
            "qualityProfileId": quality_profile_id,
            "rootFolderPath": root_folder,
            "monitored": True,
            "anyEditionOk": True,
            "editions": [edition],
            "author": added_author,
            "addOptions": {
                "addType": "manual",
                "searchForMissingBooks": False,
            },
        }

        logger.info("Adding book: %s", json.dumps(book_payload))

        resp = self.session.post(
            self._url("/book"), json=book_payload, timeout=30
        )

        if not resp.ok:
            # The book may already exist (orphaned from a prior partial add).
            # Re-check and return the existing book.
            existing_books = self.session.get(self._url("/book"), timeout=15).json()
            match = next(
                (b for b in existing_books if b.get("foreignBookId") == foreign_book_id),
                None,
            )
            if match:
                logger.info("Book already exists (after POST error): '%s' (id=%s)", match.get("title"), match.get("id"))
                return self._monitor_and_search(match)

            logger.error("POST /book failed (%d): %s", resp.status_code, resp.text[:500])

        resp.raise_for_status()
        result = resp.json()
        book_id = result.get("id")

        # Trigger a search for just this book via the command API
        if book_id:
            search_resp = self.session.post(
                self._url("/command"),
                json={"name": "BookSearch", "bookIds": [book_id]},
                timeout=15,
            )
            if search_resp.ok:
                logger.info("Triggered BookSearch for book id=%d", book_id)
            else:
                logger.warning(
                    "BookSearch command failed (%d): %s",
                    search_resp.status_code, search_resp.text[:200],
                )

        return result

    def get_queue(self) -> list:
        """Get current download queue."""
        resp = self.session.get(self._url("/queue"), params={"pageSize": 200}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("records", data) if isinstance(data, dict) else data

    def get_book_status(self, book_id: int) -> Optional[dict]:
        """Get the status of a specific book."""
        resp = self.session.get(self._url(f"/book/{book_id}"), timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def get_books(self) -> list:
        """Get all books from the Bookshelf library."""
        resp = self.session.get(self._url("/book"), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get_history(self) -> list:
        """Get download history."""
        resp = self.session.get(
            self._url("/history"), params={"pageSize": 50}, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("records", data) if isinstance(data, dict) else data
