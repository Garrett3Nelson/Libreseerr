// State
let currentModalBook = null;
let selectedServers = new Set(["ebook"]);
let serverConfigured = { ebook: false, audiobook: false };
let slotOptionsCache = {};
let currentUser = null;
let editingUsername = null;
let cachedAvailability = null;

// Inline SVG fallback cover. Self-contained (no network) so a missing/broken
// cover can never trigger a failing request. The old fallback pointed at
// via.placeholder.com, which is now defunct — every broken cover re-fired the
// img onerror against a dead host in an infinite loop, thrashing the network
// and making the Discover grid flicker. Exposed on window so the inline
// onerror handler (which runs in global scope) can reach it.
const NO_COVER = "data:image/svg+xml," + encodeURIComponent(
    "<svg xmlns='http://www.w3.org/2000/svg' width='200' height='300'>" +
    "<rect width='100%' height='100%' fill='#1f2937'/>" +
    "<text x='50%' y='50%' fill='#ec4899' font-family='sans-serif' font-size='16' " +
    "text-anchor='middle' dominant-baseline='middle'>No Cover</text></svg>"
);
window.NO_COVER = NO_COVER;

// Hardcover-driven personal rows, pinned above the generic ones. Only shown
// when a Hardcover token is configured (hardcoverEnabled, from /api/config).
// Empty rows are hidden by loadDiscovery's existing zero-length check.
const PERSONAL_CATEGORIES = [
    { key: "continue_series", title: "Continue the Series" },
    { key: "more_by_authors", title: "More From Authors You've Read" },
    { key: "want_to_read", title: "On Your Want-to-Read List" },
];
let hardcoverEnabled = false;

const DISCOVERY_CATEGORIES = [
    { key: "new_releases", title: "New Releases" },
    { key: "trending", title: "Trending" },
    { key: "best_sellers", title: "Best Sellers" },
    { key: "fiction", title: "Popular Fiction" },
    { key: "science_fiction", title: "Science Fiction" },
    { key: "mystery", title: "Mystery & Thriller" },
    { key: "fantasy", title: "Fantasy" },
    { key: "romance", title: "Romance" },
    { key: "nonfiction", title: "Non-Fiction" },
    { key: "history", title: "History" },
    { key: "classics", title: "Classics" },
];

// ─── Auth ───

async function loadCurrentUser() {
    try {
        const resp = await fetch("/api/auth/me");
        if (resp.status === 401) {
            window.location.href = "/login";
            return;
        }
        currentUser = await resp.json();

        // Show admin-only elements if user is admin
        if (currentUser.role === "admin") {
            document.body.classList.add("is-admin");
        }

        // Set sidebar user info
        document.getElementById("sidebar-username").textContent = currentUser.username;
        document.getElementById("sidebar-role").textContent = currentUser.role;
    } catch (err) {
        window.location.href = "/login";
    }
}

async function doLogout() {
    try {
        await fetch("/api/auth/logout", { method: "POST" });
    } catch (err) {
        // ignore
    }
    window.location.href = "/login";
}

// ─── 401 Interceptor ───

const originalFetch = window.fetch;
window.fetch = async function (...args) {
    const resp = await originalFetch.apply(this, args);
    if (resp.status === 401) {
        window.location.href = "/login";
    }
    return resp;
};

// ─── Sidebar ───

function openSidebar() {
    document.getElementById("sidebar").classList.add("open");
    document.getElementById("sidebar-overlay").classList.add("active");
}

function closeSidebar() {
    document.getElementById("sidebar").classList.remove("open");
    document.getElementById("sidebar-overlay").classList.remove("active");
}

// ─── Navigation ───

document.querySelectorAll(".sidebar-link").forEach((link) => {
    link.addEventListener("click", (e) => {
        e.preventDefault();
        document.querySelectorAll(".sidebar-link").forEach((l) => l.classList.remove("active"));
        link.classList.add("active");
        document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
        const pageId = "page-" + link.dataset.page;
        document.getElementById(pageId).classList.add("active");
        if (link.dataset.page === "requests") loadRequests();
        if (link.dataset.page === "settings") { loadConfig(); loadHardcover(); }
        if (link.dataset.page === "users") { loadUsers(); loadLDAP(); loadOIDC(); }
        closeSidebar();
    });
});

// ─── Search ───

const searchInput = document.getElementById("search-input");

searchInput.addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });

let searchTimeout;
searchInput.addEventListener("input", () => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(doSearch, 500);
});

