'use strict';

const PAGE_SIZE = 50;
const $ = (id) => document.getElementById(id);

// Nodes are built rather than concatenated into HTML. Titles and usernames come
// from scraped pages, and string interpolation into an attribute is exactly the
// kind of thing that works until one film has a quote in its name.
function el(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
        if (value === null || value === undefined || value === false) continue;
        if (key === 'class') node.className = value;
        else if (key === 'text') node.textContent = value;
        else if (key === 'onclick') node.addEventListener('click', value);
        else node.setAttribute(key, value);
    }
    for (const child of children.flat()) {
        if (child === null || child === undefined || child === false) continue;
        node.append(child);
    }
    return node;
}

function show(node, visible) {
    node.classList.toggle('hidden', !visible);
}

function debounce(fn, ms) {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), ms);
    };
}

async function getJSON(url) {
    const response = await fetch(url);
    let body = {};
    try {
        body = await response.json();
    } catch {
        // Fall through to the status-based message below.
    }
    if (!response.ok) {
        throw new Error(body.error || `Request failed (${response.status})`);
    }
    return body;
}

function relativeTime(epochSeconds) {
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - epochSeconds));
    if (seconds < 60) return 'just now';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
}

// ── shared state ──

const state = {
    friends: [],
    nicknames: {},
};

const displayName = (username) => state.nicknames[username] || username;

function renderWarnings(warnings) {
    const box = $('warnings');
    box.replaceChildren();
    if (!warnings || !warnings.length) {
        show(box, false);
        return;
    }
    for (const warning of warnings) {
        box.append(el('div', { class: 'warning-item', text: warning }));
    }
    show(box, true);
}

function setSyncStatus(generatedAt) {
    if (generatedAt) {
        $('sync-status').textContent = `Synced ${relativeTime(generatedAt)}`;
    }
}

// ── tabs ──

document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach((t) => {
            t.classList.remove('active');
            t.setAttribute('aria-selected', 'false');
        });
        document.querySelectorAll('.tab-content').forEach((c) => c.classList.remove('active'));
        tab.classList.add('active');
        tab.setAttribute('aria-selected', 'true');
        $(`tab-${tab.dataset.tab}`).classList.add('active');

        if (tab.dataset.tab === 'overlap') overlapView.ensureLoaded();
        if (tab.dataset.tab === 'missing') missingView.ensureLoaded();
    });
});

// ── random picker ──

$('toggle-filters').addEventListener('click', () => {
    const panel = $('filters-panel');
    const hidden = panel.classList.toggle('hidden');
    $('toggle-filters').textContent = hidden ? 'Show Filters' : 'Hide Filters';
    $('toggle-filters').setAttribute('aria-expanded', String(!hidden));
});

$('clear-filters').addEventListener('click', () => {
    $('genre-select').selectedIndex = -1;
    ['year-min', 'year-max', 'runtime-min', 'runtime-max'].forEach((id) => {
        $(id).value = '';
    });
});

async function loadGenres() {
    try {
        const data = await getJSON('/api/genres');
        const select = $('genre-select');
        select.replaceChildren(
            ...data.genres.map((g) => el('option', { value: g, text: g }))
        );
    } catch (e) {
        console.error('Failed to load genres:', e);
    }
}

function randomFilterParams() {
    const params = new URLSearchParams();
    const genres = Array.from($('genre-select').selectedOptions).map((o) => o.value);
    if (genres.length) params.set('genres', genres.join(','));
    for (const [key, id] of [
        ['year_min', 'year-min'],
        ['year_max', 'year-max'],
        ['runtime_min', 'runtime-min'],
        ['runtime_max', 'runtime-max'],
    ]) {
        const value = $(id).value.trim();
        if (value) params.set(key, value);
    }
    return params;
}

$('get-movie-btn').addEventListener('click', async () => {
    const button = $('get-movie-btn');
    show($('movie-card'), false);
    show($('random-error'), false);
    show($('random-loading'), true);
    button.disabled = true;

    try {
        const params = randomFilterParams().toString();
        const movie = await getJSON(`/api/random-movie${params ? `?${params}` : ''}`);
        displayMovie(movie);
    } catch (e) {
        $('random-error').textContent = e.message;
        show($('random-error'), true);
    } finally {
        show($('random-loading'), false);
        button.disabled = false;
    }
});

