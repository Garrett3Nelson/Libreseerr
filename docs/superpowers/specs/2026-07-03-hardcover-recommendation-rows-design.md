# Design: Hardcover-powered personal recommendation rows

Date: 2026-07-03
Status: Approved (brainstorming complete)

## Goal

Add personalized discovery carousels ("rows") to the Libreseerr discovery view,
driven by the configured Hardcover account's reading history. The headline
feature is a **Continue the Series** row that recommends the next unread entries
in series the user has already started. Two companion rows round out the set.

All three rows appear **only when a Hardcover token is configured** and the
account has relevant history. They are pinned **above** the existing generic
rows (New Releases, Trending, ...).

## Scope — three new rows

1. **Continue the Series** — next unread entries in series the user has partly read.
2. **More From Authors You've Read** — popular books by authors of the user's Read books.
3. **On Your Want-to-Read List** — the account's Hardcover "Want to Read" shelf,
   surfaced so the user can request them directly.

### Out of scope (v2, explicitly deferred)
- A second "Trending" flavor sourced from Open Library alongside the Hardcover
  one. Note: when a Hardcover token is present, `get_metadata_client()` already
  makes Hardcover the source for *all* discovery, so the current Trending row is
  already Hardcover data. A dual-Trending feature would mean adding explicit
  Open-Library-sourced rows. Not in v1.
- Per-user Hardcover tokens. v1 uses the single server-wide token (see below).

## Key decisions

- **Token scope: single global token.** The Hardcover token is one server-wide
  config value (`config["hardcover"]["token"]`) tied to one account. "My reading
  history" means that account's history. Correct for a personal/single-user
  instance; acceptable limitation for shared instances. Do not build per-user
  tokens in v1, but keep the recommendation logic in its own module so a
  per-user token could be threaded in later without a rewrite.
- **Recommend = next-in-series.** For each partly-read series, surface the next
  primary entries *after* the user's furthest-read position — not earlier gaps,
  not spin-offs.
- **Presentation = one combined row per feature** (not one row per series).

## Verified API facts (Hardcover GraphQL)

Endpoint and client already exist in `hardcover.py` (`HardcoverClient`, Bearer
token, `https://api.hardcover.app/v1/graphql`). Constraints: 60 req/min, 30s
timeout, **max query depth 3**. The following were verified live against a real
account during design:

- `me { username user_books { ... } }` returns the account library. `me` comes
  back as a **list** (take `[0]`). `user_books.status_id`: **1 = Want to Read,
  2 = Currently Reading, 3 = Read** (observed distribution: 61 want, 120 read).
- Each `books` row has `cached_featured_series` — a JSON object:
  `{ "id": <book_series_id>, "series": { "id", "name", "slug", "books_count",
  "primary_books_count" }, "details": "<position string e.g. '1'>", ... }`.
  This lets us get a read book's series + position **in one query at depth 3**
  (`me → user_books → book → cached_featured_series`). Do NOT try
  `me → user_books → book → book_series` — that is depth 4 and exceeds the limit.
- Series expansion (depth 3, batchable):
  ```graphql
  query($ids: [Int!]) {
    series(where: {id: {_in: $ids}}) {
      id name books_count primary_books_count
      book_series(order_by: {position: asc}, limit: 40) {
        position
        book { id title release_date cached_image compilation contributions(limit: 2) { author { name } } }
      }
    }
  }
  ```
  Expansion is **noisy**: foreign translations, box sets ("The Stormlight
  Archive, Books 1-4"), "Prime"/alt editions, and fractional positions (e.g.
  0.1) all appear. Filter accordingly (see rules below).
- Authors of the Read books (two-step, depth 3):
  step 1 `books(where: {id: {_in: [...]}}) { contributions(limit: 2) { author { id name } } }`;
  step 2 — books by those authors — use a **nested where on the books table**
  (depth 1), NOT `authors → contributions → book` (that returns null books):
  ```graphql
  query($aids: [Int!]) {
    books(where: {contributions: {author_id: {_in: $aids}}, users_count: {_gte: 50}},
          order_by: {users_count: desc}, limit: 30) {
      id title release_date users_count cached_image compilation
      contributions(limit: 2) { author { name } }
    }
  }
  ```
- Want-to-Read display (depth 3):
  `me { user_books(where: {status_id: {_eq: 1}}, order_by: {date_added: desc}) { book { id title cached_image contributions(limit: 1) { author { name } } } } }`.

## Architecture

Reuse the existing carousel plumbing; do not invent a parallel one.

