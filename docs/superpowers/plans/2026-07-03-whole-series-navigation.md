# Whole-Series Navigation for Series Cards — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the "Continue the Series" arrow cards scroll the entire primary series in both directions (back to already-read/owned books, forward to unreleased ones), still opening on the next book to get, with a label showing the true series position (e.g. "3 / 15").

**Architecture:** `recommendations.select_continue_series` stops emitting *upcoming-only* entries and instead emits every primary installment (positions `1..primary_books_count`), each flagged `read`, plus a per-group `series_total`. Series *selection* is unchanged — a series still qualifies only if it has ≥1 upcoming installment to get. The frontend (`static/js/app.js`) uses `entry.position / series_total` for the label, skips read entries when picking the default index, and adds a "Read ✓" badge. No change to `matching.py`, `/api/availability`, or the flat rows.

**Tech Stack:** Python 3.12, Flask, pytest (fakes only, no network). Frontend is plain vanilla JS served from `static/` — no build step, no JS test harness, so frontend tasks are verified in-browser (per Garrett's workflow), not by unit tests.

## Global Constraints

- No new third-party dependencies (Python or JS).
- Keep `app.py` a thin router; recommendation logic stays in `recommendations.py`.
- Do not expose the Hardcover token to the frontend.
- Preserve safe degradation: the `continue_series` row must never 500; it degrades to `[]` on failure.
- All new helpers are pure, total, and empty/None-safe.
- Python: ruff targets py312, line-length 100, rules `E4/E7/E9/F/I/B/UP`. Run `ruff check .` and `pytest` before every commit; both gate CI.
- Tests use canned payloads and fakes; never real network or backends.

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `recommendations.py` | Build the grouped `continue_series` payload | Rewrite `select_continue_series` to include read positions + `series_total`; rename cap constant |
| `tests/test_recommendations.py` | Unit-test the grouped shape | Update fixtures/expectations for read entries + `series_total`; retarget cap test |
| `tests/test_discover_recommendations.py` | Lock the discover contract | Add `read` / `series_total` passthrough assertion |
| `static/js/app.js` | Render + navigate series cards | Label uses `position / series_total`; default index skips read; "Read ✓" badge; carry `series_total` on the card |
| `static/css/style.css` | Series-card styling | Add `.book-badge.read` |

---

### Task 1: Backend — whole-series `entries` with `read` flag and `series_total`

**Files:**
- Modify: `recommendations.py:27-28` (constants), `recommendations.py:158-224` (`select_continue_series`)
- Test: `tests/test_recommendations.py` (update fixtures + `continue_series` cases + `build_all`)

**Interfaces:**
- Consumes (existing, unchanged): `library.series_progress` (`{sid: {furthest, last_date, name}}`), `library.excluded_ids` (read + reading book ids), `clean_series_name`, `_rank`, `_is_noise`, `_is_unreleased`, `_parse_int_position`, `hardcover.normalize_book_row`.
- Produces: `select_continue_series(library, data) -> list[dict]` where each group is:
  ```python
  {"series_id": int, "series_name": str, "series_total": int,
   "entries": [ { **normalized_book, "position": int, "released": bool, "read": bool }, ... ]}
  ```
  `entries` = every primary installment (positions `1..primary_books_count`) with a valid canonical edition, ascending; read positions (`pos <= furthest` or a position whose any edition id is in `excluded_ids`) are **included** and flagged `read: True`, preferring the actually-read edition; non-read positions keep the existing compilation/noise/excluded filters and `read: False`. A series is emitted only if it has ≥1 non-read (upcoming) surviving position. `series_total = primary_books_count` (or max entry position). Capped at `SERIES_ENTRIES_CAP`, groups ordered by recency, capped at `ROW_LIMIT`.

- [ ] **Step 1: Update the shared `_entries` helper and fixtures in the test file**

In `tests/test_recommendations.py`, replace the `_entries` helper (line 99-100) so it also surfaces the new `read` flag:

```python
def _entries(group):
    return [(e["id"], e["position"], e["released"], e["read"]) for e in group["entries"]]
```

- [ ] **Step 2: Rewrite the grouped-shape / filter / cap / read tests (failing)**

In `tests/test_recommendations.py`, **replace** these functions with the versions below:
`test_continue_series_grouped_shape_and_positions`, `test_continue_series_unreleased_flagged_not_dropped`, `test_continue_series_excludes_read_position_across_editions`, `test_continue_series_caps_entries_per_card`, and `test_build_all_returns_three_rows`. Leave the other `continue_series` tests (`filters_noise_fractional_and_beyond_primary`, `canonical_edition_pick`, `drops_position_when_canonical_is_compilation`, `orders_series_by_recency`, `cleans_series_name`) unchanged — they still hold.

```python
def test_continue_series_grouped_shape_and_positions():
    lib = rec.parse_library(_LIB)
    out = rec.select_continue_series(lib, _EXPANSION)
    assert len(out) == 1
    group = out[0]
    assert group["series_id"] == 1
    assert group["series_name"] == "Stormlight"
    assert group["series_total"] == 5  # primary_books_count
    # Whole primary run 1..5: read 1-2 (canonical read edition), upcoming 3-5.
    assert _entries(group) == [
        ("100", 1, True, True), ("101", 2, True, True),
        ("102", 3, True, False), ("103", 4, True, False), ("104", 5, True, False)]


def test_continue_series_unreleased_flagged_not_dropped():
    lib = rec.parse_library({"me": [{"user_books": [
        {"status_id": 3, "date_added": "2026-01-01", "book": {
            "id": 1, "title": "One",
            "cached_featured_series": {"series": {"id": 7, "name": "S"}, "details": "1"}}},
    ]}]})
    data = {"series": [{"id": 7, "name": "S", "primary_books_count": 5, "book_series": [
        {"position": 2, "book": {"id": 30, "title": "Future Two", "users_count": 100,
                                 "release_date": "2999-01-01", "cached_image": {"url": "c"}}},
        {"position": 3, "book": {"id": 31, "title": "Real Three", "users_count": 50,
                                 "release_date": "2020-01-01", "cached_image": {"url": "c"}}},
    ]}]}
    out = rec.select_continue_series(lib, data)
    # No book_series row for position 1, so entries start at 2. Unreleased INCLUDED,
    # flagged released=False; neither is a read position.
    assert _entries(out[0]) == [("30", 2, False, False), ("31", 3, True, False)]


def test_continue_series_includes_read_position_with_read_flag():
    lib = rec.parse_library({"me": [{"user_books": [
        {"status_id": 3, "date_added": "2026-01-01", "book": {
            "id": 50, "title": "Book Three (English)",
            "cached_featured_series": {"series": {"id": 9, "name": "S"}, "details": "3"}}},
    ]}]})
    data = {"series": [{"id": 9, "name": "S", "primary_books_count": 5, "book_series": [
        _bs(2, 60, "Book Two"),
        _bs(3, 50, "Book Three (English)"),   # already read -> canonical read edition
        _bs(3, 51, "Buch Drei", users=0),     # foreign ed. of read book -> not canonical
        _bs(4, 70, "Book Four"),
    ]}]}
    out = rec.select_continue_series(lib, data)
    ids = [e["id"] for e in out[0]["entries"]]
    assert "51" not in ids                      # foreign edition of read book dropped
    assert ids == ["60", "50", "70"]            # read installment kept as context
    by_id = {e["id"]: e for e in out[0]["entries"]}
    assert by_id["50"]["read"] is True          # position 3 flagged read
    assert by_id["70"]["read"] is False         # position 4 still upcoming


def test_continue_series_caps_entries_per_card():
    lib = rec.parse_library({"me": [{"user_books": [
        {"status_id": 3, "date_added": "2026-01-01", "book": {
            "id": 1, "title": "One",
            "cached_featured_series": {"series": {"id": 7, "name": "S"}, "details": "1"}}},
    ]}]})
    bs = [_bs(p, 100 + p, f"Book {p}") for p in range(2, 80)]  # 78 upcoming positions
    data = {"series": [{"id": 7, "name": "S", "primary_books_count": 100, "book_series": bs}]}
    out = rec.select_continue_series(lib, data)
    assert len(out[0]["entries"]) == rec.SERIES_ENTRIES_CAP


def test_build_all_returns_three_rows():
    out = rec.build_all(_FakeHC())
    assert set(out) == set(rec.PERSONALIZED_CATEGORIES)
    groups = out["continue_series"]
    assert [g["series_id"] for g in groups] == [1]
    # Whole primary run now included: read 100,101 then upcoming 102,103,104.
    assert [e["id"] for e in groups[0]["entries"]] == ["100", "101", "102", "103", "104"]
    assert groups[0]["series_total"] == 5
    assert [b["id"] for b in out["want_to_read"]] == ["301", "300"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_recommendations.py -k continue_series -v`
Expected: FAIL — `series_total` / `read` keys missing, `SERIES_ENTRIES_CAP` undefined, entries lists don't include read positions.

- [ ] **Step 4: Rename the cap constant**

In `recommendations.py`, replace the constant at line 28:

```python
PER_CARD_CAP = 12
```

with:

```python
SERIES_ENTRIES_CAP = 60   # whole-series card guard; real primary series don't reach this
```

- [ ] **Step 5: Rewrite `select_continue_series`**

Replace `select_continue_series` (lines 158-224) with:

```python
def select_continue_series(library: Library, data: dict) -> list:
    """Grouped per partly-read series, ordered by most-recent read activity.
    Each group carries the WHOLE primary run so the frontend arrows can scroll the
    entire series::

        {"series_id": int, "series_name": str,  # cleaned
         "series_total": int,                    # label denominator
         "entries": [ { **normalized_book, "position": int,
                        "released": bool, "read": bool }, ... ]}

    Entries are every primary installment (positions ``1..primary_books_count``)
    with a valid canonical edition, ascending. Read positions (at/below the
    furthest-read book, or any position whose edition the user has read) are
    INCLUDED and flagged ``read: True``, preferring the actually-read edition —
    these are the left-hand context the arrows scroll back through. Upcoming
    (not-read) positions keep the content filters (canonical edition, drop
    compilation/noise/already-read) and are flagged ``read: False``; unreleased
    ones are included and flagged ``released: False``. A series is emitted only if
    it has at least one upcoming installment to get, so which series appear is
    unchanged. Capped at ``SERIES_ENTRIES_CAP`` entries; series ordered by recency
    and capped at ``ROW_LIMIT``.
    """
    today = datetime.date.today().isoformat()
    groups_out = []  # (last_date, group dict)
    for s in data.get("series") or []:
        prog = library.series_progress.get(s.get("id"))
        if not prog:
            continue
        furthest = prog["furthest"]
        primary_count = s.get("primary_books_count") or 0
        # Positions where the user read *any* edition — treated as read even if the
        # specific edition below isn't the one they logged.
        read_positions = {
            _parse_int_position(bs.get("position"))
            for bs in s.get("book_series") or []
            if (bs.get("book") or {}).get("id") in library.excluded_ids
        }
        read_positions.discard(None)
        # Group all editions by integer position across the whole primary run.
        editions_by_pos = {}
        for bs in s.get("book_series") or []:
            pos = _parse_int_position(bs.get("position"))
            if pos is None:
                continue
            if primary_count and pos > primary_count:
                continue
            editions_by_pos.setdefault(pos, []).append(bs.get("book") or {})
        # Pick a canonical edition per position; (book, is_read) survives filtering.
        canonical_by_pos = {}
        for pos, editions in editions_by_pos.items():
            is_read = pos <= furthest or pos in read_positions
            if is_read:
                # Prefer the edition the user actually read; else best-ranked.
                read_ed = next(
                    (b for b in editions if b.get("id") in library.excluded_ids), None)
                canonical_by_pos[pos] = (read_ed or max(editions, key=_rank), True)
                continue
            canonical = max(editions, key=_rank)
            if canonical.get("compilation"):
                continue
            if canonical.get("id") in library.excluded_ids:
                continue
            if _is_noise(canonical.get("title", "")):
                continue
            canonical_by_pos[pos] = (canonical, False)
        # Gate: keep the series only if there's an upcoming installment to get.
        if not any(not is_read for _, is_read in canonical_by_pos.values()):
            continue
        positions = sorted(canonical_by_pos)[:SERIES_ENTRIES_CAP]
        entries = []
        for pos in positions:
            book, is_read = canonical_by_pos[pos]
            entry = hardcover.normalize_book_row(book)
            entry["position"] = pos
            entry["released"] = not _is_unreleased(book, today)
            entry["read"] = is_read
            entries.append(entry)
        series_total = primary_count or (positions[-1] if positions else 0)
        groups_out.append((prog["last_date"], {
            "series_id": s.get("id"),
            "series_name": clean_series_name(s.get("name") or prog.get("name") or ""),
            "series_total": series_total,
            "entries": entries,
        }))
    groups_out.sort(key=lambda g: g[0], reverse=True)
    return [g for _, g in groups_out[:ROW_LIMIT]]
```

- [ ] **Step 6: Run the recommendations suite to verify it passes**

Run: `pytest tests/test_recommendations.py -v`
Expected: PASS — updated `continue_series` cases (grouped shape with read entries, `series_total`, read-flag, cap), plus untouched `more_by_authors` / `want_to_read` / `parse_library` / `build_all` cases.

- [ ] **Step 7: Verify no stale `PER_CARD_CAP` references remain**

Run: `grep -rn "PER_CARD_CAP" .`
Expected: no matches (constant fully renamed).

- [ ] **Step 8: Lint**

Run: `ruff check recommendations.py tests/test_recommendations.py`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add recommendations.py tests/test_recommendations.py
git commit -m "feat: whole-series entries with read flag and series_total"
```

---

### Task 2: Backend — lock `read` / `series_total` in the discover contract

**Files:**
- Test: `tests/test_discover_recommendations.py` (add one case)
- (No `app.py` change expected — `_discover_personalized` passes `build_all` through unchanged.)

**Interfaces:**
- Consumes: the grouped `continue_series` payload from Task 1 (`series_total`, per-entry `read`).
- Produces: verified contract — `GET /api/discover?category=continue_series` returns `series_total` and per-entry `read` untouched; never 500.

- [ ] **Step 1: Read the existing grouped contract test for the fixture/auth pattern**

Run: `pytest tests/test_discover_recommendations.py -k grouped -v`
Expected: PASS (existing `test_continue_series_returns_grouped_structure` is green). This confirms the `auth_client` fixture and monkeypatch pattern you'll reuse.

- [ ] **Step 2: Add the passthrough contract test**

Add to `tests/test_discover_recommendations.py`:

```python
def test_continue_series_passes_read_and_series_total(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_metadata_client", lambda: object())
    monkeypatch.setattr(recommendations, "build_all", lambda client: {
        "continue_series": [{
            "series_id": 1, "series_name": "Stormlight", "series_total": 15,
            "entries": [
                {"id": "100", "title": "The Way of Kings", "position": 1,
                 "released": True, "read": True, "match_title": "way of kings|"},
                {"id": "102", "title": "Oathbringer", "position": 3,
                 "released": True, "read": False, "match_title": "oathbringer|"},
            ],
        }],
        "more_by_authors": [], "want_to_read": [],
    })
    resp = auth_client.get("/api/discover?category=continue_series")
    assert resp.status_code == 200
    group = resp.get_json()[0]
    assert group["series_total"] == 15
    assert group["entries"][0]["read"] is True
    assert group["entries"][1]["read"] is False
```

- [ ] **Step 3: Run the test**

Run: `pytest tests/test_discover_recommendations.py -k passes_read -v`
Expected: PASS (the endpoint already passes `build_all`'s shape through). If it fails, the fix belongs in `_discover_personalized` in `app.py` — but no change is anticipated.

- [ ] **Step 4: Full backend gate**

Run: `ruff check . ; pytest`
Expected: ruff clean; pytest all green. Backend Definition-of-Done.

- [ ] **Step 5: Commit**

```bash
git add tests/test_discover_recommendations.py
git commit -m "test: lock read/series_total in grouped discover contract"
```

---

### Task 3: Frontend — true-position label, read-aware default, "Read ✓" badge

**Files:**
- Modify: `static/js/app.js` — `renderSeriesCard` (356-369), `nextGapIndex` (382-387), `renderSeriesEntry` (417-450)
- Modify: `static/css/style.css` — append `.book-badge.read`

**Note:** No JS unit-test harness exists; this task is verified in-browser in Task 4. Keep changes minimal and self-contained.

**Interfaces:**
- Consumes: grouped payload from Task 1 (`group.series_total`, `entry.position`, `entry.read`); existing `bookOwnership`, `entryFullyOwned`, `seriesAvailability`, `NO_COVER`, `openDownloadModal`.
- Produces: the card shows `entry.position / group.series_total`; the default entry is the first released **not-read** not-fully-owned installment (fallback: first not-read entry); read entries render a "Read ✓" badge alongside library badges.

- [ ] **Step 1: Carry `series_total` onto the card element**

In `static/js/app.js`, replace `renderSeriesCard` (lines 356-369) with:

```javascript
function renderSeriesCard(group) {
    const entriesJson = JSON.stringify(group.entries).replace(/"/g, "&quot;");
    const nameJson = (group.series_name || "").replace(/"/g, "&quot;");
    const total = group.series_total || group.entries.length;
    return `
        <div class="series-card" data-entries="${entriesJson}" data-index="0"
             data-total="${total}" data-series="${nameJson}">
            <div class="series-card-name">${group.series_name || ""}</div>
            <div class="series-card-body"></div>
            <div class="series-card-nav">
                <button class="series-arrow series-prev" aria-label="Previous">‹</button>
                <span class="series-pos"></span>
                <button class="series-arrow series-next" aria-label="Next">›</button>
            </div>
        </div>`;
}
```

- [ ] **Step 2: Make the default index skip read installments**

Replace `nextGapIndex` (lines 382-387) with:

```javascript
// First entry the user should act on: released, not already read, not fully owned.
// Falls back to the first not-read entry (e.g. only unreleased installments remain)
// so the card still opens past the read backlog rather than on book 1.
function nextGapIndex(entries, availability) {
    for (let i = 0; i < entries.length; i++) {
        const e = entries[i];
        if (!e.read && e.released && !entryFullyOwned(e, availability)) return i;
    }
    for (let i = 0; i < entries.length; i++) {
        if (!entries[i].read) return i;
    }
    return -1;
}
```

- [ ] **Step 3: Use the true position for the label and add the "Read ✓" badge**

Replace `renderSeriesEntry` (lines 417-450) with:

```javascript
function renderSeriesEntry(card, entries) {
    const idx = parseInt(card.dataset.index, 10);
    const entry = entries[idx];
    const body = card.querySelector(".series-card-body");
    const cover = entry.cover || NO_COVER;
    const author = Array.isArray(entry.authors) ? entry.authors.join(", ") : "";
    const year = (entry.publishedDate || "").substring(0, 4);

    let badges = "";
    if (entry.read) badges += '<span class="book-badge read">Read ✓</span>';
    if (!entry.released) {
        badges += `<span class="book-badge coming">Coming${year ? " " + year : ""}</span>`;
    } else if (seriesAvailability) {
        const own = bookOwnership(entry, seriesAvailability);
        if (own.ebookRequested) badges += '<span class="book-badge ebook-requested">eBook Requested</span>';
        else if (own.hasEbook) badges += '<span class="book-badge ebook">eBook ✓</span>';
        if (own.audiobookRequested) badges += '<span class="book-badge audiobook-requested">Audiobook Requested</span>';
        else if (own.hasAudiobook) badges += '<span class="book-badge audiobook">Audiobook ✓</span>';
    }

    body.innerHTML = `
        <img class="series-cover" src="${cover}" alt="${entry.title || ""}" loading="lazy"
             onerror="this.onerror=null;this.src=window.NO_COVER">
        <div class="series-entry-title" title="${entry.title || ""}">${entry.title || ""}</div>
        <div class="series-entry-author">${author}${year ? " (" + year + ")" : ""}</div>
        <div class="book-badges">${badges}</div>`;

    // Clicking a released entry opens the request modal; unreleased is inert.
    body.onclick = entry.released ? () => openDownloadModal(entry) : null;
    body.classList.toggle("series-unreleased", !entry.released);

    const total = card.dataset.total || entries.length;
    card.querySelector(".series-pos").textContent = `${entry.position} / ${total}`;
    card.querySelector(".series-prev").disabled = idx === 0;
    card.querySelector(".series-next").disabled = idx === entries.length - 1;
}
```

- [ ] **Step 4: Append the "Read ✓" badge style**

Append to `static/css/style.css`:

```css
.book-badge.read { background: #16a34a; color: #fff; }
```

- [ ] **Step 5: Syntax check**

Run: `node --check static/js/app.js`
Expected: no output (valid syntax). If `node` is unavailable, skip — Task 4 catches runtime errors in-browser.

- [ ] **Step 6: Commit**

```bash
git add static/js/app.js static/css/style.css
git commit -m "feat: whole-series scroll — true position label, read-aware default, Read badge"
```

---

### Task 4: Browser verification + TODO capture

**Files:**
- Modify (if needed): `TODO.md` (create/append)

**Prereq:** backend gate green — run `ruff check . ; pytest` and confirm before starting. A Hardcover token plus the ebook **and** audiobook Readarr backends are configured on the live account (confirmed), so a partly-read series with mixed ownership can be exercised.

- [ ] **Step 1: Start the app**

Run: `python app.py` (dev server on `0.0.0.0:5000`, debug). (A server may already be running from this session — reuse it if so.)

- [ ] **Step 2: Load browser tools and open Discover**

Load the `claude-in-chrome` MCP tools via ToolSearch (one batched call), open a new tab to `http://127.0.0.1:5000`, log in, and open the Discover page. Record a GIF of the series-card interaction (`series_navigation.gif`).

- [ ] **Step 3: Verify each acceptance criterion visually (report only what is on screen)**

Confirm and capture:
1. A "Continue the Series" card **opens on the next book to get** (first released, not-read, not-fully-owned), not on book 1.
2. The position label shows the **true series position**, e.g. "3 / 15" — not "1 / 3".
3. Pressing **‹ reaches earlier read installments** (book 1), which show a **"Read ✓"** badge; pressing **› reaches the unreleased tail**, showing "Coming ‹year›".
4. Arrows **clamp** at both ends (‹ disabled on book 1, › disabled on the last installment).
5. An installment you own in one format but not the other shows the correct **library badge** (e.g. "eBook ✓") in addition to "Read ✓" where applicable.
6. Clicking a released entry (including a read one) still **opens the request modal**; an unreleased entry is not clickable.

- [ ] **Step 4: Record any unresolved issue in TODO.md**

For anything that can't be made correct in scope, append to `TODO.md`: what the issue is, where it occurs (file/function), and steps already tried. If everything passes, no entry is needed.

- [ ] **Step 5: Final gate + commit any verification-driven fixes**

Run: `ruff check . ; pytest`
Expected: ruff clean; pytest green. Commit any fixes made during verification:

```bash
git add -A
git commit -m "fix: address browser-verification findings for whole-series navigation"
```

---

## Self-Review

**Spec coverage:**
- "Entries become the whole primary run, read positions included + flagged" → Task 1 (`select_continue_series` rewrite; `read` flag; canonical read-edition preference). ✓
- "Series selection unchanged (gate on an upcoming installment)" → Task 1 gate `if not any(not is_read ...)`. ✓
- "`series_total` label denominator (primary_books_count, fallback max position)" → Task 1 `series_total`; Task 3 label. ✓
- "Cap → SERIES_ENTRIES_CAP = 60" → Task 1 Step 4/5, retargeted cap test. ✓
- "Label uses position / series_total" → Task 3 Step 3. ✓
- "Default index skips read; fallback when only unreleased remain" → Task 3 Step 2 (two-pass `nextGapIndex`). ✓
- "Read ✓ badge in addition to library badges" → Task 3 Step 3/4. ✓
- "Discover contract passes read/series_total" → Task 2. ✓
- "Browser verification per Garrett's workflow" → Task 4. ✓
- Constraints (no deps; app.py thin; token unexposed; `[]`-on-failure) → unchanged by these tasks; `build_all` degradation path untouched. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to" placeholders; every code step shows complete code.

**Type consistency:** `series_total` (int) + per-entry `read` (bool) produced in Task 1, asserted in Task 1/Task 2, consumed in Task 3 (`group.series_total` → `data-total`; `entry.read`, `entry.position`). `SERIES_ENTRIES_CAP` replaces `PER_CARD_CAP` consistently (constant, rewrite, test, grep guard). `nextGapIndex`/`entryFullyOwned`/`bookOwnership`/`renderSeriesEntry` names match the existing `static/js/app.js`. ✓
