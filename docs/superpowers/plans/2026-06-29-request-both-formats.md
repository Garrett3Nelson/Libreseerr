# Request Both Formats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user request a book for the ebook slot, the audiobook slot, or both at once from the download modal, each format keeping its own quality profile and root folder.

**Architecture:** "Both" = two independent request entries created from one submit. Backend extracts the per-slot logic into a Flask-free helper (`_create_single_request`) unit-tested directly; the `/api/request` route becomes a thin wrapper accepting a `targets` array and isolating per-target failures. Frontend turns the two format buttons into multi-select toggles, renders one quality-profile + root-folder section per selected slot, and disables unconfigured slots with a hover tooltip.

**Tech Stack:** Flask (Python), vanilla JS + plain HTML/CSS (no bundler), pytest.

## Global Constraints

- No new dependencies; vanilla JS only, no bundler/build step (`CLAUDE.md`).
- Backend state pattern: mutate module globals under `lock`, then `save_*` (`CLAUDE.md` "State & persistence").
- All three backend clients are duck-typed; tests use fakes, never real network.
- Two server slots only: `"ebook"` and `"audiobook"`.
- Frontend has no JS test harness — frontend changes are verified in the browser, and only what is visible on screen counts as verified (`~/.claude/CLAUDE.md` "Browser Verification").

---

### Task 1: Backend helper `_create_single_request`

Extract the existing single-request body into a reusable, Flask-free helper. Pure refactor of behavior for one slot; no route change yet.

**Files:**
- Modify: `app.py` (the `create_request` route, currently ~`app.py:1075-1149` — locate by the `@app.route("/api/request", methods=["POST"])` decorator)
- Test: `tests/test_request_targets.py` (create)

**Interfaces:**
- Produces: `_create_single_request(server_type: str, book_data: dict, quality_profile_id: int, root_folder: str) -> dict` — returns a request_entry dict with `status` `"processing"` on success (and a `readarr_book_id` key) or `"error"` (with `error` set). Does NOT insert into `requests_history` or persist.
- Consumes: existing module-level `get_client`, `time`, `datetime`, `UTC`, `json`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_request_targets.py`:

```python
"""Tier 2: /api/request multi-target (request both formats) behavior."""
import pytest

import app as app_module


class _FakeClient:
    """Duck-typed stand-in for a backend client."""

    def __init__(self, add_raises=False, found=True):
        self.add_raises = add_raises
        self.found = found

    def _hit(self):
        return [{"title": "X", "author": {"authorName": "A"}, "foreignBookId": "1"}] if self.found else []

    def lookup_by_isbn(self, isbn):
        return self._hit()

    def search_books(self, query):
        return self._hit()

    def add_book(self, book, quality_profile_id, root_folder):
        if self.add_raises:
            raise RuntimeError("backend boom")
        return {"id": 42}


def test_single_request_success(monkeypatch):
    monkeypatch.setattr(app_module, "get_client", lambda st: _FakeClient())
    book = {"title": "Dune", "authors": ["Frank Herbert"], "isbn_13": "9780441013593"}
    entry = app_module._create_single_request("ebook", book, 1, "/books")
    assert entry["status"] == "processing"
    assert entry["server_type"] == "ebook"
    assert entry["readarr_book_id"] == 42


def test_single_request_add_book_error(monkeypatch):
    monkeypatch.setattr(app_module, "get_client", lambda st: _FakeClient(add_raises=True))
    book = {"title": "Dune", "authors": ["Frank Herbert"]}
    entry = app_module._create_single_request("audiobook", book, 2, "/audio")
    assert entry["status"] == "error"
    assert "boom" in entry["error"]