- **Frontend** (`static/js/app.js`): `DISCOVERY_CATEGORIES` drives a loop that
  fetches `GET /api/discover?category=<key>` per row. Add three new category
  objects — `continue_series` ("Continue the Series"), `more_by_authors`
  ("More From Authors You've Read"), `want_to_read` ("On Your Want-to-Read
  List") — inserted at the **front** of the list, but only when Hardcover is
  active. The frontend learns "Hardcover active" from a boolean it already has
  access to (add a `hardcover_enabled` flag to whatever config/status payload
  the frontend loads on startup; do not expose the token).
- **Backend** (`app.py` + new `recommendations.py`): in the `/api/discover`
  handler, route the three new keys to a `recommendations.py` helper. Keep the
  Hardcover query/normalization details in `recommendations.py` (or as methods
  on `HardcoverClient`) so `app.py` stays a thin router and `hardcover.py` stays
  focused on the existing metadata role. The helper fetches the library **once**
  and reuses it across all three rows within a request.
- Each row returns the **shared normalized book schema** (`_normalize_*`
  shape: id, title, authors, cover, isbns, etc.) so cards, the request modal,
  and the request-both flow work unchanged. ISBNs may be empty — the request
  flow already falls back to title search, matching current Hardcover discover
  rows.

## Data flow (per cache-miss)

1. Fetch library once: `me { user_books }` → Read set (status 3) and
   Want-to-Read set (status 1), each with `book_id`, title, cover, and
   `cached_featured_series`.
2. **Continue the Series**: group Read books by `series.id`; per series compute
   the furthest-read integer `position` (from each read book's
   `cached_featured_series.details`); batch-expand those series in one query;
   keep primary/non-compilation/English entries with `position >` furthest and
   not already Read/Currently-Reading; cap a few entries per series so one
   series can't dominate; order series by the user's most-recent read activity;
   cap the row at ~20 items.
3. **More by Authors**: collect author ids from the Read set (step-1 query),
   then step-2 nested-where query ordered by `users_count desc`, excluding
   already-Read books and compilations; cap ~20.
4. **Want-to-Read**: the status-1 books, most-recently-added first; cap ~20.

Total ≈ 4–5 Hardcover queries per cache-miss (the library fetch is shared).
Reuse the existing `_discover_cache` with the existing 300s TTL, keyed by
`(source, category)`. Because the token is global (one account), no per-user
cache key is needed.

## Filtering rules (from observed data)

- Exclude `compilation == true` books and title patterns that are clearly box
  sets (e.g. contains "Books 1-" / a comma-separated list of the series titles).
- Exclude foreign-language / alternate editions and "Prime"/alt entries; prefer
  entries that have a `cached_image` (cover). Prefer integer positions; drop
  fractional positions (0.1) for "next in series".
- Treat `position <= primary_books_count` as the primary run of the series.
- Never recommend a book already in the Read or Currently-Reading set.
  Want-to-Read entries that are the next-in-series ARE allowed (prime request
  candidates) — do not exclude them.
- Dedupe by book id and by (series id, position) — pick the canonical entry
  (highest `users_count`, or the one with a cover) when positions collide.

## Errors & empty states

- Any Hardcover failure while building a personalized row must be caught, logged,
  and turned into an **empty list** so the discovery page still renders. A
  personalized row failing must never 500 the whole page.
- The frontend must **hide rows that return zero items** (so users without a
  given kind of history don't see empty shelves). Verify current behavior and
  add the hide-on-empty handling if missing.

## Testing (repo convention: fakes, no network)

- Unit-test `recommendations.py` against **canned** `user_books` / series /
  author payloads (mirroring the shapes above). Assert:
  - next-in-series computation picks the correct next entries and excludes
    already-read positions;
  - compilation / fractional-position / non-primary entries are filtered out;
  - dedupe collapses duplicate positions;
  - author row excludes already-read books;
  - want-to-read row returns the status-1 set in recency order.
- Endpoint test: the three category keys return data when a (fake) Hardcover
  client is present and are gated (absent/empty) when it is not.
- `ruff check .` and `pytest` must pass (both gate CI). Also confirm the Docker
  build/boot job still serves `/login` (existing CI job).

## Non-goals / guardrails for the implementer

- Do not add new third-party dependencies.
- Do not touch the request/download flow, auth, or the ebook/audiobook slot
  logic.
- Do not expose the Hardcover token to the frontend.
- Keep `app.py` a thin router; put logic in `recommendations.py`.
- Follow the existing load-on-request / save-after-mutate state pattern if any
  new persisted state is added (none is expected — recommendations are derived,
  cached in-memory only).
