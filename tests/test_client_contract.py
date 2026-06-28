"""Tier 3: the three backend clients honor the same duck-typed interface.

CLAUDE.md calls this shared method set the core abstraction. If someone adds or
renames a method on one client without the others, this fails loudly.
"""
import inspect

from bookshelf import BookshelfClient
from lazylibrarian import LazyLibrarianClient
from readarr import ReadarrClient

BACKEND_CLIENTS = [ReadarrClient, BookshelfClient, LazyLibrarianClient]

REQUIRED_METHODS = {
    "test_connection",
    "search_books",
    "lookup_by_isbn",
    "get_quality_profiles",
    "get_root_folders",
    "add_book",
    "get_queue",
    "get_book_status",
    "get_books",
    "get_history",
}


def _public_methods(cls):
    return {
        name for name, _ in inspect.getmembers(cls, predicate=inspect.isfunction)
        if not name.startswith("_")
    }


def test_all_backends_implement_required_methods():
    for cls in BACKEND_CLIENTS:
        missing = REQUIRED_METHODS - _public_methods(cls)
        assert not missing, f"{cls.__name__} is missing: {sorted(missing)}"


def test_readarr_and_bookshelf_share_surface():
    # These two are documented as the same Readarr-compatible REST shape, so
    # their public surfaces should stay in lockstep. LazyLibrarian is a
    # deliberately different API and is only held to REQUIRED_METHODS above.
    readarr = _public_methods(ReadarrClient)
    bookshelf = _public_methods(BookshelfClient)
    assert readarr == bookshelf, (
        f"only-in-Readarr={sorted(readarr - bookshelf)}, "
        f"only-in-Bookshelf={sorted(bookshelf - readarr)}"
    )