function displayMovie(movie) {
    $('movie-title').textContent = movie.name || 'Untitled';

    const meta = [];
    if (movie.year) meta.push(String(movie.year));
    if (movie.runtime) meta.push(`${movie.runtime} min`);
    if (movie.official_rating) meta.push(movie.official_rating);
    if (movie.community_rating) meta.push(`★ ${movie.community_rating.toFixed(1)}`);
    $('movie-meta').replaceChildren(
        ...meta.map((item) => el('span', { class: 'meta-item', text: item }))
    );

    $('movie-genres').replaceChildren(
        ...(movie.genres || []).map((g) => el('span', { class: 'genre-tag', text: g }))
    );

    $('movie-overview').textContent = movie.overview || 'No overview available.';

    const img = $('movie-image');
    img.src = movie.image_url || '';
    img.alt = movie.image_url ? `${movie.name} poster` : '';

    show($('movie-card'), true);
}

// ── friend chips ──

async function loadFriends() {
    try {
        const data = await getJSON('/api/friends');
        state.friends = data.friends || [];
        state.nicknames = data.nicknames || {};
    } catch (e) {
        console.error('Failed to load friends:', e);
    }
    overlapView.renderChips();
    missingView.renderChips();
}

// ── list views (overlap & missing) ──

function createListView(config) {
    const { name, endpoint, showAvailability } = config;
    let all = [];
    let visible = PAGE_SIZE;
    let loaded = false;
    let inFlight = 0;

    const listNode = $(`${name}-list`);
    const emptyNode = $(`${name}-empty`);
    const countNode = $(`${name}-count`);
    const moreNode = $(`${name}-more`);
    const errorNode = $(`${name}-error`);
    const loadingNode = $(`${name}-loading`);
    const searchNode = $(`${name}-search`);
    const chipsNode = $(`${name}-friend-filter`);

    function selectedFriends() {
        return Array.from(chipsNode.querySelectorAll('input:checked')).map((cb) => cb.value);
    }

    function matchMode() {
        const checked = document.querySelector(`input[name="${name}-match"]:checked`);
        return checked ? checked.value : 'any';
    }

    function queryParams() {
        const params = new URLSearchParams();
        const friends = selectedFriends();
        // Every chip selected is the same as no constraint; sending the whole
        // list would make "all of them" mean "wanted by literally everyone".
        if (friends.length && friends.length < state.friends.length) {
            params.set('friends', friends.join(','));
            params.set('match', matchMode());
        } else if (friends.length === state.friends.length && matchMode() === 'all') {
            params.set('friends', friends.join(','));
            params.set('match', 'all');
        }
        if (showAvailability && $('jellyfin-only').checked) {
            params.set('jellyfin_only', 'true');
        }
        return params;
    }

    function searchTerm() {
        return searchNode.value.trim().toLowerCase();
    }

    function filtered() {
        const term = searchTerm();
        if (!term) return all;
        return all.filter((movie) => (movie.name || '').toLowerCase().includes(term));
    }

    function movieRow(movie) {
        const title = movie.url
            ? el('a', { href: movie.url, target: '_blank', rel: 'noopener noreferrer', text: movie.name })
            : document.createTextNode(movie.name || '');

        const info = el(
            'div',
            { class: 'movie-info' },
            el(
                'div',
                { class: 'movie-title' },
                title,
                movie.year ? el('span', { class: 'movie-year', text: `(${movie.year})` }) : null
            ),
            el(
                'div',
                { class: 'movie-badges' },
                (movie.wanted_by || []).map((friend) =>
                    el('span', { class: 'friend-badge', text: displayName(friend) })
                )
            )
        );

        const status = showAvailability
            ? el('span', {
                  class: `jellyfin-status ${movie.on_jellyfin ? 'available' : 'unavailable'}`,
                  text: movie.on_jellyfin ? 'On Jellyfin' : 'Not on server',
              })
            : null;

        return el('div', { class: 'movie-list-item' }, info, status);
    }

    function render() {
        const movies = filtered();
        const page = movies.slice(0, visible);

        listNode.replaceChildren(...page.map(movieRow));

        const hasResults = movies.length > 0;
        show(countNode, hasResults);
        countNode.textContent = hasResults
            ? `Showing ${page.length} of ${movies.length} film${movies.length === 1 ? '' : 's'}`
            : '';

        show(moreNode, movies.length > page.length);
        moreNode.textContent = `Show ${Math.min(PAGE_SIZE, movies.length - page.length)} more`;

        show(emptyNode, !hasResults);
        if (!hasResults) {
            emptyNode.replaceChildren(el('p', { text: emptyMessage() }));
        }
    }

    function emptyMessage() {
        if (searchTerm()) return `No films matching “${searchNode.value.trim()}”.`;
        if (name === 'overlap') {
            return 'No films match these filters. Try selecting fewer friends, or switch to “Any of them”.';
        }
        return 'Nothing missing — every film on these watchlists is already on your server.';
    }

    async function load() {
        const request = ++inFlight;
        show(loadingNode, true);
        show(errorNode, false);
        try {
            const params = queryParams().toString();
            const data = await getJSON(`${endpoint}${params ? `?${params}` : ''}`);
            // A slower earlier request must not overwrite a newer result.
            if (request !== inFlight) return;
            all = data.movies || [];
            visible = PAGE_SIZE;
            loaded = true;
            renderWarnings(data.warnings);
            setSyncStatus(data.generated_at);
            render();
        } catch (e) {
            if (request !== inFlight) return;
            errorNode.textContent = e.message;
            show(errorNode, true);
            listNode.replaceChildren();
            show(countNode, false);
            show(moreNode, false);
        } finally {
            if (request === inFlight) show(loadingNode, false);
        }
    }

    function renderChips() {
        chipsNode.replaceChildren(
            ...state.friends.map((friend) => {
                const input = el('input', { type: 'checkbox', value: friend, checked: 'checked' });
                input.addEventListener('change', load);
                return el('label', { class: 'friend-chip' }, input, el('span', { text: displayName(friend) }));
            })
        );
    }

    searchNode.addEventListener(
        'input',
        debounce(() => {
            visible = PAGE_SIZE;
            render();
        }, 150)
    );

    moreNode.addEventListener('click', () => {
        visible += PAGE_SIZE;
        render();
    });

    document.querySelectorAll(`input[name="${name}-match"]`).forEach((radio) => {
        radio.addEventListener('change', load);
    });

    return {
        renderChips,
        reload: load,
        ensureLoaded: () => {
            if (!loaded) load();
        },
        invalidate: () => {
            loaded = false;
        },
        queryParams,
        isLoaded: () => loaded,
    };
}

