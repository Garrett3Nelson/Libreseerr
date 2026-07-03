# Request both ebook + audiobook at once — design

Upstream feature request: zamnzim/Libreseerr issue #8. A user (LazyLibrarian
backend) almost always wants both the ebook and the audiobook and asked to
request both in one action instead of two separate requests.

## Goal

From the download modal, let the user request a book for the ebook slot, the
audiobook slot, or **both at once**, with each format keeping its own quality
profile and root folder.

## Constraints / context

- Two independent server slots exist: `"ebook"` and `"audiobook"`, each with its
  own URL/API key/software and its own quality profiles + root folders
  (`config["ebook"]`, `config["audiobook"]`). Profiles/folders are fetched
  per-slot via `/api/profiles/<slot>` and `/api/rootfolders/<slot>`.
- A request entry already carries exactly one `server_type`. Status refresh
  (`/api/requests/refresh`) and delete (`/api/requests/<id>`) operate per entry.
- Current modal: two mutually-exclusive `.server-btn` buttons, one quality-profile
  `<select>`, one root-folder `<select>`, one Download button. Switching slot
  reloads that slot's options (`loadModalOptions` in `static/js/app.js`).

## Approach

"Both" = **two independent request entries**, one per slot, created from one
submit. No new combined/linked entity — this reuses the existing per-entry
tracking, refresh, and delete with zero changes to those paths, and makes
partial failure natural (one slot can error while the other proceeds).

### UI (`templates/index.html`, `static/js/app.js`, `static/css`)

- The two server buttons become **toggles (multi-select)**. Selection is tracked
  as a `Set` of slot names, defaulting to `{"ebook"}` (preserves today's default).
- A slot whose server is **not configured** is rendered **disabled/greyed** and
  cannot be toggled on. It carries a native hover tooltip (`title` attribute),
  e.g. `"Audiobook server isn't configured. Configure it in Settings to request
  this format."` Configuration state comes from the `configured` booleans already
  present in the `/api/config` response (`app.py:760` / `app.py:766`). `loadConfig()`
  already runs at app init and after every settings save; it is extended to also
  store a module-level `serverConfigured = {ebook: data.ebook.configured,
  audiobook: data.audiobook.configured}` which the modal reads. No new endpoint,
  no extra API-key exposure beyond the existing init fetch.
- The single profile/folder pair becomes **one section per selected slot**,
  rendered dynamically:
  - Toggling a slot **on** appends a section (`<h*>Ebook</h*>` + quality `<select>`
    + folder `<select>`) and lazy-loads that slot's profiles/folders.
  - Toggling a slot **off** removes its section.
  - Sections cache their loaded options so re-toggling doesn't refetch.
- **Download** button is disabled until: at least one slot is selected AND every
  selected slot has both a profile and a folder chosen.

### Backend (`app.py`)

- Extract the body of `create_request` (lines ~1083–1147: build entry, lookup,
  add_book, status) into a helper:
  `_create_single_request(server_type, book_data, quality_profile_id, root_folder) -> dict`
  returning the request_entry (without inserting/saving).
- `/api/request` accepts a **`targets`** array:
  `{ "book": {...}, "targets": [ {server_type, quality_profile_id, root_folder}, ... ] }`
  (length 1 or 2). For each target it calls the helper; failures are captured on
  that entry (`status: "error"`) and do not abort the others. All resulting
  entries are inserted into `requests_history` (under `lock`) and saved once.
  The endpoint returns the **list** of created entries.
- Validation: `targets` must be a non-empty list; each target needs
  `server_type` in `("ebook","audiobook")`, `quality_profile_id`, `root_folder`;
  the target's slot must be configured (`get_client` returns a client). An
  invalid/empty `targets` → 400.

### Frontend submit (`static/js/app.js`)

- `confirm-download-btn` handler builds `targets` from the selected slots and
  their per-section selector values, POSTs once to `/api/request`, then navigates
  to the Requests page (which now lists both new entries). If the response array
  contains any entry with `status: "error"`, surface a non-blocking notice
  naming which format(s) failed; entries that succeeded still appear.

## Edge cases

- Only one slot configured → other toggle disabled with tooltip; behaves exactly
  like today's single request.
- Both selected, one backend errors → two entries created, one `error` + one
  `processing`; modal closes, Requests page shows both.
- Neither slot selected → Download disabled (cannot submit).

## Testing

- `targets` with two valid slots → two entries returned, both non-error
  (fake clients).
- `targets` where one slot's `add_book` raises → that entry `status == "error"`,
  the other `status == "processing"`; both persisted.
- Single-element `targets` → one entry (back-compat after the refactor).
- Empty/invalid `targets` → 400.
- Tests use fake client objects in the existing `tests/` style
  (`test_client_contract.py` / `conftest.py`), no network.

## Out of scope (YAGNI)

- Remembering the last format selection across modal opens.
- A combined/linked "both" request entity or paired status.
- A "request both" shortcut on the search-result cards (this is modal-only).
- Any change to Bindery support (tracked separately in `TODO.md`).
