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

## Verify request modal "pre-select missing format" on a two-slot account

**What:** Feature 3 of the library-aware recommendations work (spec
`docs/superpowers/specs/2026-07-03-library-aware-recommendations-design.md`)
specifies that when the request modal opens for a *partially*-owned book (owned
in one format, missing the other), it should pre-select the **missing** format's
target instead of defaulting to ebook. The owned-marking half ("Already in
library" / disabled server button) was visually confirmed during browser
verification; the **auto-select-the-missing-slot** half was NOT visually
confirmed against the live account.

**Where:** `static/js/app.js` `openDownloadModal` (the loop that adds each
configured-but-not-owned slot to `selectedServers`) and `renderServerButtons`
(`.owned` marking). Backend match keys from `/api/availability`.

**Why unverified:** The live account has only the **ebook** slot configured
(audiobook has no URL). With a single configured slot, "fully owned" == owned in
that one slot, so an owned book is hidden and partial ownership never arises —
there is no second slot to auto-select. The auto-select branch is therefore
unreachable on this account.

**Already tried:** Browser-verified everything else live (series cards, clean
names, arrow paging + end clamp, next-gap open, "Coming <year>" unreleased state
and non-requestability, owned "eBook ✓" badge on flat and series cards,
hide-fully-owned flat card, hide-fully-owned series, modal "In library"
owned-marking) — the last four by driving the real frontend functions
(`applyAvailabilityBadges`, `hideFullyOwnedFlatCards`, `initSeriesCards`,
`openDownloadModal`) against the live DOM with synthetic availability, since no
current recommendation intersects the owned library. Could not exercise the
two-slot pre-select path because no audiobook server is configured.

**To finish:** Configure both an ebook and an audiobook server, own a title in
exactly one of them, and confirm the modal opens with the *other* format's
target pre-selected and the owned one marked "In library".

## Note: Bookshelf exposes no ISBN or author for availability matching

Not a bug — recording the observed live behavior. The configured Bookshelf
ebook server's `get_books()` returns titles but no ISBNs and no author, so
`/api/availability` emits title-only match keys (`"<normtitle>|"`) and matching
runs on the normalized-title fallback rather than ISBN or title+author. This is
the graceful-degradation path the spec anticipated (Feature 1 "ISBN risk"). If a
backend that exposes ISBNs/authors is added later, matching precision improves
automatically with no code change.

## Availability counts only downloaded books (fixed); bookFileCount can lag

**Fixed.** `/api/availability` (`check_availability`) used to add every book from
`get_books()` to the owned set. Bookshelf/Readarr `GET /api/v1/book` returns the
whole **catalog**, including metadata stubs the user never downloaded — often
auto-created during an author metadata refresh, with `editions: null`,
`monitored: false` and `statistics.bookFileCount: 0`. Those earned false
eBook/Audiobook badges (reported case: Murderbot 4.5 `Home: Habitat, Range,
Niche, Territory`, which was in the audiobook catalog as a fileless stub). Now a
book is skipped unless `statistics.bookFileCount > 0`. The gate runs before the
editions/flat format dispatch because a stub has `editions: null` and would
otherwise slip through the flat branch. LazyLibrarian books (no `statistics`
dict) are unaffected.

**Known caveat (Bookshelf-side, not ours):** `statistics.bookFileCount` is a
cached value Bookshelf doesn't recompute until an author refresh runs, so a book
with an imported file can still report `bookFileCount: 0` (observed live for
`Fugitive Telemetry` — `/api/v1/bookfile` returned 1 file while `bookFileCount`
was 0, and Bookshelf's own UI showed "Files (0)"). We intentionally mirror
Bookshelf's reported count rather than making a per-book `/bookfile` call on every
availability hit (that would be one HTTP call per catalog entry). Unfiltered
`GET /bookfile` is not an option — it 500s demanding an authorId/bookId. Once the
underlying Bookshelf count bug is fixed, such books report correctly with no
change here. If it needs addressing sooner: batch `/bookfile` by authorId (one
call per owned author) and treat presence there as owned.
