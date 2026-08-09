/* RescueNet — citizen app.
 *
 * Every number on screen comes from the backend. Nothing here invents data:
 * if the API cannot answer, the UI says so rather than showing a placeholder
 * that looks like a real reading.
 */

import { api, ApiError } from './api.js';
import { identity, newKey, outbox, flushOutbox, filedLocally } from './store.js';
import { location as geo, createMap, userIcon, incidentIcon } from './geo.js';
import {
  $, $$, el, esc, toast, severityBand, severityLabel, titleCase,
  ago, minutesLabel, metres, haversineKm, parseUtc, reasonLabel,
} from './ui.js';

const state = {
  online: navigator.onLine,
  health: null,
  governance: null,
  queue: [],
  nearby: [],
  nearbyFilter: 'all',
  photo: null,
  history: [],
  syncing: false,
};

const TABS = [
  { key: 'home', label: 'Home', icon: '<path d="M3 11L12 4L21 11" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><path d="M5 10V19C5 19.6 5.4 20 6 20H18C18.6 20 19 19.6 19 19V10" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>' },
  { key: 'nearby', label: 'Nearby', icon: '<path d="M12 21C12 21 19 14.5 19 9.5C19 5.9 15.9 3 12 3C8.1 3 5 5.9 5 9.5C5 14.5 12 21 12 21Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><circle cx="12" cy="9.5" r="2.3" stroke="currentColor" stroke-width="1.8"/>' },
  { key: 'sos', label: 'SOS', sos: true },
  { key: 'mine', label: 'My reports', icon: '<rect x="5" y="3" width="14" height="18" rx="2" stroke="currentColor" stroke-width="1.8"/><path d="M8 8H16M8 12H16M8 16H12" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>' },
  { key: 'system', label: 'System', icon: '<circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>' },
];

// --- Navigation ---------------------------------------------------------

function show(key) {
  if (key === 'sos') return openSos();

  $$('.screen').forEach((node) => node.classList.remove('is-active'));
  if (key !== 'home') $(`#screen-${key}`)?.classList.add('is-active');

  $$('.tabbar button').forEach((btn) => {
    btn.classList.toggle('is-active', btn.dataset.tab === key);
  });

  state.history.push(key);
  onEnter(key);
}

function back() {
  state.history.pop();
  const previous = state.history.pop() || 'home';
  show(previous);
}

/** Drop the back-stack and land somewhere fresh.
 *
 * Used once a report is sent: backing out of the confirmation should return to
 * Home, not to the form the citizen has already finished with. */
function resetTo(key) {
  state.history = [];
  show(key);
}

function onEnter(key) {
  if (key === 'home') { refreshHome(); setTimeout(() => maps.home?.invalidateSize(), 80); }
  if (key === 'report') { setTimeout(() => { ensureReportMap(); maps.report?.invalidateSize(); }, 80); }
  if (key === 'nearby') loadNearby();
  if (key === 'mine') loadMine();
  if (key === 'responders') loadResponders();
  if (key === 'system') loadSystem();
}

function renderTabs() {
  const markup = TABS.map((tab) => {
    if (tab.sos) {
      return `<button data-tab="sos" class="sos-tab" aria-label="Send an SOS"><span>SOS</span></button>`;
    }
    return `<button data-tab="${tab.key}">
      <svg width="21" height="21" viewBox="0 0 24 24" fill="none" aria-hidden="true">${tab.icon}</svg>
      <span>${tab.label}</span>
    </button>`;
  }).join('');

  $$('.tabbar').forEach((bar) => {
    bar.innerHTML = markup;
    bar.querySelectorAll('button').forEach((btn) => {
      btn.addEventListener('click', () => show(btn.dataset.tab));
    });
  });
}

// --- Connectivity -------------------------------------------------------

