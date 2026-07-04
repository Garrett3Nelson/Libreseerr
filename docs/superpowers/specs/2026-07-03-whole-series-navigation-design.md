# Whole-Series Navigation for Series Cards — Design

**Date:** 2026-07-03
**Status:** Approved (design), pending implementation plan
**Builds on:** `2026-07-03-library-aware-recommendations-design.md` (series cards feature)

## Problem

The "Continue the Series" cards let the user page through installments with ‹ ›
arrows, but each card's `entries` list contains only the *upcoming* installments
(positions after the user's furthest-read book). The position label is a
synthetic `idx+1 / entries.length`, so the next book to get reads **"1 / 3"**
even though it is really, say, **book 3 of a 15-book series**. This is
confusing and hides the true position in the series.

## Goal

Let the arrows scroll through the **entire** primary series in both directions —
back to already-read/owned early installments and forward to unreleased ones —
while the card still *opens* on the next book to get, and the label shows the
**true series position**, e.g. "3 / 15".

## Non-Goals

- No change to which series appear in the row. A series still qualifies only if
  it has at least one upcoming installment to get; a fully-read/owned series
  stays hidden.
- No change to the flat rows (`more_by_authors`, `want_to_read`), the matching
  layer (`matching.py`), or `/api/availability`.
- No windowing/pagination beyond a generous whole-series cap. Real primary
  series don't exceed it.

## Behavior

### Series selection (unchanged)

A series is included only when it has ≥1 **upcoming** installment: a position
beyond the furthest-read book, within the primary run, with a valid canonical
edition (released or unreleased). If there are none, the series is skipped —
identical to today. This guarantees the row's *contents* (which series show up)
do not change; only each card's internal navigation is enriched.

### Entries — now the whole primary series

Each group's `entries` becomes **every primary installment**, positions
`1..primary_books_count`, ascending, one canonical edition per position:

- **Earlier / read positions are now included** (previously excluded). Each is
  flagged `"read": true`. For a read position, the canonical edition prefers the
  edition the user actually read (`book id in library.excluded_ids`); otherwise
  the best-ranked edition for that position.
- **Upcoming positions** keep `"read": false` and `"released"` as today
  (unreleased installments are included and flagged `released: false`, not
  dropped).
- The existing content filters (compilation / noise-title drop, canonical
  ranking, fractional-position skip, beyond-primary skip) still apply per
  position. If a position has no valid canonical edition it is simply absent
  from `entries` (positions may be non-contiguous; the label uses the entry's
  own `position`, so gaps are harmless).

### New fields

- **Per entry:** `"read": bool`.
- **Per group:** `"series_total": int` — the label denominator. Uses
  `primary_books_count` when present, else the max entry `position`.

### Cap

Replace the per-card upcoming cap (`PER_CARD_CAP = 12`) with a whole-series cap
`SERIES_ENTRIES_CAP = 60`. The list keeps the **highest** positions, so upcoming
installments (which sort highest) are retained and the earliest read books are
dropped first — the opposite of truncating the tail. Real primary series do not
reach 60, so this is a payload-size guard only.

### Frontend navigation & label

- **Label:** `${entry.position} / ${group.series_total}` → "3 / 15" (was
  `idx+1 / entries.length`).
- **Default landing index** (`nextGapIndex`): first entry that is
  **released AND not read AND not fully-owned-in-library**. Read/owned earlier
  books now sit in the array to the left but are skipped for the default. If no
  such entry exists (only unreleased remain), fall back to the first upcoming
  (unreleased) entry so the card still opens sensibly.
- **Arrows:** traverse the full `entries` array, clamped at both ends
  (disabled on book 1 / book N).

### Badges (`renderSeriesEntry`)

- A `read` entry shows a **"Read ✓"** badge, rendered **in addition to** any
  eBook/Audiobook library badges (a book can be both read and in-library).
- Unreleased entries still show **"Coming ‹year›"**.
- Released entries stay clickable and open the request modal — a read book not
  in the download library can still be requested.

## Data shape

```jsonc
// one group in the continue_series list
{
  "series_id": 1,
  "series_name": "Stormlight",   // cleaned
  "series_total": 15,            // NEW — label denominator
  "entries": [
    { /* ...normalized book... */ "position": 1, "released": true,  "read": true  },
    { /* ... */                   "position": 2, "released": true,  "read": true  },
    { /* ... */                   "position": 3, "released": true,  "read": false },  // next gap
    { /* ... */                   "position": 4, "released": false, "read": false }   // Coming
  ]
}
```

## Components touched

| Unit | Change |
|------|--------|
| `recommendations.select_continue_series` | Include read positions (flagged `read`), add `series_total`, swap `PER_CARD_CAP`→`SERIES_ENTRIES_CAP`, keep series-selection gate on presence of an upcoming installment. |
| `recommendations.py` constants | `PER_CARD_CAP = 12` → `SERIES_ENTRIES_CAP = 60`. |
| `static/js/app.js` `renderSeriesEntry` | Label uses `position / series_total`; add "Read ✓" badge. |
| `static/js/app.js` `nextGapIndex` | Add `&& !entry.read`; fall back to first upcoming when all released are read/owned. |
| `static/js/app.js` `renderSeriesCard` | Carry `series_total` onto the card element for the label. |
| `static/css/style.css` | `.book-badge.read` style. |

## Testing

- **`tests/test_recommendations.py`** (TDD — adjust expectations first): grouped
  `entries` now include read positions with `read: true` in ascending order; each
  group carries `series_total`; the canonical-edition, cleaned-name, recency, and
  filter cases still hold. The cap test targets `SERIES_ENTRIES_CAP`.
- **`tests/test_discover_recommendations.py`**: the grouped contract passes
  through `read` and `series_total` unchanged (no endpoint change expected).
- **Frontend:** verified in-browser per Garrett's workflow — label shows the true
  position (e.g. "3 / 15"), arrows reach book 1 and the unreleased tail and clamp
  at both ends, "Read ✓" badges appear on early installments, and the card still
  defaults to the next book to get.

## Risks / edge cases

- **Read on Hardcover ≠ in download library.** Early installments may show
  "Read ✓" with no eBook/Audiobook badge. Intended — the badge reflects reading
  history; library badges reflect the download backend.
- **Non-contiguous positions.** A missing canonical edition leaves a gap in
  `entries`; the label uses each entry's own `position`, so "3 / 15" stays
  correct even if position 7 is absent.
- **Only-unreleased-remaining series.** Still appears, opens on the unreleased
  entry, and scrolls back through the read installments. Unchanged intent.