async function doSearch() {
    const query = searchInput.value.trim();
    const container = document.getElementById("discovery-content");
    const grid = document.getElementById("search-results");

    // Make sure we're on the search page
    if (!document.getElementById("page-search").classList.contains("active")) {
        document.querySelector('[data-page="search"]').click();
    }

    if (!query) {
        loadDiscovery();
        return;
    }

    // Switch to search mode
    container.style.display = "none";
    grid.style.display = "";

    grid.innerHTML = '<div class="empty-state"><div class="spinner"></div> Searching...</div>';

    try {
        const resp = await fetch("/api/search?q=" + encodeURIComponent(query));
        const data = await resp.json();
        if (data.error) {
            grid.innerHTML = `<div class="empty-state">Error: ${data.error}</div>`;
            return;
        }
        if (!data.length) {
            grid.innerHTML = '<div class="empty-state">No results found</div>';
            return;
        }
        grid.innerHTML = data.map(renderBookCard).join("");
        grid.querySelectorAll(".book-card").forEach((card) => {
            card.addEventListener("click", () => openDownloadModal(JSON.parse(card.dataset.book)));
        });
        fetchAvailability().then(applyAvailabilityBadges);
    } catch (err) {
        grid.innerHTML = `<div class="empty-state">Error: ${err.message}</div>`;
    }
}

