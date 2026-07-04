# Library-Aware Recommendations + Series Cards — Design Spec

**Date:** 2026-07-03
**Status:** Approved (brainstorm complete), ready for implementation planning
**Builds on:** `docs/superpowers/plans/2026-07-03-hardcover-recommendation-rows.md` (Hardcover personal rows — completed)

## Goal

Make the three Hardcover personal discovery rows aware of what is already in the
user's Readarr/Bookshelf/LazyLibrarian libraries, and turn "Continue the Series"
into a navigable per-series card. Concretely:

1. **Reliable library matching** so recommended books that are already downloaded
   (or actively requested) are recognized and badged — today they slip through
   because Hardcover rows carry no ISBN and matching is exact-title-only.
2. **Series cards** for "Continue the Series": one card per partly-read series,
   opening on the next book the user should get, with ‹ › arrows to page through
   the remaining installments.
3. **Owned / format-aware behavior**: keep a partially-owned book visible with a
   badge (so the user can add the missing format), hide it only when fully owned,
   and have the request modal pre-select the missing format.

## Non-Goals

- No change to the request/download submission flow, auth, or the ebook/audiobook
  slot model beyond the modal's default format selection.
- No new third-party dependencies.
- No change to the generic (Open Library) discovery rows' *content*; they may gain
  the same normalized-title match key for consistency, but their selection logic is
  untouched.
- Do not expose the Hardcover token to the frontend.

## Background (current state)

- `recommendations.py` builds three rows (`continue_series`, `more_by_authors`,
  `want_to_read`) as flat lists of normalized book dicts. `continue_series`
  currently returns up to `PER_SERIES_CAP = 3` flat book cards per series and
  **drops** unreleased installments.
- `hardcover.normalize_book_row` returns `isbn_13=""` / `isbn_10=""` — Hardcover
  rows have no ISBN.
- `/api/availability` (`app.py`) already reads every book from the configured
  ebook + audiobook servers and returns `{isbns, titles}` per slot, plus
  `ebook_requests` / `audiobook_requests` for active requests. Titles are stored
  lowercased but otherwise raw.
- Frontend `applyAvailabilityBadges` (`static/js/app.js`) stamps
  `eBook ✓ / Audiobook ✓ / eBook Requested / Audiobook Requested` badges on every
  `.book-card`, matching by ISBN **or exact lowercased full title**. It runs over
  all cards including the personal rows — so the *mechanism* exists; matching is
  the weak link.
- The request modal (`openRequestModal` → `renderServerButtons` → `renderSlotOptions`)
  lets the user toggle ebook/audiobook targets. It pre-selects the first configured
  slot and does **not** reflect what is already owned.

## Feature 1 — Reliable library matching

### Decision
Match order: **ISBN-13 / ISBN-10 exact → normalized `title + first-author` fallback.**
Normalization lives in **Python** (one tested implementation); the frontend does
dumb set-membership against precomputed keys — no normalization logic duplicated
in JS, so the two sides can't drift.

### Components
- **New module `matching.py`** (pure, unit-tested):
  - `normalize_title(s: str) -> str`: lowercase → cut at the first `:` or ` (`
    (drop subtitle / parenthetical qualifier) → strip a leading article
    (`the` / `a` / `an`) → strip punctuation → collapse whitespace. Empty-safe.
  - `match_key(title, author) -> str`: `normalize_title(title) + "|" + normalize_title(author)`
    (author optional; when absent, title-only key). Used so a same-title /
    different-author collision within a library doesn't false-match.
- **`/api/availability`**: build the library index using `normalize_title` /
  `match_key` for titles (keep ISBNs as today, unchanged). Return the normalized
  title keys (replacing / alongside the raw lowercased titles). Also index the
  library book's author where the backend exposes it, to populate the match key.
- **Discover responses**: attach to each personal-row book (and, for consistency,
  each generic discover book) a precomputed `match_title` = `match_key(...)`, and
  populate `isbn_13` / `isbn_10` for Hardcover rows where an ISBN is available
  (see ISBN risk below).
- **Frontend `applyAvailabilityBadges`**: compare book ISBNs first, then
  `book.match_title` against the availability normalized-key sets. Remove the
  in-JS lowercasing/normalization; rely on the backend-supplied keys.

### ISBN risk (explicit)
Fetching per-book edition ISBNs from Hardcover may exceed its GraphQL depth-3 cap.
The implementation must verify this live. If impractical, ship **without** Hardcover
ISBNs and rely on the normalized `title + author` fallback — the feature still
works, only slightly less precise on rare same-title cases. This must degrade
gracefully and never block a row from rendering.

## Feature 2 — Series cards (Continue the Series only)

### Backend — grouped return shape
`select_continue_series(library, data)` returns a **list of series groups** instead
of a flat book list:

```python
[
  {
    "series_id": int,
    "series_name": str,        # cleaned (see clean_series_name)
    "entries": [               # upcoming primary installments, position order
      { **normalized_book, "position": int, "released": bool },
      ...
    ],
  },
  ...
]
```

Rules:
- `entries` = all upcoming **primary** installments after the user's furthest-read
  position, in ascending position order, capped at a per-card max (~12).
- Canonical edition per integer position (existing `_rank`: most readers, then
  has-cover). Drop compilations, foreign/alt editions (`_is_noise`), and fractional
  positions (novellas) as today.