async function renderConnection() {
  const node = $('#connBanner');
  const queued = await outbox.count();

  if (!state.online) {
    node.innerHTML = `<div class="banner banner-offline">
      <span>Offline${queued ? ` — ${queued} report${queued > 1 ? 's' : ''} waiting to send` : ''}</span>
    </div>`;
    return;
  }
  if (queued) {
    node.innerHTML = `<div class="banner banner-sync">
      <span class="grow">${queued} queued report${queued > 1 ? 's' : ''}</span>
      <button class="btn btn-sm btn-ghost" id="syncNow">Sync now</button>
    </div>`;
    $('#syncNow').addEventListener('click', () => syncNow(true));
    return;
  }
  node.innerHTML = '';
}

async function syncNow(manual = false) {
  if (state.syncing || !state.online) return;
  state.syncing = true;
  try {
    const result = await flushOutbox(api);
    if (result.synced) {
      toast(`${result.synced} queued report${result.synced > 1 ? 's' : ''} synced`, 'ok');
      refreshHome();
    } else if (manual && result.attempted) {
      toast('Could not sync yet — will retry', 'err');
    } else if (manual) {
      toast('Nothing waiting to sync');
    }
  } finally {
    state.syncing = false;
    renderConnection();
  }
}

window.addEventListener('online', () => {
  state.online = true;
  renderConnection();
  syncNow();
  refreshHome();
});
window.addEventListener('offline', () => {
  state.online = false;
  renderConnection();
});

// --- Maps ---------------------------------------------------------------

const maps = { home: null, report: null };
const markers = { home: null, report: null, incidents: [] };

function ensureHomeMap() {
  if (maps.home || typeof L === 'undefined') return;
  maps.home = createMap($('#homeMap'), { zoom: 14 });
  markers.home = L.marker([geo.current.lat, geo.current.lng], { icon: userIcon() }).addTo(maps.home);
}

function ensureReportMap() {
  if (maps.report || typeof L === 'undefined' || !$('#reportMap')) return;
  maps.report = createMap($('#reportMap'), { zoom: 16, controls: false });
  markers.report = L.marker([geo.current.lat, geo.current.lng], { icon: userIcon() }).addTo(maps.report);
}

function paintIncidents(reports) {
  if (!maps.home) return;
  markers.incidents.forEach((marker) => maps.home.removeLayer(marker));
  markers.incidents = reports
    .filter((report) => report.lat && report.lng)
    .map((report) => L.marker([report.lat, report.lng], { icon: incidentIcon(report.severity_score) })
      .addTo(maps.home)
      .bindPopup(`<b>${esc(titleCase(report.incident_type) || 'Incident')}</b><br>${esc(report.text.slice(0, 90))}…`));
}

geo.onChange((position) => {
  const point = [position.lat, position.lng];
  markers.home?.setLatLng(point);
  markers.report?.setLatLng(point);

  const line = $('#gpsLine');
  if (line) {
    line.textContent = position.error
      ? position.error
      : `Your location · ±${position.accuracy} m accuracy`;
  }
  const coords = $('#reportCoords');
  if (coords) coords.textContent = `${position.lat.toFixed(5)}, ${position.lng.toFixed(5)}`;
  const accuracy = $('#reportAccuracy');
  if (accuracy) accuracy.textContent = position.error ? 'unavailable' : `±${position.accuracy} m`;
  const note = $('#reportGeoNote');
  if (note) {
    note.textContent = position.isFallback
      ? 'Using an approximate area. Turn on location for an accurate report.'
      : '';
  }
});

// --- Home ---------------------------------------------------------------

