// Tab navigation
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById(`tab-${tab.dataset.tab}`).classList.add('active');

        // Load data on first tab switch
        if (tab.dataset.tab === 'overlap' && !overlapLoaded) loadOverlap();
        if (tab.dataset.tab === 'missing' && !missingLoaded) loadMissing();
    });
});

// State
let overlapLoaded = false;
let missingLoaded = false;
let overlapData = [];
let missingData = [];
let allFriends = [];

// ── Random Picker ──

document.addEventListener('DOMContentLoaded', () => { loadGenres(); loadFriends(); });

document.getElementById('get-movie-btn').addEventListener('click', fetchRandomMovie);
document.getElementById('toggle-filters').addEventListener('click', () => {
    const panel = document.getElementById('filters-panel');
    panel.classList.toggle('hidden');
    document.getElementById('toggle-filters').textContent =
        panel.classList.contains('hidden') ? 'Show Filters' : 'Hide Filters';
});
document.getElementById('clear-filters').addEventListener('click', () => {
    document.getElementById('genre-select').selectedIndex = -1;
    ['year-min', 'year-max', 'runtime-min', 'runtime-max'].forEach(id => {
        document.getElementById(id).value = '';
    });
});

async function loadGenres() {
    try {
        const res = await fetch('/api/genres');
        if (!res.ok) return;
        const data = await res.json();
        const select = document.getElementById('genre-select');
        select.innerHTML = '';
        data.genres.forEach(g => {
            const opt = document.createElement('option');
            opt.value = g;
            opt.textContent = g;
            select.appendChild(opt);
        });
    } catch (e) {
        console.error('Failed to load genres:', e);
    }
}

function getFilterParams() {
    const params = new URLSearchParams();
    const genres = Array.from(document.getElementById('genre-select').selectedOptions)
        .map(o => o.value);
    if (genres.length) params.append('genres', genres.join(','));
    ['year_min', 'year_max', 'runtime_min', 'runtime_max'].forEach(key => {
        const val = document.getElementById(key.replace('_', '-')).value;
        if (val) params.append(key, val);
    });
    return params.toString();
}

async function fetchRandomMovie() {
    const card = document.getElementById('movie-card');
    const error = document.getElementById('random-error');
    const loading = document.getElementById('random-loading');
    const btn = document.getElementById('get-movie-btn');

    card.classList.add('hidden');
    error.classList.add('hidden');
    loading.classList.remove('hidden');
    btn.disabled = true;

    try {
        const params = getFilterParams();
        const res = await fetch(`/api/random-movie${params ? '?' + params : ''}`);
        const data = await res.json();
        if (res.ok) {
            displayMovie(data);
        } else {
            showError('random-error', data.error || 'Failed to fetch movie');
        }
    } catch (e) {
        showError('random-error', 'Network error: unable to connect to server');
    } finally {
        loading.classList.add('hidden');
        btn.disabled = false;
    }
}

function displayMovie(movie) {
    document.getElementById('movie-title').textContent = movie.name;
    document.getElementById('movie-year').textContent = movie.year || '';
    document.getElementById('movie-runtime').textContent =
        movie.runtime ? `${movie.runtime} min` : '';

    const genresEl = document.getElementById('movie-genres');
    genresEl.innerHTML = '';
    (movie.genres || []).forEach(g => {
        const tag = document.createElement('span');
        tag.className = 'genre-tag';
        tag.textContent = g;
        genresEl.appendChild(tag);
    });

    document.getElementById('movie-overview').textContent =
        movie.overview || 'No overview available.';

    const img = document.getElementById('movie-image');
    if (movie.image_url) {
        img.src = movie.image_url;
        img.alt = `${movie.name} poster`;
    } else {
        img.src = '';
        img.alt = 'No poster available';
    }

    document.getElementById('movie-card').classList.remove('hidden');
}

// ── Friend Filter ──

async function loadFriends() {
    try {
        const res = await fetch('/api/friends');
        if (!res.ok) return;
        const data = await res.json();
        allFriends = data.friends || [];
        renderFriendFilter('overlap-friend-filter', renderOverlap);
        renderFriendFilter('missing-friend-filter', renderMissing);
    } catch (e) {
        console.error('Failed to load friends:', e);
    }
}

function renderFriendFilter(containerId, onChange) {
    const container = document.getElementById(containerId);
    container.innerHTML = allFriends.map(f => `
        <label class="friend-chip">
            <input type="checkbox" value="${esc(f)}" checked>
            <span>${esc(f)}</span>
        </label>
    `).join('');
    container.querySelectorAll('input').forEach(cb => {
        cb.addEventListener('change', onChange);
    });
}

function getSelectedFriends(containerId) {
    return Array.from(document.querySelectorAll(`#${containerId} input:checked`))
        .map(cb => cb.value);
}

function filterByFriends(movies, selectedFriends) {
    if (selectedFriends.length === allFriends.length) return movies;
    return movies.filter(m =>
        selectedFriends.every(f => (m.wanted_by || []).includes(f))
    );
}