function renderBookCard(book) {
    const title = book.title || "Unknown Title";
    const author = book.author?.authorName || (Array.isArray(book.authors) ? book.authors.join(", ") : "Unknown Author");
    const year = book.releaseDate ? book.releaseDate.substring(0, 4) : book.publishedDate ? book.publishedDate.substring(0, 4) : "";
    let cover = "";
    if (book.author?.images?.length) cover = book.author.images[0].url;
    if (!cover && book.images?.length) cover = book.images[0].url;
    if (!cover && book.cover) cover = book.cover;
    if (!cover) cover = NO_COVER;
    const bookJson = JSON.stringify(book).replace(/"/g, "&quot;");

    return `
        <div class="book-card" data-book="${bookJson}">
            <img class="book-cover" src="${cover}" alt="${title}" loading="lazy"
                 onerror="this.onerror=null;this.src=window.NO_COVER">
            <div class="book-overlay">
                <div class="book-overlay-title">${title}</div>
                <div class="book-overlay-author">${author}${year ? " (" + year + ")" : ""}</div>
            </div>
            <div class="book-info">
                <div class="book-title" title="${title}">${title}</div>
                <div class="book-author">${author}</div>
                ${year ? `<div class="book-year">${year}</div>` : ""}
            </div>
        </div>`;
}

async function fetchAvailability() {
    if (cachedAvailability) return cachedAvailability;
    try {
        const resp = await fetch("/api/availability");
        cachedAvailability = await resp.json();
    } catch {
        cachedAvailability = { ebook: { isbns: [], titles: [] }, audiobook: { isbns: [], titles: [] } };
    }
    return cachedAvailability;
}

// Title-only prefix of a match key ("normtitle|author" -> "normtitle|").
// Bridges the case where one side knows the author and the other doesn't —
// pure string op, no normalization in JS (that lives in Python matching.py).
function titleOnlyKey(matchTitle) {
    if (!matchTitle) return "";
    return matchTitle.split("|")[0] + "|";
}

function slotHas(set, isbn, matchTitle) {
    if (!set) return false;
    if (isbn && set.isbns.includes(isbn)) return true;
    if (matchTitle && set.titles.includes(matchTitle)) return true;
    const prefix = titleOnlyKey(matchTitle);
    return !!prefix && set.titles.includes(prefix);
}

// Central owned/requested check for one book against /api/availability.
function bookOwnership(book, availability) {
    const isbn = book.isbn_13 || book.isbn_10 || book.isbn13 || book.isbn10 || "";
    const key = book.match_title || "";
    return {
        hasEbook: slotHas(availability.ebook, isbn, key),
        hasAudiobook: slotHas(availability.audiobook, isbn, key),
        ebookRequested: slotHas(availability.ebook_requests, isbn, key),
        audiobookRequested: slotHas(availability.audiobook_requests, isbn, key),
    };
}

function applyAvailabilityBadges(availability) {
    document.querySelectorAll(".book-card").forEach((card) => {
        if (card.querySelector(".book-badges")) return;
        let book;
        try { book = JSON.parse(card.dataset.book); } catch { return; }
        const own = bookOwnership(book, availability);

        // "Requested" takes priority over "available" for the same server type
        const showEbook = own.ebookRequested ? "requested" : (own.hasEbook ? "available" : null);
        const showAudiobook = own.audiobookRequested ? "requested" : (own.hasAudiobook ? "available" : null);

        if (!showEbook && !showAudiobook) return;

        let html = '<div class="book-badges">';
        if (showEbook === "available") html += '<span class="book-badge ebook">eBook ✓</span>';
        else if (showEbook === "requested") html += '<span class="book-badge ebook-requested">eBook Requested</span>';
        if (showAudiobook === "available") html += '<span class="book-badge audiobook">Audiobook ✓</span>';
        else if (showAudiobook === "requested") html += '<span class="book-badge audiobook-requested">Audiobook Requested</span>';
        html += '</div>';
        card.querySelector(".book-info").insertAdjacentHTML("beforeend", html);
    });
}

function renderDiscoveryRow(category) {
    const cards = category.books.map(renderBookCard).join("");
    return `
        <div class="discovery-row">
            <div class="discovery-row-header">
                <div class="discovery-row-title">${category.title}</div>
            </div>
            <div class="discovery-row-scroll">${cards}</div>
        </div>`;
}

async function loadDiscovery() {
    const container = document.getElementById("discovery-content");
    const searchResults = document.getElementById("search-results");

    container.style.display = "";
    searchResults.style.display = "none";

    container.innerHTML = '<div class="discovery-loading"><div class="spinner"></div> Loading discovery...</div>';

    const categories = hardcoverEnabled
        ? [...PERSONAL_CATEGORIES, ...DISCOVERY_CATEGORIES]
        : DISCOVERY_CATEGORIES;
    const promises = categories.map(async (cat) => {
        try {
            const resp = await fetch("/api/discover?category=" + encodeURIComponent(cat.key) + "&limit=20");
            const data = await resp.json();
            if (data.error || !data.length) return null;
            return { ...cat, data };
        } catch {
            return null;
        }
    });
    const results = await Promise.all(promises);

    // Dedupe across FLAT rows only: a popular work shows up in several categories
    // (Best Sellers, Fiction, Fantasy…). continue_series is grouped and rendered
    // separately, so it's exempt from the flat dedupe.
    const seen = new Set();
    const valid = [];
    for (const row of results) {
        if (!row) continue;
        if (row.key === "continue_series") {
            valid.push({ ...row, grouped: true });
            continue;
        }
        const books = row.data.filter((b) => {
            const key = b.id || ((b.title || "").toLowerCase() + "|" + ((b.authors || [])[0] || "").toLowerCase());
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
        });
        if (books.length) valid.push({ ...row, books });
    }

    if (!valid.length) {
        container.innerHTML = '<div class="empty-state">Unable to load discovery content</div>';
        return;
    }

    container.innerHTML = valid.map((row) =>
        row.grouped ? renderSeriesRow(row) : renderDiscoveryRow(row)).join("");

    // Flat book cards open the download modal on click.
    container.querySelectorAll(".book-card").forEach((card) => {
        card.addEventListener("click", () => openDownloadModal(JSON.parse(card.dataset.book)));
    });

    // Availability drives flat badges, series default-entry selection, and the
    // hide-fully-owned pass — all after /api/availability resolves.
    fetchAvailability().then((availability) => {
        applyAvailabilityBadges(availability);
        initSeriesCards(availability);
        hideFullyOwnedFlatCards(availability);
    });
}

function renderSeriesRow(row) {
    const cards = row.data.map(renderSeriesCard).join("");
    return `
        <div class="discovery-row">
            <div class="discovery-row-header">
                <div class="discovery-row-title">${row.title}</div>
            </div>
            <div class="discovery-row-scroll">${cards}</div>
        </div>`;
}

// One card per series. All entries are stored as JSON on the element; navigation
// swaps the visible entry. Data-only here — the default index and owned badges
// are set later by initSeriesCards once availability resolves.
function renderSeriesCard(group) {
    const entriesJson = JSON.stringify(group.entries).replace(/"/g, "&quot;");
    const nameJson = (group.series_name || "").replace(/"/g, "&quot;");
    return `
        <div class="series-card" data-entries="${entriesJson}" data-index="0" data-series="${nameJson}">
            <div class="series-card-name">${group.series_name || ""}</div>
            <div class="series-card-body"></div>
            <div class="series-card-nav">
                <button class="series-arrow series-prev" aria-label="Previous">‹</button>
                <span class="series-pos"></span>
                <button class="series-arrow series-next" aria-label="Next">›</button>
            </div>
        </div>`;
}

let seriesAvailability = null;

function entryFullyOwned(entry, availability) {
    const own = bookOwnership(entry, availability);
    const slots = [];
    if (serverConfigured.ebook) slots.push(own.hasEbook || own.ebookRequested);
    if (serverConfigured.audiobook) slots.push(own.hasAudiobook || own.audiobookRequested);
    return slots.length > 0 && slots.every(Boolean);
}

// First entry the user should act on: released and not fully owned.
function nextGapIndex(entries, availability) {
    for (let i = 0; i < entries.length; i++) {
        if (entries[i].released && !entryFullyOwned(entries[i], availability)) return i;
    }
    return -1;
}

function initSeriesCards(availability) {
    seriesAvailability = availability;
    document.querySelectorAll(".series-card").forEach((card) => {
        let entries;
        try { entries = JSON.parse(card.dataset.entries); } catch { entries = []; }
        const gap = nextGapIndex(entries, availability);
        if (gap === -1) { card.remove(); return; }  // fully owned series -> hide
        card.dataset.index = String(gap);
        renderSeriesEntry(card, entries);
        card.querySelector(".series-prev").addEventListener("click", () => stepSeries(card, -1));
        card.querySelector(".series-next").addEventListener("click", () => stepSeries(card, 1));
    });
    // Hide any discovery row whose series cards were all removed.
    document.querySelectorAll(".discovery-row").forEach((rowEl) => {
        const scroll = rowEl.querySelector(".discovery-row-scroll");
        if (scroll && scroll.children.length === 0) rowEl.remove();
    });
}

function stepSeries(card, delta) {
    let entries;
    try { entries = JSON.parse(card.dataset.entries); } catch { return; }
    let idx = parseInt(card.dataset.index, 10) + delta;
    idx = Math.max(0, Math.min(entries.length - 1, idx));  // clamp at ends
    card.dataset.index = String(idx);
    renderSeriesEntry(card, entries);
}

function renderSeriesEntry(card, entries) {
    const idx = parseInt(card.dataset.index, 10);
    const entry = entries[idx];
    const body = card.querySelector(".series-card-body");
    const cover = entry.cover || NO_COVER;
    const author = Array.isArray(entry.authors) ? entry.authors.join(", ") : "";
    const year = (entry.publishedDate || "").substring(0, 4);

    let badges = "";
    if (!entry.released) {
        badges = `<span class="book-badge coming">Coming${year ? " " + year : ""}</span>`;
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

    card.querySelector(".series-pos").textContent = `${idx + 1} / ${entries.length}`;
    card.querySelector(".series-prev").disabled = idx === 0;
    card.querySelector(".series-next").disabled = idx === entries.length - 1;
}

function hideFullyOwnedFlatCards(availability) {
    document.querySelectorAll(".book-card").forEach((card) => {
        let book;
        try { book = JSON.parse(card.dataset.book); } catch { return; }
        const own = bookOwnership(book, availability);
        const slots = [];
        if (serverConfigured.ebook) slots.push(own.hasEbook || own.ebookRequested);
        if (serverConfigured.audiobook) slots.push(own.hasAudiobook || own.audiobookRequested);
        if (slots.length > 0 && slots.every(Boolean)) card.remove();
    });
    // Hide any discovery row left with no cards after removals.
    document.querySelectorAll(".discovery-row").forEach((rowEl) => {
        const scroll = rowEl.querySelector(".discovery-row-scroll");
        if (scroll && scroll.children.length === 0) rowEl.remove();
    });
}

// ─── Download Modal ───

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
        btn.onclick = configured ? () => { toggleServer(slot).catch(console.error); } : null;
    });
}

async function toggleServer(slot) {
    if (selectedServers.has(slot)) selectedServers.delete(slot);
    else selectedServers.add(slot);
    renderServerButtons();
    await renderSlotOptions();
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
                    <select class="slot-profile" data-slot="${s}"><option value="">Loading...</option></select>
                </div>
                <div class="form-group">
                    <label>Root Folder</label>
                    <select class="slot-folder" data-slot="${s}"><option value="">Loading...</option></select>
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
            ? `<option value="" disabled>${opts.profiles.error}</option>`
            : opts.profiles.map((p) => `<option value="${p.id}">${p.name}</option>`).join("");
        folderSelect.innerHTML = opts.folders.error
            ? `<option value="" disabled>${opts.folders.error}</option>`
            : opts.folders.map((f) => `<option value="${f.path}">${f.path}</option>`).join("");
    } catch (err) {
        profileSelect.innerHTML = '<option value="" disabled>Error loading</option>';
        folderSelect.innerHTML = '<option value="" disabled>Error loading</option>';
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

// ─── Requests ───

async function loadRequests() {
    const list = document.getElementById("requests-list");
    try {
        const resp = await fetch("/api/requests");
        const data = await resp.json();
        if (!data.length) {
            list.innerHTML = '<div class="empty-state">No requests yet. Search for books and download them!</div>';
            return;
        }
        list.innerHTML = data.map(renderRequest).join("");
        list.querySelectorAll(".delete-btn").forEach((btn) => {
            btn.addEventListener("click", async (e) => {
                e.stopPropagation();
                const id = parseInt(btn.dataset.id);
                await fetch("/api/requests/" + id, { method: "DELETE" });
                loadRequests();
            });
        });
    } catch (err) {
        list.innerHTML = `<div class="empty-state">Error loading requests</div>`;
    }
}

function renderRequest(req) {
    const cover = req.cover_url || NO_COVER;
    const progress = req.progress || 0;
    const fillClass = req.status === "completed" ? "complete" : req.status === "error" ? "error" : "";

    let statusDisplay;
    if (req.status === "processing" || req.status === "pending") {
        statusDisplay = '<span class="status-label processing"><span class="spinner"></span> Processing</span>';
    } else if (req.status === "completed") {
        statusDisplay = `<span class="status-label completed">Completed</span>
                <div class="progress-bar">
                    <div class="progress-fill complete" style="width: 100%"></div>
                </div>`;
    } else if (req.status === "error") {
        statusDisplay = `<span class="status-label error">Error</span>
                ${req.error ? `<div class="request-error" title="${req.error}">${req.error}</div>` : ""}`;
    } else {
        // downloading
        statusDisplay = `<span class="status-label downloading">Downloading</span>
                <div class="progress-bar">
                    <div class="progress-fill" style="width: ${progress}%"></div>
                </div>`;
    }

    return `
        <div class="request-item">
            <img class="request-cover" src="${cover}" alt="${req.title}"
                 onerror="this.onerror=null;this.src=window.NO_COVER">
            <div class="request-details">
                <div class="request-title">${req.title}</div>
                <div class="request-meta">${req.author || ""}</div>
                <span class="request-server ${req.server_type}">${req.server_type}</span>
            </div>
            <div class="request-status">
                ${statusDisplay}
                <button class="btn btn-small btn-danger delete-btn" data-id="${req.id}" style="margin-top: 0.4rem">Remove</button>
            </div>
        </div>`;
}

document.getElementById("refresh-btn").addEventListener("click", async () => {
    const btn = document.getElementById("refresh-btn");
    btn.disabled = true;
    btn.textContent = "Refreshing...";
    try {
        await fetch("/api/requests/refresh", { method: "POST" });
        await loadRequests();
    } finally {
        btn.disabled = false;
        btn.textContent = "Refresh Status";
    }
});

// ─── Settings ───

async function loadConfig() {
    try {
        const resp = await fetch("/api/config");
        const data = await resp.json();
        serverConfigured = { ebook: !!data.ebook.configured, audiobook: !!data.audiobook.configured };
        hardcoverEnabled = !!data.hardcover_enabled;
        slotOptionsCache = {};
        document.getElementById("ebook-url").value = data.ebook.url || "";
        document.getElementById("ebook-api").value = data.ebook.api_key || "";
        document.getElementById("audiobook-url").value = data.audiobook.url || "";
        document.getElementById("audiobook-api").value = data.audiobook.api_key || "";
        document.getElementById("ebook-server-software").value = data.ebook.server_software || "readarr";
        document.getElementById("audiobook-server-software").value = data.audiobook.server_software || "readarr";
    } catch (err) {
        console.error("Failed to load config", err);
    }
}

window.saveConfig = async function (type) {
    const url = document.getElementById(type + "-url").value;
    const api_key = document.getElementById(type + "-api").value;
    const server_software = document.getElementById(type + "-server-software").value;
    const statusEl = document.getElementById(type + "-status");

    try {
        const resp = await fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ server_type: type, url, api_key, server_software }),
        });
        const data = await resp.json();
        statusEl.className = "status-msg success";
        statusEl.textContent = "Configuration saved!";
    } catch (err) {
        statusEl.className = "status-msg error";
        statusEl.textContent = "Error: " + err.message;
    }

    setTimeout(() => { statusEl.textContent = ""; }, 3000);
};