async function refreshHome() {
  ensureHomeMap();
  renderConnection();

  try {
    const health = await api.health();
    state.health = health;
    const line = $('#systemStatus');
    line.className = `status-line ${health.status === 'ok' ? 'ok' : 'bad'}`;
    line.innerHTML = `<span class="dot"></span>Dispatch online · ${esc(health.environment)}`;
  } catch {
    const line = $('#systemStatus');
    line.className = 'status-line bad';
    line.innerHTML = '<span class="dot"></span>Cannot reach dispatch — reports will be queued';
  }

  try {
    const [queue, responders] = await Promise.all([
      api.queue({ limit: 50 }),
      api.responders({ limit: 100 }),
    ]);
    state.queue = queue.items;

    const ready = responders.items.filter((r) => r.dispatchable).length;
    const worst = queue.items[0];

    $('#statGrid').innerHTML = `
      <button class="stat" data-go="nearby"><b>${queue.total}</b><span>IN QUEUE</span></button>
      <button class="stat" data-go="responders"><b>${ready}/${responders.total}</b><span>CREWS FREE</span></button>
      <button class="stat" data-go="nearby"><b>${worst ? worst.priority.severity : '—'}</b><span>TOP SEVERITY</span></button>`;
    $$('#statGrid .stat').forEach((node) => {
      node.addEventListener('click', () => show(node.dataset.go));
    });

    $('#homeQueue').innerHTML = queue.items.slice(0, 3).map(incidentRow).join('')
      || '<div class="empty">Nothing waiting. The queue is clear.</div>';
    bindIncidentRows($('#homeQueue'));

    paintIncidents(queue.items);
  } catch (err) {
    $('#statGrid').innerHTML = '';
    $('#homeQueue').innerHTML = `<div class="empty">${esc(err.message)}</div>`;
  }
}

function incidentRow(report, index) {
  const band = severityBand(report.priority ? report.priority.severity : report.severity_score);
  const severity = report.priority ? report.priority.severity : report.severity_score;
  const waited = report.priority ? report.priority.minutes_waiting : null;
  return `<button class="incident sev-${band}" data-id="${report.id}">
    <div class="sev-bar"></div>
    <div class="incident-body">
      ${report.position ? `<span class="rank">${report.position}</span>` : ''}
      <span class="grow">
        <span class="incident-text">${esc(report.text)}</span>
        <span class="incident-meta">
          <span class="pill sev-chip sev-${band}">${severityLabel(severity)} · ${severity ?? '—'}</span>
          <span class="pill status-chip status-${esc(report.status)}">${esc(titleCase(report.status))}</span>
          ${report.incident_type ? `<span class="tiny dim">${esc(titleCase(report.incident_type))}</span>` : ''}
          ${waited !== null ? `<span class="tiny dim">waiting ${minutesLabel(waited)}</span>` : ''}
        </span>
      </span>
    </div>
  </button>`;
}

function bindIncidentRows(root) {
  root.querySelectorAll('.incident').forEach((node) => {
    node.addEventListener('click', () => openDetail(node.dataset.id));
  });
}

// --- Nearby -------------------------------------------------------------

const NEARBY_FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'critical', label: 'Critical' },
  { key: 'high', label: 'High' },
  { key: 'active', label: 'Being handled' },
];

function renderNearbyFilters() {
  $('#nearbyFilters').innerHTML = NEARBY_FILTERS.map((f) =>
    `<button class="chip ${state.nearbyFilter === f.key ? 'is-active' : ''}" data-filter="${f.key}">${f.label}</button>`
  ).join('');
  $$('#nearbyFilters .chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      state.nearbyFilter = chip.dataset.filter;
      renderNearbyFilters();
      renderNearby();
    });
  });
}

async function loadNearby() {
  renderNearbyFilters();
  const list = $('#nearbyList');
  list.innerHTML = '<div class="stack"><div class="skeleton"></div><div class="skeleton"></div></div>';
  try {
    const { items } = await api.listReports({ limit: 100 });
    const here = geo.current;
    state.nearby = items
      .map((report) => ({ ...report, distanceKm: haversineKm(here, { lat: report.lat, lng: report.lng }) }))
      .sort((a, b) => a.distanceKm - b.distanceKm);
    renderNearby();
  } catch (err) {
    list.innerHTML = `<div class="empty">${esc(err.message)}<br><br>Nearby incidents need a connection.</div>`;
  }
}