// ── Friend Overlap ──

document.getElementById('overlap-random-btn').addEventListener('click', pickRandomOverlap);
document.getElementById('jellyfin-only').addEventListener('change', renderOverlap);

async function loadOverlap() {
    const loading = document.getElementById('overlap-loading');
    const error = document.getElementById('overlap-error');
    loading.classList.remove('hidden');
    error.classList.add('hidden');

    try {
        const res = await fetch('/api/overlap');
        const data = await res.json();
        if (!res.ok) throw new Error(data.error);
        overlapData = data.movies || [];
        overlapLoaded = true;
        renderOverlap();
    } catch (e) {
        showError('overlap-error', e.message || 'Failed to load watchlists');
    } finally {
        loading.classList.add('hidden');
    }
}

function renderOverlap() {
    const list = document.getElementById('overlap-list');
    const empty = document.getElementById('overlap-empty');
    const jellyfinOnly = document.getElementById('jellyfin-only').checked;
    const selectedFriends = getSelectedFriends('overlap-friend-filter');

    let movies = filterByFriends(overlapData, selectedFriends);
    if (jellyfinOnly) {
        movies = movies.filter(m => m.on_jellyfin);
    }

    if (!movies.length) {
        list.innerHTML = '';
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');
    list.innerHTML = movies.map(m => movieListItem(m, true)).join('');
}

async function pickRandomOverlap() {
    const jellyfinOnly = document.getElementById('jellyfin-only').checked;
    const result = document.getElementById('overlap-random-result');

    try {
        const res = await fetch(`/api/overlap/random?jellyfin_only=${jellyfinOnly}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error);

        result.innerHTML = `
            <div class="picked-title">${esc(data.name)}</div>
            <div class="picked-year">${data.year || ''}</div>
            <div class="picked-friends">
                ${(data.wanted_by || []).map(f => `<span class="friend-badge">${esc(f)}</span>`).join(' ')}
            </div>
            ${data.url ? `<a href="${esc(data.url)}" target="_blank" style="color: var(--primary); font-size: 0.85rem;">View on Letterboxd</a>` : ''}
        `;
        result.classList.remove('hidden');
    } catch (e) {
        showError('overlap-error', e.message);
    }
}

// ── Movies to Add ──

async function loadMissing() {
    const loading = document.getElementById('missing-loading');
    const error = document.getElementById('missing-error');
    loading.classList.remove('hidden');
    error.classList.add('hidden');

    try {
        const res = await fetch('/api/missing');
        const data = await res.json();
        if (!res.ok) throw new Error(data.error);

        missingData = data.movies || [];
        missingLoaded = true;
        renderMissing();
    } catch (e) {
        showError('missing-error', e.message || 'Failed to load data');
    } finally {
        loading.classList.add('hidden');
    }
}

function renderMissing() {
    const list = document.getElementById('missing-list');
    const empty = document.getElementById('missing-empty');
    const selectedFriends = getSelectedFriends('missing-friend-filter');

    const movies = filterByFriends(missingData, selectedFriends);

    if (!movies.length) {
        list.innerHTML = '';
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');
    list.innerHTML = movies.map(m => movieListItem(m, false)).join('');
}

// ── Shared helpers ──

function movieListItem(movie, showJellyfinStatus) {
    const link = movie.url
        ? `<a href="${esc(movie.url)}" target="_blank">${esc(movie.name)}</a>`
        : esc(movie.name);
    const year = movie.year ? `<span class="movie-year">(${movie.year})</span>` : '';
    const badges = (movie.wanted_by || [])
        .map(f => `<span class="friend-badge">${esc(f)}</span>`).join('');

    let statusHtml = '';
    if (showJellyfinStatus) {
        statusHtml = movie.on_jellyfin
            ? '<span class="jellyfin-status available">On Jellyfin</span>'
            : '<span class="jellyfin-status unavailable">Not on server</span>';
    }

    return `
        <div class="movie-list-item">
            <div class="movie-info">
                <div class="movie-title">${link} ${year}</div>
                <div class="movie-badges">${badges}</div>
            </div>
            ${statusHtml}
        </div>
    `;
}

function showError(elementId, message) {
    const el = document.getElementById(elementId);
    el.textContent = message;
    el.classList.remove('hidden');
}

function esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ── Cache refresh ──

document.getElementById('refresh-cache').addEventListener('click', async () => {
    const btn = document.getElementById('refresh-cache');
    btn.disabled = true;
    btn.textContent = 'Refreshing...';
    try {
        await fetch('/api/cache/refresh', { method: 'POST' });
        overlapLoaded = false;
        missingLoaded = false;
        overlapData = [];
        missingData = [];
        // Reload current tab's data
        const activeTab = document.querySelector('.tab.active').dataset.tab;
        if (activeTab === 'overlap') loadOverlap();
        if (activeTab === 'missing') loadMissing();
    } catch (e) {
        console.error('Failed to refresh cache:', e);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Refresh Cache';
    }
});