window.testConnection = async function (type) {
    const url = document.getElementById(type + "-url").value;
    const api_key = document.getElementById(type + "-api").value;
    const server_software = document.getElementById(type + "-server-software").value;
    const statusEl = document.getElementById(type + "-status");

    statusEl.className = "status-msg";
    statusEl.textContent = "Testing...";

    try {
        const resp = await fetch("/api/config/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url, api_key, server_software }),
        });
        const data = await resp.json();
        if (data.success) {
            statusEl.className = "status-msg success";
            statusEl.textContent = "Connected! Version: " + (data.status.version || "unknown");
        } else {
            statusEl.className = "status-msg error";
            statusEl.textContent = "Failed: " + data.error;
        }
    } catch (err) {
        statusEl.className = "status-msg error";
        statusEl.textContent = "Error: " + err.message;
    }
};

// ─── User Management ───

async function loadUsers() {
    const list = document.getElementById("users-list");
    try {
        const resp = await fetch("/api/users");
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.length) {
            list.innerHTML = '<div class="empty-state">No users found</div>';
            return;
        }
        list.innerHTML = data.map(renderUser).join("");
        list.querySelectorAll(".edit-user-btn").forEach((btn) => {
            btn.addEventListener("click", () => {
                openEditUserModal(btn.dataset.username, btn.dataset.role);
            });
        });
        list.querySelectorAll(".delete-user-btn").forEach((btn) => {
            btn.addEventListener("click", async () => {
                if (!confirm("Delete user '" + btn.dataset.username + "'?")) return;
                try {
                    const resp = await fetch("/api/users/" + encodeURIComponent(btn.dataset.username), {
                        method: "DELETE",
                    });
                    const data = await resp.json();
                    if (data.error) {
                        alert(data.error);
                    } else {
                        loadUsers();
                    }
                } catch (err) {
                    alert("Error: " + err.message);
                }
            });
        });
    } catch (err) {
        list.innerHTML = '<div class="empty-state">Error loading users</div>';
    }
}