function renderNearby() {
  const active = ['assigned', 'acknowledged', 'en_route', 'on_scene'];
  const filtered = state.nearby.filter((report) => {
    if (state.nearbyFilter === 'all') return true;
    if (state.nearbyFilter === 'active') return active.includes(report.status);
    return severityBand(report.severity_score) === state.nearbyFilter;
  });

  $('#nearbyList').innerHTML = filtered.length
    ? `<div class="stack">${filtered.slice(0, 40).map((report) => {
        const band = severityBand(report.severity_score);
        return `<button class="incident sev-${band}" data-id="${report.id}">
          <div class="sev-bar"></div>
          <div class="incident-body">
            <span class="grow">
              <span class="incident-text">${esc(report.text)}</span>
              <span class="incident-meta">
                <span class="pill sev-chip sev-${band}">${severityLabel(report.severity_score)}</span>
                <span class="pill status-chip status-${esc(report.status)}">${esc(titleCase(report.status))}</span>
                <span class="tiny dim">${metres(report.distanceKm)} away · ${ago(report.client_created_at)}</span>
              </span>
            </span>
          </div>
        </button>`;
      }).join('')}</div>`
    : '<div class="empty">No incidents match this filter.</div>';

  bindIncidentRows($('#nearbyList'));
}

// --- My reports ---------------------------------------------------------

async function loadMine() {
  const list = $('#mineList');
  const queued = await outbox.all();

  let mine = [];
  let error = null;
  try {
    const { items } = await api.listReports({ reporter_pseudonym: identity.pseudonym, limit: 50 });
    mine = items;
  } catch (err) {
    error = err;
    // Fall back to what this device remembers filing, so the screen is still
    // useful with no connection.
    mine = filedLocally.all().map((entry) => ({ ...entry, offlineRecord: true }));
  }

  const queuedMarkup = queued.length ? `
    <div class="section-title">Waiting to send (${queued.length})</div>
    <div class="stack">${queued.map((item) => `
      <div class="card">
        <div class="incident-text">${esc(item.text)}</div>
        <div class="incident-meta">
          <span class="pill status-chip">Queued on this device</span>
          <span class="tiny dim">filed ${ago(item.clientCreatedAt)}${item.image ? ' · with photo' : ''}</span>
        </div>
        ${item.lastError ? `<p class="tiny dim" style="margin-top:6px">Last attempt: ${esc(item.lastError)}</p>` : ''}
      </div>`).join('')}</div>` : '';

  const sentMarkup = mine.length ? `
    <div class="section-title">Sent</div>
    <div class="stack">${mine.map((report) => report.offlineRecord
      ? `<div class="card"><div class="incident-text">${esc(report.text)}</div>
           <div class="incident-meta"><span class="pill status-chip">Sent · details need a connection</span>
           <span class="tiny dim">${ago(report.clientCreatedAt)}</span></div></div>`
      : incidentRow(report)).join('')}</div>` : '';

  list.innerHTML = (queuedMarkup + sentMarkup) || `<div class="empty">
    You have not filed anything yet.<br><br>Reports you send appear here with their severity score and progress.
  </div>`;

  if (error && !queued.length && !mine.length) {
    list.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  }
  bindIncidentRows(list);
}

// --- Report detail ------------------------------------------------------

const LIFECYCLE = ['received', 'classified', 'verified', 'queued', 'assigned', 'acknowledged', 'en_route', 'on_scene', 'resolved', 'closed'];