def test_single_request_no_client(monkeypatch):
    monkeypatch.setattr(app_module, "get_client", lambda st: None)
    entry = app_module._create_single_request("ebook", {"title": "X"}, 1, "/books")
    assert entry["status"] == "error"
    assert "not configured" in entry["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_request_targets.py -q`
Expected: FAIL with `AttributeError: module 'app' has no attribute '_create_single_request'`.

- [ ] **Step 3: Add the helper**

In `app.py`, directly above the `@app.route("/api/request", methods=["POST"])` decorator, add:

```python
def _create_single_request(server_type, book_data, quality_profile_id, root_folder):
    """Build and submit one request entry for a single server slot.

    Returns the request_entry dict (status 'processing' on success, 'error' on
    failure). Does NOT insert into requests_history or persist — the caller does
    that for all entries together under `lock`.
    """
    title = book_data.get("title", "Unknown")
    authors = book_data.get("authors", [])
    author_name = authors[0] if authors else "Unknown"
    cover_url = book_data.get("cover", "")
    isbn = book_data.get("isbn_13") or book_data.get("isbn_10", "")

    request_entry = {
        "id": int(time.time() * 1000),
        "title": title,
        "author": author_name,
        "cover_url": cover_url,
        "server_type": server_type,
        "quality_profile_id": quality_profile_id,
        "isbn": isbn,
        "status": "pending",
        "progress": 0,
        "error": None,
        "created_at": datetime.now(UTC).isoformat(),
    }

    try:
        client = get_client(server_type)
        if not client:
            raise ValueError(f"{server_type} server not configured")

        readarr_books = []
        if isbn:
            readarr_books = client.lookup_by_isbn(isbn)
        if not readarr_books:
            readarr_books = client.search_books(f"{title} {author_name}")

        if readarr_books:
            readarr_book = readarr_books[0]
            if not readarr_book.get("author", {}).get("authorName"):
                readarr_book["author"] = {"authorName": author_name, "foreignAuthorId": ""}
            app.logger.info(
                "Backend match for '%s': title='%s', author=%s",
                title, readarr_book.get("title"), json.dumps(readarr_book.get("author", {})),
            )
        else:
            readarr_book = {
                "title": title,
                "author": {"authorName": author_name, "foreignAuthorId": ""},
                "foreignBookId": isbn or book_data.get("id", ""),
            }
            app.logger.info("No backend match, using Open Library fallback for '%s' by '%s'", title, author_name)

        request_entry["status"] = "processing"
        result = client.add_book(readarr_book, quality_profile_id, root_folder)
        request_entry["readarr_book_id"] = result.get("id")
    except Exception as e:
        request_entry["status"] = "error"
        request_entry["error"] = str(e)

    return request_entry
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_request_targets.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_request_targets.py
git commit -m "Add _create_single_request helper for per-slot requests (issue #8)"
```

---

### Task 2: `/api/request` accepts a `targets` array

Rewrite the route to consume the helper for 1–2 targets, validate up front, assign unique ids, persist once, and return the list of entries.

**Files:**
- Modify: `app.py` (the `create_request` route body)
- Test: `tests/test_request_targets.py` (add route tests + fixture)

**Interfaces:**
- Consumes: `_create_single_request` (Task 1), module globals `lock`, `requests_history`, `save_requests`, `get_client`.
- Produces: `POST /api/request` with JSON `{ "book": {...}, "targets": [ {server_type, quality_profile_id, root_folder}, ... ] }` → `200` with a JSON **list** of request entries, or `400` `{ "error": ... }`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_request_targets.py`:

```python
@pytest.fixture
def auth_client(flask_app, monkeypatch):
    """Test client with auth bypassed and persistence/reload neutralized so the
    route mutates an in-memory requests_history we control."""
    flask_app.config["LOGIN_DISABLED"] = True
    monkeypatch.setattr(app_module, "save_requests", lambda: None)
    monkeypatch.setattr(app_module, "load_requests", lambda: None)  # reload_state no-op
    monkeypatch.setattr(app_module, "requests_history", [])
    return flask_app.test_client()


_BOOK = {"title": "Dune", "authors": ["Frank Herbert"]}


def test_request_both_creates_two_entries(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_client", lambda st: _FakeClient())
    resp = auth_client.post("/api/request", json={
        "book": _BOOK,
        "targets": [
            {"server_type": "ebook", "quality_profile_id": 1, "root_folder": "/books"},
            {"server_type": "audiobook", "quality_profile_id": 1, "root_folder": "/audio"},
        ],
    })
    assert resp.status_code == 200
    entries = resp.get_json()
    assert len(entries) == 2
    assert {e["server_type"] for e in entries} == {"ebook", "audiobook"}
    assert all(e["status"] == "processing" for e in entries)
    assert entries[0]["id"] != entries[1]["id"]  # unique ids


def test_request_isolates_partial_failure(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_client",
                        lambda st: _FakeClient(add_raises=(st == "audiobook")))
    resp = auth_client.post("/api/request", json={
        "book": _BOOK,
        "targets": [
            {"server_type": "ebook", "quality_profile_id": 1, "root_folder": "/books"},
            {"server_type": "audiobook", "quality_profile_id": 1, "root_folder": "/audio"},
        ],
    })
    assert resp.status_code == 200
    by_type = {e["server_type"]: e for e in resp.get_json()}
    assert by_type["ebook"]["status"] == "processing"
    assert by_type["audiobook"]["status"] == "error"


def test_request_single_target_still_works(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_client", lambda st: _FakeClient())
    resp = auth_client.post("/api/request", json={
        "book": _BOOK,
        "targets": [{"server_type": "ebook", "quality_profile_id": 1, "root_folder": "/books"}],
    })
    assert resp.status_code == 200
    assert len(resp.get_json()) == 1


def test_request_empty_targets_400(auth_client):
    resp = auth_client.post("/api/request", json={"book": _BOOK, "targets": []})
    assert resp.status_code == 400


def test_request_unconfigured_slot_400(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_client",
                        lambda st: None if st == "audiobook" else _FakeClient())
    resp = auth_client.post("/api/request", json={
        "book": _BOOK,
        "targets": [{"server_type": "audiobook", "quality_profile_id": 1, "root_folder": "/a"}],
    })
    assert resp.status_code == 400
    assert "not configured" in resp.get_json()["error"]


def test_request_missing_profile_400(auth_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_client", lambda st: _FakeClient())
    resp = auth_client.post("/api/request", json={
        "book": _BOOK,
        "targets": [{"server_type": "ebook", "root_folder": "/books"}],  # no quality_profile_id
    })
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_request_targets.py -q`
Expected: FAIL — the route still expects the old single `server_type` body, so the new tests get 400s / wrong shapes.

- [ ] **Step 3: Rewrite the route**

Replace the entire `create_request` function body (everything under `@app.route("/api/request", methods=["POST"])` / `@login_required` / `def create_request():`, down to its `return`) with:

```python
def create_request():
    data = request.json or {}
    book_data = data.get("book")
    targets = data.get("targets")

    if not book_data or not isinstance(targets, list) or not targets:
        return jsonify({"error": "Request must include 'book' and a non-empty 'targets' list"}), 400

    for t in targets:
        if not isinstance(t, dict):
            return jsonify({"error": "Each target must be an object"}), 400
        st = t.get("server_type")
        if st not in ("ebook", "audiobook"):
            return jsonify({"error": "target server_type must be 'ebook' or 'audiobook'"}), 400
        if not t.get("quality_profile_id") or not t.get("root_folder"):
            return jsonify({"error": f"{st} target missing quality_profile_id or root_folder"}), 400
        if not get_client(st):
            return jsonify({"error": f"{st} server not configured"}), 400

    entries = [
        _create_single_request(
            t["server_type"], book_data, t["quality_profile_id"], t["root_folder"]
        )
        for t in targets
    ]

    with lock:
        used_ids = {r["id"] for r in requests_history}
        for entry in entries:
            while entry["id"] in used_ids:
                entry["id"] += 1  # avoid same-millisecond / existing-history id collisions
            used_ids.add(entry["id"])
            requests_history.insert(0, entry)
        save_requests()

    return jsonify(entries)
```

- [ ] **Step 4: Run the full backend suite**

Run: `python -m pytest -q`
Expected: PASS (all prior tests + the new `tests/test_request_targets.py` tests).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_request_targets.py
git commit -m "Accept targets array in /api/request to request both formats (issue #8)"
```

---

### Task 3: Frontend multi-select modal with per-slot options

Turn the format buttons into multi-select toggles, disable unconfigured slots with a tooltip, render one profile+folder section per selected slot, gate the Download button, and submit a `targets` array.

**Files:**
- Modify: `templates/index.html` (download modal, ~lines 301-316)
- Modify: `static/js/app.js` (modal section ~lines 3, 300-406; `loadConfig` ~line 488)
- Modify: `static/css/style.css` (after the `.server-btn.active` rule, ~line 506)

**Interfaces:**
- Consumes: `GET /api/config` (`configured` booleans), `GET /api/profiles/<slot>`, `GET /api/rootfolders/<slot>`, `POST /api/request` (targets array from Task 2).
- Produces: browser-visible behavior only (no JS API for other tasks).

- [ ] **Step 1: Update the modal HTML**

In `templates/index.html`, replace the format/profile/folder block (the `<div class="form-group">` containing `<label>Download Server</label>` through the closing `</div>` of the root-folder group, currently lines ~302-316) with:

```html
                <div class="form-group">
                    <label>Formats</label>
                    <div class="server-select">
                        <button type="button" class="server-btn active" data-server="ebook">Ebook</button>
                        <button type="button" class="server-btn" data-server="audiobook">Audiobook</button>
                    </div>
                </div>
                <div id="slot-options"></div>
```

- [ ] **Step 2: Add CSS for disabled buttons and slot sections**

In `static/css/style.css`, immediately after the `.server-btn.active { ... }` rule (ends ~line 506), add:

```css
.server-btn:disabled, .server-btn.disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.slot-section { margin-top: 0.75rem; }
.slot-heading {
    margin: 0.5rem 0 0.25rem;
    font-size: 0.9rem;
    color: var(--text-secondary);
}
```

- [ ] **Step 3: Add module-level state and extend `loadConfig`**

In `static/js/app.js`, replace the line:

```javascript
let selectedServer = "ebook";
```

with:

```javascript
let selectedServers = new Set(["ebook"]);
let serverConfigured = { ebook: false, audiobook: false };
let slotOptionsCache = {};
```

Then in `loadConfig()`, immediately after `const data = await resp.json();`, add:

```javascript
        serverConfigured = { ebook: !!data.ebook.configured, audiobook: !!data.audiobook.configured };
        slotOptionsCache = {};
```

- [ ] **Step 4: Replace the modal functions**

In `static/js/app.js`, replace the entire modal block spanning ~lines 302-406 — from `async function openDownloadModal(book) {` through the end of the `confirm-download-btn` click handler. This span includes `openDownloadModal`, `closeModal`, `selectServer`, `loadModalOptions`, and the click handler; the replacement below re-includes `closeModal` unchanged and drops `selectServer`/`loadModalOptions` (replaced by the new functions). Use:

```javascript
async function openDownloadModal(book) {
    currentModalBook = book;
    selectedServers = new Set();
    if (serverConfigured.ebook) selectedServers.add("ebook");
    else if (serverConfigured.audiobook) selectedServers.add("audiobook");

    document.getElementById("modal-title").textContent = "Download: " + (book.title || "Unknown");
    renderServerButtons();
    await renderSlotOptions();
    document.getElementById("download-modal").classList.add("active");
}

function closeModal() {
    document.getElementById("download-modal").classList.remove("active");
    currentModalBook = null;
}

function renderServerButtons() {
    document.querySelectorAll(".server-btn").forEach((btn) => {
        const slot = btn.dataset.server;
        const label = slot === "ebook" ? "Ebook" : "Audiobook";
        const configured = serverConfigured[slot];
        btn.disabled = !configured;
        btn.classList.toggle("disabled", !configured);
        btn.title = configured
            ? ""
            : `${label} server isn't configured. Configure it in Settings to request this format.`;
        btn.classList.toggle("active", selectedServers.has(slot));
        btn.onclick = configured ? () => toggleServer(slot) : null;
    });
}

function toggleServer(slot) {
    if (selectedServers.has(slot)) selectedServers.delete(slot);
    else selectedServers.add(slot);
    renderServerButtons();
    renderSlotOptions();
}

async function renderSlotOptions() {
    const container = document.getElementById("slot-options");
    const slots = ["ebook", "audiobook"].filter((s) => selectedServers.has(s));
    container.innerHTML = slots
        .map((s) => `
            <div class="slot-section" data-slot="${s}">
                <h4 class="slot-heading">${s === "ebook" ? "Ebook" : "Audiobook"}</h4>
                <div class="form-group">
                    <label>Quality Profile</label>
                    <select class="slot-profile" data-slot="${s}"><option>Loading...</option></select>
                </div>
                <div class="form-group">
                    <label>Root Folder</label>
                    <select class="slot-folder" data-slot="${s}"><option>Loading...</option></select>
                </div>
            </div>`)
        .join("");
    await Promise.all(slots.map(loadSlotOptions));
    updateDownloadEnabled();
}

async function loadSlotOptions(slot) {
    const profileSelect = document.querySelector(`.slot-profile[data-slot="${slot}"]`);
    const folderSelect = document.querySelector(`.slot-folder[data-slot="${slot}"]`);
    if (!profileSelect || !folderSelect) return;
    try {
        let opts = slotOptionsCache[slot];
        if (!opts) {
            const [pr, fr] = await Promise.all([
                fetch("/api/profiles/" + slot),
                fetch("/api/rootfolders/" + slot),
            ]);
            opts = { profiles: await pr.json(), folders: await fr.json() };
            slotOptionsCache[slot] = opts;
        }
        profileSelect.innerHTML = opts.profiles.error
            ? `<option disabled>${opts.profiles.error}</option>`
            : opts.profiles.map((p) => `<option value="${p.id}">${p.name}</option>`).join("");
        folderSelect.innerHTML = opts.folders.error
            ? `<option disabled>${opts.folders.error}</option>`
            : opts.folders.map((f) => `<option value="${f.path}">${f.path}</option>`).join("");
    } catch (err) {
        profileSelect.innerHTML = '<option disabled>Error loading</option>';
        folderSelect.innerHTML = '<option disabled>Error loading</option>';
    }
    profileSelect.onchange = updateDownloadEnabled;
    folderSelect.onchange = updateDownloadEnabled;
}

function updateDownloadEnabled() {
    const btn = document.getElementById("confirm-download-btn");
    const slots = ["ebook", "audiobook"].filter((s) => selectedServers.has(s));
    let ok = slots.length > 0;
    for (const s of slots) {
        const p = document.querySelector(`.slot-profile[data-slot="${s}"]`);
        const f = document.querySelector(`.slot-folder[data-slot="${s}"]`);
        if (!p || !f || !p.value || !f.value) { ok = false; break; }
    }
    btn.disabled = !ok;
}

document.getElementById("confirm-download-btn").addEventListener("click", async () => {
    if (!currentModalBook) return;
    const slots = ["ebook", "audiobook"].filter((s) => selectedServers.has(s));
    if (!slots.length) return;

    const targets = [];
    for (const s of slots) {
        const qp = parseInt(document.querySelector(`.slot-profile[data-slot="${s}"]`).value);
        const rf = document.querySelector(`.slot-folder[data-slot="${s}"]`).value;
        if (!qp || !rf) {
            alert("Please select a quality profile and root folder for each format.");
            return;
        }
        targets.push({ server_type: s, quality_profile_id: qp, root_folder: rf });
    }

    const btn = document.getElementById("confirm-download-btn");
    btn.disabled = true;
    btn.textContent = "Sending...";
    try {
        const resp = await fetch("/api/request", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ book: currentModalBook, targets }),
        });
        const data = await resp.json();
        if (data.error) {
            alert("Error: " + data.error);
        } else {
            const failed = (Array.isArray(data) ? data : []).filter((e) => e.status === "error");
            if (failed.length) {
                alert("Some formats failed: " + failed.map((e) => `${e.server_type} (${e.error})`).join("; "));
            }
            closeModal();
            document.querySelector('[data-page="requests"]').click();
        }
    } catch (err) {
        alert("Error: " + err.message);
    } finally {
        btn.disabled = false;
        btn.textContent = "Download";
    }
});
```

- [ ] **Step 5: Browser verification**

Start the app: `python app.py` (dev server on `0.0.0.0:5000`). Log in, then verify on screen (report only what is visible):

1. Open a book's download modal. The **Ebook** and **Audiobook** toggles appear; the default-selected slot shows its Quality Profile + Root Folder section.
2. If a slot is unconfigured, its toggle is greyed/disabled and hovering shows the "…isn't configured. Configure it in Settings…" tooltip.
3. With both slots configured, clicking the second toggle adds a second section (its own profile + folder); clicking again removes it.
4. The Download button is disabled until each selected slot has a profile and folder chosen.
5. Selecting both and clicking Download lands on the Requests page showing **two** new entries (one ebook, one audiobook).

If backends aren't configured in the environment, verify what is reachable (toggles, disabled state + tooltip, gating) and note which end-to-end steps could not be confirmed.

- [ ] **Step 6: Commit**

```bash
git add templates/index.html static/js/app.js static/css/style.css
git commit -m "Multi-select format toggles + per-slot options in download modal (issue #8)"
```

---

### Task 4: Final full-suite check

**Files:** none (verification only).

- [ ] **Step 1: Run the entire backend test suite**

Run: `python -m pytest -q`
Expected: PASS (all tests green, including `tests/test_request_targets.py`).

- [ ] **Step 2: Confirm no stray references to removed symbols**

Run: `grep -rn "selectedServer\b\|loadModalOptions\|selectServer\|getElementById(\"quality-profile\")\|getElementById(\"root-folder\")" static/js/app.js`
Expected: no output (all old single-select symbols removed).

---

## Notes for the implementer

- The route intentionally drops the old single-`server_type` body shape; the frontend (Task 3) is the only caller and is updated in lockstep.
- Per-target failures (e.g. one backend down) become `status: "error"` entries — they are NOT a 400. Only structural/validation problems (empty targets, unknown slot, missing profile/folder, unconfigured slot) return 400 before any entry is created.
- Unique-id loop in the route guards against two entries minted in the same millisecond colliding (the `id` is `int(time.time()*1000)`).