function renderUser(user) {
    const initial = user.username.charAt(0);
    const createdDate = user.created_at ? new Date(user.created_at).toLocaleDateString() : "Unknown";
    const isSelf = currentUser && user.username === currentUser.username;
    const deleteDisabled = isSelf ? "disabled" : "";
    const deleteStyle = isSelf ? 'style="opacity:0.4;cursor:not-allowed;"' : "";

    return `
        <div class="user-item">
            <div class="user-avatar">${initial}</div>
            <div class="user-details">
                <div class="user-name">${user.username}${isSelf ? " (you)" : ""}</div>
                <div class="user-meta">Created ${createdDate}</div>
            </div>
            <span class="user-role-badge ${user.role}">${user.role}</span>
            <div class="user-actions">
                <button class="btn btn-small btn-secondary edit-user-btn"
                        data-username="${user.username}" data-role="${user.role}">Edit</button>
                <button class="btn btn-small btn-danger delete-user-btn"
                        data-username="${user.username}" ${deleteDisabled} ${deleteStyle}>Delete</button>
            </div>
        </div>`;
}

function openAddUserModal() {
    editingUsername = null;
    document.getElementById("user-modal-title").textContent = "Add User";
    document.getElementById("user-modal-username").value = "";
    document.getElementById("user-modal-username").disabled = false;
    document.getElementById("user-modal-password").value = "";
    document.getElementById("user-modal-role").value = "user";
    document.getElementById("user-modal-error").style.display = "none";
    document.getElementById("user-modal").classList.add("active");
}