async function openDetail(id) {
  show('detail');
  const body = $('#detailBody');
  body.innerHTML = '<div class="stack"><div class="skeleton"></div><div class="skeleton"></div></div>';

  let report;
  try {
    report = await api.getReport(id);
  } catch (err) {
    body.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    return;
  }

  const band = severityBand(report.severity_score);
  const flagged = report.status === 'flagged';
  const rejected = report.status === 'rejected';

  body.innerHTML = `
    <div class="detail-head sev-${band}">
      <div class="incident-meta" style="margin-top:0">
        <span class="pill sev-chip sev-${band}">${severityLabel(report.severity_score)}</span>
        <span class="pill status-chip status-${esc(report.status)}">${esc(titleCase(report.status))}</span>
        ${report.incident_type ? `<span class="pill status-chip">${esc(titleCase(report.incident_type))}</span>` : ''}
      </div>
      <p class="detail-text">${esc(report.text)}</p>
      <p class="tiny dim" style="margin-top:8px">Filed ${ago(report.client_created_at)} · ${esc(report.reporter_pseudonym)}</p>
    </div>

    ${flagged ? `<div class="card" style="margin-top:14px;border-color:var(--danger)">
      <b style="color:#ff8a75">Held for human review</b>
      <p class="tiny muted" style="margin-top:5px;line-height:1.5">Automated checks could not confirm this report, so a person will look at it. It has not been rejected — nothing is ever discarded automatically.</p>
    </div>` : ''}
    ${rejected ? `<div class="card" style="margin-top:14px">
      <b>Closed after review</b>
      <p class="tiny muted" style="margin-top:5px">An operator reviewed this report and closed it.</p>
    </div>` : ''}

    ${scoreBlock('Severity', report.severity_score, report.severity_reasons, band)}
    ${report.authenticity_score !== null ? scoreBlock('Trust', report.authenticity_score, report.authenticity_reasons, 'low') : ''}

    <div class="section-title">Progress</div>
    <div class="timeline">${timeline(report.status)}</div>

    ${report.image ? `<div class="section-title">Photograph</div>
      <div class="card"><p class="tiny muted">Perceptual hash <code>${esc(report.image.phash)}</code></p>
      ${report.image.exif ? `<p class="tiny dim" style="margin-top:6px">EXIF ${report.image.exif.has_gps ? 'GPS present' : 'has no GPS'}${report.image.exif.camera ? ` · ${esc(report.image.exif.camera)}` : ''}</p>` : '<p class="tiny dim" style="margin-top:6px">No EXIF metadata.</p>'}</div>` : ''}

    <div class="section-title">Scored by</div>
    <div class="card"><div class="kv"><span>Provider</span><span>${esc(report.scoring_provider || 'pending')}</span></div>
    <div class="kv"><span>Report id</span><span class="tiny">${esc(report.id.slice(0, 8))}…</span></div></div>`;
}

function scoreBlock(title, score, reasons, band) {
  const total = (reasons || []).reduce((sum, r) => sum + r.weight, 0);
  return `<div class="score-block sev-${band}">
    <div class="section-title">${title}</div>
    <div class="card">
      <div class="score-head"><span class="score-value">${score ?? '—'}</span><span class="score-max">/ 100</span></div>
      <div style="margin-top:12px">
        ${(reasons || []).map((r) => `<div class="reason">
          <span class="reason-code"><b>${esc(reasonLabel(r.code))}</b><span class="tiny dim">from the ${esc(r.source)}</span></span>
          <span class="reason-weight ${r.weight >= 0 ? 'plus' : 'minus'}">${r.weight >= 0 ? '+' : ''}${r.weight}</span>
        </div>`).join('') || '<p class="tiny dim">Not scored yet.</p>'}
        ${reasons && reasons.length ? `<div class="reason-total"><span>Total</span><span>${total}</span></div>` : ''}
      </div>
    </div>
  </div>`;
}

function timeline(status) {
  if (status === 'flagged' || status === 'rejected') {
    return `<div class="tl-step done">Received</div>
      <div class="tl-step done">Scored</div>
      <div class="tl-step current">${status === 'flagged' ? 'Awaiting human review' : 'Closed after review'}</div>`;
  }
  const index = LIFECYCLE.indexOf(status);
  return LIFECYCLE.slice(0, 8).map((step, i) => {
    const cls = i < index ? 'done' : i === index ? 'current' : '';
    return `<div class="tl-step ${cls}">${titleCase(step)}</div>`;
  }).join('');
}

