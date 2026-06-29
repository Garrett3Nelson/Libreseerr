# TODO

## Bindery backend support (upstream issue #12)

**What:** Users can connect Libreseerr to a Bindery server (Service Type:
Readarr) and pass the connection test, but every book request fails with
`HTTP 400 {"error":"isbn or asin parameter required"}`.

**Why it happens:** Bindery (`vavallee/bindery`) is *not* a Readarr API clone.
It shares the `/api/v1` prefix and `X-Api-Key` header but deliberately splits
lookup into different endpoints:

| Purpose         | Bindery                          | Readarr (what Libreseerr sends)        |
|-----------------|----------------------------------|----------------------------------------|
| Free-text search| `GET /api/v1/search/book?q=…`    | `GET /api/v1/book/lookup?term=…`       |
| ISBN/ASIN lookup| `GET /api/v1/book/lookup?isbn=…` | `GET /api/v1/book/lookup?term=isbn:…`  |
| Author search   | `GET /api/v1/search/author?q=…`  | `GET /api/v1/author/lookup?term=…`     |

Bindery only advertises arr-compatibility for `/api/queue`; the rest is its own
contract. So `ReadarrClient.search_books` and `.lookup_by_isbn` both 400.

**Decision:** This is a *new backend*, not a Readarr bugfix. Add a dedicated
`BinderyClient` (like `bookshelf.py` / `lazylibrarian.py`) and a `server_software`
option, rather than special-casing `ReadarrClient`.

**Before building:**
- Verify the write path against a live Bindery instance. Only `/api/queue` is
  confirmed arr-shaped; the `POST /book` + author-ensure + `command BookSearch`
  flow in `readarr.py:196-291` is unverified and may differ.
- Confirm `get_queue` / `get_book_status` / `get_history` shapes on Bindery.

**Already tried:** None — scoped only. Worth doing because Readarr was archived
June 2025 (dead metadata backend) and Bindery is the main migration target.

Refs: https://github.com/vavallee/bindery , docs/API.md in that repo.