function openEditUserModal(username, role) {
    editingUsername = username;
    document.getElementById("user-modal-title").textContent = "Edit User";
    document.getElementById("user-modal-username").value = username;
    document.getElementById("user-modal-username").disabled = true;
    document.getElementById("user-modal-password").value = "";
    document.getElementById("user-modal-password").placeholder = "Leave blank to keep current password";
    document.getElementById("user-modal-role").value = role;
    document.getElementById("user-modal-error").style.display = "none";
    document.getElementById("user-modal").classList.add("active");
}

function closeUserModal() {
    document.getElementById("user-modal").classList.remove("active");
    document.getElementById("user-modal-password").placeholder = "Enter password";
    editingUsername = null;
}

window.saveUserModal = async function () {
    const username = document.getElementById("user-modal-username").value.trim();
    const password = document.getElementById("user-modal-password").value;
    const role = document.getElementById("user-modal-role").value;
    const errorEl = document.getElementById("user-modal-error");
    const btn = document.getElementById("user-modal-save-btn");

    errorEl.style.display = "none";

    if (!username) {
        errorEl.textContent = "Username is required";
        errorEl.style.display = "block";
        return;
    }

    if (!editingUsername && !password) {
        errorEl.textContent = "Password is required for new users";
        errorEl.style.display = "block";
        return;
    }

    btn.disabled = true;
    btn.textContent = "Saving...";

    try {
        let resp;
        if (editingUsername) {
            // Edit existing user
            const body = { role };
            if (password) body.password = password;
            resp = await fetch("/api/users/" + encodeURIComponent(editingUsername), {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
        } else {
            // Create new user
            resp = await fetch("/api/users", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username, password, role }),
            });
        }

        const data = await resp.json();
        if (data.error) {
            errorEl.textContent = data.error;
            errorEl.style.display = "block";
        } else {
            closeUserModal();
            loadUsers();
        }
    } catch (err) {
        errorEl.textContent = "Error: " + err.message;
        errorEl.style.display = "block";
    } finally {
        btn.disabled = false;
        btn.textContent = "Save";
    }
};