// --- Responders ---------------------------------------------------------

async function loadResponders() {
  const list = $('#respondersList');
  list.innerHTML = '<div class="stack"><div class="skeleton"></div><div class="skeleton"></div></div>';
  try {
    const { items } = await api.responders({ limit: 100 });
    const here = geo.current;
    const sorted = items
      .map((r) => ({ ...r, distanceKm: haversineKm(here, { lat: r.lat, lng: r.lng }) }))
      .sort((a, b) => a.distanceKm - b.distanceKm);

    list.innerHTML = `<div class="stack">${sorted.map((r) => {
      const pct = r.capacity ? Math.round((r.active_count / r.capacity) * 100) : 0;
      return `<div class="responder">
        <div class="responder-badge">${skillIcon(r.skill)}</div>
        <div class="grow">
          <div class="spread"><b>${esc(r.name)}</b>
            <span class="pill ${r.dispatchable ? 'status-resolved' : 'status-flagged'}">${r.dispatchable ? 'Available' : titleCase(r.status)}</span>
          </div>
          <div class="tiny dim" style="margin-top:3px">${esc(titleCase(r.skill))} · ${metres(r.distanceKm)} away · ${r.active_count}/${r.capacity} assigned</div>
          <div class="load-track"><div class="load-fill ${pct >= 100 ? 'full' : ''}" style="width:${pct}%"></div></div>
        </div>
      </div>`;
    }).join('')}</div>`;
  } catch (err) {
    list.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
  }
}

function skillIcon(skill) {
  const icons = {
    medical: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M12 6v12M6 12h12" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
    rescue: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M12 2L19 5V11C19 16 16 19.5 12 21C8 19.5 5 16 5 11V5L12 2Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>',
    structural: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M4 20V9L12 4L20 9V20" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M9 20V14H15V20" stroke="currentColor" stroke-width="1.8"/></svg>',
  };
  return icons[skill] || icons.rescue;
}

// --- System / governance -------------------------------------------------

async function loadSystem() {
  const body = $('#systemBody');
  body.innerHTML = '<div class="stack"><div class="skeleton"></div><div class="skeleton"></div></div>';

  const queued = await outbox.all();
  let governance = null;
  try {
    governance = await api.governance();
  } catch { /* rendered as unavailable below */ }

  body.innerHTML = `
    <div class="section-title">How your report is scored</div>
    ${governance ? `<div class="honest">${esc(governance.scoring.honest_summary)}</div>` : `
      <div class="card"><p class="muted tiny">Cannot reach the server, so the scoring provenance cannot be shown right now.</p></div>`}

    ${governance ? `<div class="section-title">Thresholds in use</div>
      <div class="card">
        <div class="kv"><span>Flag for review below</span><span>${governance.thresholds.authenticity_flag_threshold} / 100</span></div>
        <div class="kv"><span>Duplicate image distance</span><span>${governance.thresholds.phash_duplicate_distance}</span></div>
        <div class="kv"><span>Responder search radius</span><span>${governance.thresholds.dispatch_max_radius_km} km</span></div>
        <div class="kv"><span>Corroboration window</span><span>${governance.thresholds.corroboration_radius_m} m / ${governance.thresholds.corroboration_window_min} min</span></div>
      </div>

      <div class="section-title">Human decisions on record</div>
      <div class="card">
        <div class="kv"><span>Queue overrides</span><span>${governance.human_in_the_loop.priority_overrides}</span></div>
        <div class="kv"><span>Reviews by a person</span><span>${governance.human_in_the_loop.reports_verified_by_human + governance.human_in_the_loop.reports_rejected_by_human}</span></div>
        <div class="kv"><span>Rejected automatically</span><span>${governance.data.auto_rejected_reports} — never</span></div>
      </div>` : ''}

    <div class="section-title">This device</div>
    <div class="card">
      <div class="kv"><span>Your pseudonym</span><span>${esc(identity.pseudonym)}</span></div>
      <div class="kv"><span>Connection</span><span>${state.online ? 'Online' : 'Offline'}</span></div>
      <div class="kv"><span>Waiting to send</span><span>${queued.length}</span></div>
      <div class="kv"><span>Backend</span><span class="tiny">${esc(api.base)}</span></div>
    </div>
    ${queued.length ? `<div class="card" style="margin-top:12px">
      ${queued.map((item) => `<div class="outbox-item">
        <span class="grow"><b class="tiny">${esc(item.text.slice(0, 60))}…</b>
        <span class="tiny dim" style="display:block">queued ${ago(item.queuedAt)} · ${item.attempts || 0} attempt(s)</span></span>
      </div>`).join('')}
    </div>` : ''}

    <button class="btn btn-ghost" id="changeApi" style="margin-top:18px;width:100%">Change backend address</button>
    <p class="tiny dim center-text" style="margin-top:14px;line-height:1.5">
      Reports are pseudonymous. No name, phone number or email is collected (NFR-7).
    </p>`;

  $('#changeApi').addEventListener('click', () => {
    const next = prompt('Backend base URL', api.base);
    if (next) { api.setBase(next.trim()); toast('Backend updated'); loadSystem(); }
  });
}