const overlapView = createListView({
    name: 'overlap',
    endpoint: '/api/overlap',
    showAvailability: true,
});

const missingView = createListView({
    name: 'missing',
    endpoint: '/api/missing',
    showAvailability: false,
});

$('jellyfin-only').addEventListener('change', () => overlapView.reload());

$('overlap-random-btn').addEventListener('click', async () => {
    const button = $('overlap-random-btn');
    const result = $('overlap-random-result');
    button.disabled = true;
    try {
        // Same parameters as the list, so the pick always comes from what the
        // user can actually see rather than the whole unfiltered set.
        const params = overlapView.queryParams().toString();
        const movie = await getJSON(`/api/overlap/random${params ? `?${params}` : ''}`);
        result.replaceChildren(
            el('div', { class: 'picked-title', text: movie.name }),
            movie.year ? el('div', { class: 'picked-year', text: String(movie.year) }) : null,
            el(
                'div',
                { class: 'picked-friends' },
                (movie.wanted_by || []).map((friend) =>
                    el('span', { class: 'friend-badge', text: displayName(friend) })
                )
            ),
            el('div', { class: 'picked-status', text: movie.on_jellyfin ? 'On Jellyfin' : 'Not on your server' }),
            movie.url
                ? el('a', {
                      class: 'picked-link',
                      href: movie.url,
                      target: '_blank',
                      rel: 'noopener noreferrer',
                      text: 'View on Letterboxd',
                  })
                : null
        );
        show(result, true);
        show($('overlap-error'), false);
    } catch (e) {
        show(result, false);
        $('overlap-error').textContent = e.message;
        show($('overlap-error'), true);
    } finally {
        button.disabled = false;
    }
});

// ── refresh ──

$('refresh-cache').addEventListener('click', async () => {
    const button = $('refresh-cache');
    button.disabled = true;
    button.textContent = 'Refreshing…';
    try {
        await fetch('/api/cache/refresh', { method: 'POST' });
        overlapView.invalidate();
        missingView.invalidate();
        const active = document.querySelector('.tab.active').dataset.tab;
        if (active === 'overlap') overlapView.reload();
        else if (active === 'missing') missingView.reload();
    } catch (e) {
        console.error('Failed to refresh:', e);
    } finally {
        button.disabled = false;
        button.textContent = 'Refresh data';
    }
});

// ── boot ──

loadGenres();
loadFriends();