// Close modals on background click
document.getElementById("download-modal").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeModal();
});
document.getElementById("user-modal").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeUserModal();
});

// ─── LDAP Configuration ───

async function loadLDAP() {
    try {
        const resp = await fetch("/api/ldap");
        const data = await resp.json();
        document.getElementById("ldap-enabled").checked = data.enabled || false;
        document.getElementById("ldap-server-url").value = data.server_url || "";
        document.getElementById("ldap-bind-dn").value = data.bind_dn || "";
        document.getElementById("ldap-bind-password").value = data.bind_password || "";
        document.getElementById("ldap-base-dn").value = data.base_dn || "";
        document.getElementById("ldap-search-filter").value = data.user_search_filter || "(sAMAccountName={username})";
        document.getElementById("ldap-default-role").value = data.default_role || "user";
    } catch (err) {
        console.error("Failed to load LDAP config", err);
    }
}

window.saveLDAP = async function () {
    const statusEl = document.getElementById("ldap-status");
    try {
        const resp = await fetch("/api/ldap", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                enabled: document.getElementById("ldap-enabled").checked,
                server_url: document.getElementById("ldap-server-url").value,
                bind_dn: document.getElementById("ldap-bind-dn").value,
                bind_password: document.getElementById("ldap-bind-password").value,
                base_dn: document.getElementById("ldap-base-dn").value,
                user_search_filter: document.getElementById("ldap-search-filter").value,
                default_role: document.getElementById("ldap-default-role").value,
            }),
        });
        const data = await resp.json();
        if (data.error) {
            statusEl.className = "status-msg error";
            statusEl.textContent = data.error;
        } else {
            statusEl.className = "status-msg success";
            statusEl.textContent = "LDAP configuration saved!";
        }
    } catch (err) {
        statusEl.className = "status-msg error";
        statusEl.textContent = "Error: " + err.message;
    }
    setTimeout(() => { statusEl.textContent = ""; }, 3000);
};

window.testLDAP = async function () {
    const statusEl = document.getElementById("ldap-status");
    statusEl.className = "status-msg";
    statusEl.textContent = "Testing...";
    try {
        const resp = await fetch("/api/ldap/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                server_url: document.getElementById("ldap-server-url").value,
                bind_dn: document.getElementById("ldap-bind-dn").value,
                bind_password: document.getElementById("ldap-bind-password").value,
                base_dn: document.getElementById("ldap-base-dn").value,
                user_search_filter: document.getElementById("ldap-search-filter").value,
            }),
        });
        const data = await resp.json();
        if (data.success) {
            statusEl.className = "status-msg success";
            statusEl.textContent = data.message;
        } else {
            statusEl.className = "status-msg error";
            statusEl.textContent = "Failed: " + data.error;
        }
    } catch (err) {
        statusEl.className = "status-msg error";
        statusEl.textContent = "Error: " + err.message;
    }
};

// ─── OIDC Configuration ───