// --- Report submission ---------------------------------------------------

function setPhoto(file) {
  if (!file) return;
  state.photo = file;
  const url = URL.createObjectURL(file);
  $('#photoPreview').innerHTML = `<div class="preview">
    <img src="${url}" alt="Attached photograph">
    <button class="preview-drop" id="dropPhoto" aria-label="Remove photograph">×</button>
    <div class="preview-note">Sent with EXIF intact — it helps verify where and when this was taken.</div>
  </div>`;
  $('#dropPhoto').addEventListener('click', () => {
    state.photo = null;
    URL.revokeObjectURL(url);
    $('#photoPreview').innerHTML = '';
  });
}

async function submitReport({ text, urgent = false } = {}) {
  const body = (text ?? $('#reportText').value).trim();
  if (!body) {
    toast('Describe what is happening first', 'err');
    $('#reportText')?.focus();
    return null;
  }

  const entry = {
    idempotencyKey: newKey(),
    text: body,
    lat: geo.current.lat,
    lng: geo.current.lng,
    clientCreatedAt: new Date().toISOString(),
    pseudonym: identity.pseudonym,
    image: urgent ? null : state.photo,
  };

  if (!state.online) {
    await outbox.add(entry);
    renderConnection();
    toast('Saved on your device — it will send when you are back online', 'ok');
    return { queued: true };
  }

  try {
    const created = await api.createReport({
      text: entry.text,
      lat: entry.lat,
      lng: entry.lng,
      clientCreatedAt: entry.clientCreatedAt,
      pseudonym: entry.pseudonym,
      idempotencyKey: entry.idempotencyKey,
      image: entry.image,
    });
    filedLocally.add({ ...entry, image: undefined, id: created.id });
    return created;
  } catch (err) {
    if (err instanceof ApiError && err.isOffline) {
      // The browser thought it was online but the server is unreachable. The
      // report is still the citizen's — queue it rather than lose it.
      await outbox.add(entry);
      renderConnection();
      toast('Server unreachable — saved and will retry', 'ok');
      return { queued: true };
    }
    toast(err.message, 'err');
    return null;
  }
}

// --- SOS -----------------------------------------------------------------

const SOS_SECONDS = 5;
const RING = 2 * Math.PI * 120;
let sosTimer = null;