- **Change from current behavior:** unreleased installments are **included** and
  flagged `released: False` (so arrows can reach them), rather than dropped. Each
  entry carries `released` computed from `_is_unreleased`.
- Series ordered by the user's most-recent read activity (`last_date`), unchanged.
- Retire the flat `PER_SERIES_CAP = 3` behavior for this row (replaced by the
  per-card entry cap). Cross-series `ROW_LIMIT` still caps the number of series
  cards.
- **New `clean_series_name(name) -> str`** (pure, tested): strip parenthetical
  qualifiers matching `order|publication|chronolog|omnibus` and a trailing
  `Series`, collapse whitespace. E.g. `"Ender's Game (Publication Order)"` →
  `"Ender's Game"`.

The other two rows (`more_by_authors`, `want_to_read`) keep their **flat** shape.

### Frontend — `renderSeriesCard(group)`
- Header shows the cleaned series name.
- Body shows one entry at a time: cover, title, author, year, plus an `n / total`
  position indicator and **‹ ›** arrows (clamped at first/last entry).
- The card holds all `entries` as data; navigation swaps the visible entry.
- Per-entry owned/requested badges are recomputed as the user navigates (reuse the
  same matching used by `applyAvailabilityBadges`). Unreleased entries show a
  disabled `Coming ‹year›` state and are not requestable.
- **Opens on the "next gap"**: the first entry that is released and not fully owned.
  If every upcoming installment is fully owned, the whole series card is hidden.
- The request action reuses the existing modal with the currently-shown entry's
  book dict.
- `loadDiscovery` special-cases `continue_series` (grouped → series cards) vs. the
  other two rows (flat → book cards). The default-index selection and
  hide-if-fully-owned pass run **after** `/api/availability` resolves, since both
  depend on owned state.

### API note
`GET /api/discover?category=continue_series` now returns the grouped structure.
The `_discover_personalized` caching still applies (cache the grouped payload).
Empty result (no partly-read series) → `[]`, and the row is hidden as today.

## Feature 3 — Owned / format-aware behavior

- **Flat rows** (`more_by_authors`, `want_to_read`): badge a book when it is owned
  in one format; **remove the card only when fully owned** — present in *every
  configured* slot (if only one slot is configured, "fully owned" = present in that
  slot). Handled in the post-availability pass in JS.
- **Request modal**: when opening for a partially-owned book, pre-select the
  **missing** format(s) instead of always defaulting to ebook (own the ebook →
  default the audiobook target). Mark an already-owned, configured server button as
  "Already in library" (disabled or clearly annotated) so the user isn't offered a
  duplicate. The book dict passed into the modal carries enough owned-state context
  (or the modal re-checks cached availability) to decide.

## Data flow summary

```
Hardcover ──build_all──> recommendations
  continue_series: [ {series_id, series_name, entries:[{book, position, released}]} ]
  more_by_authors / want_to_read: [ normalized_book (+match_title, isbns?) ]
        │
        ▼
/api/discover?category=… (cached)  ──► frontend loadDiscovery
        │                                   │ grouped? → renderSeriesCard
        │                                   │ flat?    → renderBookCard
/api/availability (ISBNs + normalized keys) ┘
        └──► applyAvailabilityBadges + series default-entry + hide-fully-owned
```

## Error handling

- `build_all` and each row already degrade to `[]` on failure; unchanged.
- `/api/availability` already try/excepts per server; the normalization additions
  must be empty-safe.
- Hardcover ISBN fetch failures never block a row (fall back to title+author).
- New helpers (`matching.py`, `clean_series_name`) are pure and total (empty-safe).

## Testing

**Unit (pytest, fakes only — no network):**
- `matching.normalize_title`: subtitles after `:`, parenthetical qualifiers,
  leading articles, punctuation, unicode dashes/quotes, empty/None-safe.
- `matching.match_key`: title+author composition, author-absent case.
- `recommendations.select_continue_series` (grouped shape): entry ordering by
  position, unreleased **flagged not dropped**, canonical edition pick, compilation/
  foreign/fractional dropped, per-card cap, series ordering by recency, cleaned
  `series_name` present.
- `recommendations.clean_series_name`: qualifier stripping, trailing "Series",
  no-op on clean names.
- `/api/availability`: returns normalized title keys; still returns ISBNs and the
  request sets; empty/misconfigured server safe.
- `/api/discover?category=continue_series`: returns grouped structure; other two
  categories still flat; `[]` when empty; never 500.

**Browser verification (per Garrett's workflow — visual confirmation required):**
- Series card renders with a clean name; ‹ › arrows page through installments and
  clamp at the ends.
- Card opens on the correct "next gap" entry; a fully-owned series is hidden.
- Owned/requested badges are accurate per entry as you navigate; unreleased entries
  show "Coming ‹year›" and are not requestable.
- A partially-owned book in a flat row stays visible with a badge; a fully-owned one
  disappears.
- The request modal pre-selects the missing format and marks the owned one.

## Definition of Done

- All three features implemented behind the existing `hardcover_enabled` gate.
- `ruff check .` clean and `pytest` green (new tests included; no new tracked
  failures introduced).
- Browser verification above confirmed against the live account, with findings
  captured. Any unresolved issue recorded in `TODO.md` (what / where / tried).
```