async function loadOIDC() {
    try {
        const resp = await fetch("/api/oidc");
        const data = await resp.json();
        const unavailableEl = document.getElementById("oidc-unavailable");
        if (data.available === false) {
            if (unavailableEl) unavailableEl.style.display = "block";
        } else if (unavailableEl) {
            unavailableEl.style.display = "none";
        }
        document.getElementById("oidc-enabled").checked = data.enabled || false;
        document.getElementById("oidc-display-name").value = data.display_name || "OIDC";
        document.getElementById("oidc-issuer-url").value = data.issuer_url || "";
        document.getElementById("oidc-client-id").value = data.client_id || "";
        document.getElementById("oidc-client-secret").value = data.client_secret || "";
        document.getElementById("oidc-scope").value = data.scope || "openid profile email";
        document.getElementById("oidc-username-claim").value = data.username_claim || "preferred_username";
        document.getElementById("oidc-default-role").value = data.default_role || "user";
        document.getElementById("oidc-auto-create").checked = data.auto_create_users || false;
        document.getElementById("oidc-auto-redirect").checked = data.auto_redirect || false;
    } catch (err) {
        console.error("Failed to load OIDC config", err);
    }
}

window.saveOIDC = async function () {
    const statusEl = document.getElementById("oidc-status");
    try {
        const resp = await fetch("/api/oidc", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                enabled: document.getElementById("oidc-enabled").checked,
                display_name: document.getElementById("oidc-display-name").value,
                issuer_url: document.getElementById("oidc-issuer-url").value,
                client_id: document.getElementById("oidc-client-id").value,
                client_secret: document.getElementById("oidc-client-secret").value,
                scope: document.getElementById("oidc-scope").value,
                username_claim: document.getElementById("oidc-username-claim").value,
                default_role: document.getElementById("oidc-default-role").value,
                auto_create_users: document.getElementById("oidc-auto-create").checked,
                auto_redirect: document.getElementById("oidc-auto-redirect").checked,
            }),
        });
        const data = await resp.json();
        if (data.error) {
            statusEl.className = "status-msg error";
            statusEl.textContent = data.error;
        } else {
            statusEl.className = "status-msg success";
            statusEl.textContent = "OIDC configuration saved!";
        }
    } catch (err) {
        statusEl.className = "status-msg error";
        statusEl.textContent = "Error: " + err.message;
    }
    setTimeout(() => { statusEl.textContent = ""; }, 3000);
};

window.testOIDC = async function () {
    const statusEl = document.getElementById("oidc-status");
    statusEl.className = "status-msg";
    statusEl.textContent = "Testing...";
    try {
        const resp = await fetch("/api/oidc/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                issuer_url: document.getElementById("oidc-issuer-url").value,
            }),
        });
        const data = await resp.json();
        if (data.success) {
            statusEl.className = "status-msg success";
            statusEl.textContent = data.message;
        } else {
            statusEl.className = "status-msg error";
            statusEl.textContent = "Failed: " + data.error;
        }
    } catch (err) {
        statusEl.className = "status-msg error";
        statusEl.textContent = "Error: " + err.message;
    }
};

// ─── Hardcover (metadata source) Configuration ───

async function loadHardcover() {
    try {
        const resp = await fetch("/api/hardcover");
        const data = await resp.json();
        document.getElementById("hardcover-token").value = data.token || "";
    } catch (err) {
        console.error("Failed to load Hardcover config", err);
    }
}

window.saveHardcover = async function () {
    const statusEl = document.getElementById("hardcover-status");
    try {
        const resp = await fetch("/api/hardcover", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                token: document.getElementById("hardcover-token").value,
            }),
        });
        const data = await resp.json();
        if (data.error) {
            statusEl.className = "status-msg error";
            statusEl.textContent = data.error;
        } else {
            statusEl.className = "status-msg success";
            statusEl.textContent = "Hardcover settings saved!";
        }
    } catch (err) {
        statusEl.className = "status-msg error";
        statusEl.textContent = "Error: " + err.message;
    }
    setTimeout(() => { statusEl.textContent = ""; }, 3000);
};

window.testHardcover = async function () {
    const statusEl = document.getElementById("hardcover-status");
    statusEl.className = "status-msg";
    statusEl.textContent = "Testing...";
    try {
        const resp = await fetch("/api/hardcover/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                token: document.getElementById("hardcover-token").value,
            }),
        });
        const data = await resp.json();
        if (data.success) {
            statusEl.className = "status-msg success";
            statusEl.textContent = data.message;
        } else {
            statusEl.className = "status-msg error";
            statusEl.textContent = "Failed: " + data.error;
        }
    } catch (err) {
        statusEl.className = "status-msg error";
        statusEl.textContent = "Error: " + err.message;
    }
};

// ─── Init ───

// Load current user first, then the rest
loadCurrentUser().then(async () => {
    await loadConfig();  // sets hardcoverEnabled before discovery builds its rows
    loadDiscovery();
});