function openSos() {
  $('#screen-sos').classList.add('is-active');
  let remaining = SOS_SECONDS;

  const ring = $('#sosRing');
  ring.style.transition = 'none';
  ring.style.strokeDashoffset = 0;
  void ring.offsetWidth;
  ring.style.transition = 'stroke-dashoffset 1s linear';

  $('#sosNumber').textContent = remaining;
  $('#sosLabel').textContent = 'Sending in…';
  $('#sosExplain').textContent = 'A maximum-urgency report will be filed at your current location.';

  clearInterval(sosTimer);
  sosTimer = setInterval(() => {
    remaining -= 1;
    ring.style.strokeDashoffset = Math.min(RING * ((SOS_SECONDS - remaining) / SOS_SECONDS), RING);
    if (remaining <= 0) {
      clearInterval(sosTimer);
      fireSos();
    } else {
      $('#sosNumber').textContent = remaining;
    }
  }, 1000);
}

function closeSos() {
  clearInterval(sosTimer);
  $('#screen-sos').classList.remove('is-active');
}

async function fireSos() {
  $('#sosNumber').textContent = '…';
  $('#sosLabel').textContent = 'Sending';

  const result = await submitReport({
    urgent: true,
    text: 'SOS: immediate assistance needed at this location. The reporter triggered an emergency alert and may be unable to give details.',
  });

  if (!result) {
    $('#sosNumber').textContent = '!';
    $('#sosLabel').textContent = 'Could not send';
    return;
  }

  $('#sosNumber').textContent = '✓';
  $('#sosLabel').textContent = result.queued ? 'Saved offline' : 'Sent';
  $('#sosExplain').textContent = result.queued
    ? 'You are offline. The alert is stored and will send the moment you reconnect.'
    : 'Your alert is in the dispatch queue, ranked by severity.';

  setTimeout(() => {
    closeSos();
    if (result.queued || !result.id) {
      resetTo('mine');
    } else {
      resetTo('home');
      openDetail(result.id);
    }
  }, 1400);
}

// --- Wiring --------------------------------------------------------------

function bind() {
  renderTabs();

  $$('[data-back]').forEach((btn) => btn.addEventListener('click', back));
  $('#reportCta').addEventListener('click', () => show('report'));
  $('#recenterBtn').addEventListener('click', () => {
    maps.home?.setView([geo.current.lat, geo.current.lng], Math.max(maps.home.getZoom(), 15));
  });
  $('#identityBtn').addEventListener('click', () => show('system'));
  $('#nearbyRefresh').addEventListener('click', loadNearby);
  $('#mineRefresh').addEventListener('click', loadMine);
  $('#respondersRefresh').addEventListener('click', loadResponders);

  const text = $('#reportText');
  text.addEventListener('input', () => {
    $('#reportCount').textContent = `${text.value.length}/5000`;
  });

  $('#cameraInput').addEventListener('change', (e) => setPhoto(e.target.files[0]));
  $('#galleryInput').addEventListener('change', (e) => setPhoto(e.target.files[0]));

  $('#submitReport').addEventListener('click', async (e) => {
    const button = e.currentTarget;
    button.disabled = true;
    button.textContent = 'Sending…';
    const result = await submitReport();
    button.disabled = false;
    button.textContent = 'Submit report';

    if (result) {
      text.value = '';
      $('#reportCount').textContent = '0/5000';
      state.photo = null;
      $('#photoPreview').innerHTML = '';
      if (result.queued) {
        resetTo('mine');
      } else {
        toast('Report received', 'ok');
        resetTo('home');
        openDetail(result.id);
      }
    }
  });

  $('#sosCancel').addEventListener('click', closeSos);
  $('#sosSendNow').addEventListener('click', () => { clearInterval(sosTimer); fireSos(); });

  $('#submitPseudonym').textContent = identity.pseudonym;
  $('#identityBtn').textContent = identity.pseudonym.slice(0, 1).toUpperCase();
}

async function boot() {
  bind();
  geo.start();
  state.history = ['home'];
  await refreshHome();
  syncNow();

  // Retry the outbox periodically: `online` does not fire when the network is
  // technically up but the backend is down.
  setInterval(() => { if (state.online) syncNow(); }, 60000);

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => { /* offline shell is a bonus */ });
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
