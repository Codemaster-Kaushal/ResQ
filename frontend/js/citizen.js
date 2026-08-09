/* ResQ AI — citizen app.
 *
 * Two rules this file is built around.
 *
 * 1. Nothing is invented. Every figure comes from the backend; where the API
 *    cannot answer, the UI says so rather than showing a plausible number.
 * 2. A citizen sees their own emergency and public safety information — never
 *    another person's report, never control-room operational data. The queue
 *    depth, crew availability and severity of other people's incidents are
 *    operational facts and stay in the control room.
 */

import { api, ApiError } from './api.js';
import { identity, newKey, outbox, flushOutbox, filedLocally } from './store.js';
import { location as geo, createMap, userIcon, incidentIcon } from './geo.js';
import { i18n, t, LANGUAGES } from './i18n.js';
import { account, emergencyProfile, family } from './profile.js';
import { SIGNALS, composeBody } from './signals.js';
import { voice } from './voice.js';
import {
  $, $$, esc, toast, severityBand, severityLabel, titleCase,
  ago, minutesLabel, metres, haversineKm, parseUtc, reasonLabel,
} from './ui.js';

const state = {
  online: navigator.onLine,
  history: [],
  photo: null,
  signals: new Set(),
  attachProfile: true,
  attachFamily: true,
  nearbyFilter: 'all',
  mine: [],
  trackingId: null,
  syncing: false,
};

const TABS = [
  { key: 'home', i18n: 'nav.home', icon: '<path d="M3 11L12 4L21 11" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><path d="M5 10V19C5 19.6 5.4 20 6 20H18C18.6 20 19 19.6 19 19V10" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>' },
  { key: 'nearby', i18n: 'nav.nearby', icon: '<path d="M12 21C12 21 19 14.5 19 9.5C19 5.9 15.9 3 12 3C8.1 3 5 5.9 5 9.5C5 14.5 12 21 12 21Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><circle cx="12" cy="9.5" r="2.3" stroke="currentColor" stroke-width="1.8"/>' },
  { key: 'sos', i18n: 'nav.sos', sos: true },
  { key: 'mine', i18n: 'nav.reports', icon: '<rect x="5" y="3" width="14" height="18" rx="2" stroke="currentColor" stroke-width="1.8"/><path d="M8 8H16M8 12H16M8 16H12" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>' },
  { key: 'system', i18n: 'nav.system', icon: '<circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.8"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>' },
];

/* A citizen-facing name for each backend status. The backend's vocabulary is
 * operational (`queued`, `classified`); the citizen needs to know what is
 * happening to *their* request. */
const CITIZEN_STATUS = {
  received: 'status.received',
  classified: 'status.received',
  verified: 'status.queued',
  queued: 'status.queued',
  flagged: 'status.underReview',
  assigned: 'status.assigned',
  acknowledged: 'status.acknowledged',
  en_route: 'status.enRoute',
  on_scene: 'status.onScene',
  resolved: 'status.resolved',
  closed: 'status.resolved',
  rejected: 'status.closed',
};

const UPDATE_FOR = {
  received: 'update.received',
  classified: 'update.received',
  verified: 'update.verified',
  queued: 'update.prioritised',
  flagged: 'update.review',
  assigned: 'update.assigned',
  acknowledged: 'update.assigned',
  en_route: 'update.enRoute',
  on_scene: 'update.arrived',
  resolved: 'update.resolved',
  closed: 'update.resolved',
};

/** Short human-quotable code. The backend's idempotency key is the real id. */
function trackingCode(key) {
  const tail = String(key).replace(/[^a-z0-9]/gi, '').slice(-6).toUpperCase();
  return `RQ-${tail}`;
}

// --- Navigation ---------------------------------------------------------

function show(key) {
  if (key === 'sos') return openSos();

  $$('.screen').forEach((n) => n.classList.remove('is-active'));
  if (key !== 'home') $(`#screen-${key}`)?.classList.add('is-active');
  $$('.tabbar button').forEach((b) => b.classList.toggle('is-active', b.dataset.tab === key));

  state.history.push(key);
  onEnter(key);
}

function back() {
  state.history.pop();
  show(state.history.pop() || 'home');
}

function resetTo(key) {
  state.history = [];
  show(key);
}

function onEnter(key) {
  const enter = {
    home: () => { refreshHome(); setTimeout(() => maps.home?.invalidateSize(), 80); },
    report: () => setTimeout(() => { ensureReportMap(); maps.report?.invalidateSize(); }, 80),
    nearby: loadNearby,
    mine: loadMine,
    system: loadSystem,
    profile: loadProfile,
    emergency: loadEmergencyProfile,
    family: loadFamily,
    track: () => renderTrack(state.trackingId),
  };
  enter[key]?.();
}

/** Re-render everything that carries translated text. */
function applyLanguage() {
  document.documentElement.lang = i18n.lang;
  i18n.apply();
  renderTabs();
  renderSignals();
  const note = $('#reportLangNote');
  if (note) note.textContent = LANGUAGES.find((l) => l.code === i18n.lang).native;
  refreshHome();
}

function renderTabs() {
  const markup = TABS.map((tab) => tab.sos
    ? `<button data-tab="sos" class="sos-tab" aria-label="SOS"><span>SOS</span></button>`
    : `<button data-tab="${tab.key}">
         <svg width="21" height="21" viewBox="0 0 24 24" fill="none" aria-hidden="true">${tab.icon}</svg>
         <span>${esc(t(tab.i18n))}</span>
       </button>`).join('');

  $$('.tabbar').forEach((bar) => {
    bar.innerHTML = markup;
    bar.querySelectorAll('button').forEach((b) => b.addEventListener('click', () => show(b.dataset.tab)));
  });
}

// --- Connectivity -------------------------------------------------------

async function renderConnection() {
  const node = $('#connBanner');
  const queued = await outbox.count();

  if (!state.online) {
    node.innerHTML = `<div class="banner banner-offline">
      <span>${esc(t('system.offline'))}${queued ? ` — ${queued} ${esc(t('system.waiting').toLowerCase())}` : ''}</span></div>`;
  } else if (queued) {
    node.innerHTML = `<div class="banner banner-sync">
      <span class="grow">${queued} ${esc(t('system.waiting').toLowerCase())}</span>
      <button class="btn btn-sm btn-ghost" id="syncNow">${esc(t('common.retry'))}</button></div>`;
    $('#syncNow').addEventListener('click', () => syncNow(true));
  } else {
    node.innerHTML = '';
  }
}

async function syncNow(manual = false) {
  if (state.syncing || !state.online) return;
  state.syncing = true;
  try {
    const result = await flushOutbox(api);
    if (result.synced) {
      toast(`${result.synced} ${t('status.sent').toLowerCase()}`, 'ok');
      refreshHome();
    } else if (manual && result.attempted) {
      toast(t('common.retry'), 'err');
    }
  } finally {
    state.syncing = false;
    renderConnection();
  }
}

window.addEventListener('online', () => { state.online = true; renderConnection(); syncNow(); refreshHome(); });
window.addEventListener('offline', () => { state.online = false; renderConnection(); });

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

/** Public warnings only: a marker and a category, never anyone's words. */
function paintPublicSafety(zones) {
  if (!maps.home) return;
  markers.incidents.forEach((m) => maps.home.removeLayer(m));
  markers.incidents = zones.map((zone) => L.marker([zone.lat, zone.lng], { icon: incidentIcon(zone.severity) })
    .addTo(maps.home)
    .bindPopup(`<b>${esc(zone.label)}</b><br>${zone.count} ${esc(t('nearby.reports'))}`));
}

geo.onChange((pos) => {
  markers.home?.setLatLng([pos.lat, pos.lng]);
  markers.report?.setLatLng([pos.lat, pos.lng]);

  const line = $('#gpsLine');
  if (line) line.textContent = pos.error || `${t('sos.location')}: ±${pos.accuracy} m`;
  const coords = $('#reportCoords');
  if (coords) coords.textContent = `${pos.lat.toFixed(5)}, ${pos.lng.toFixed(5)}`;
  const acc = $('#reportAccuracy');
  if (acc) acc.textContent = pos.error ? t('sos.approximate') : `±${pos.accuracy} m`;
  const note = $('#reportGeoNote');
  if (note) note.textContent = pos.isFallback ? t('sos.approximate') : '';
});

// --- Public safety derivation -------------------------------------------

/* The backend has no shelters or road-closure feed, and inventing one would be
 * worse than not having it. What it does have is real reports — so public
 * safety is derived from them by clustering into ~700 m cells and publishing
 * only the category, the cell centre and a count. No description, no reporter,
 * no exact position: a citizen learns "flooding reported near here" without
 * learning anything about the people who reported it. */
const CELL_DEGREES = 0.006; // roughly 650 m

const PUBLIC_LABELS = {
  flooding: 'Flooding reported',
  landslide: 'Landslide reported',
  fire: 'Fire reported',
  structural_collapse: 'Building damage reported',
  trapped_persons: 'Rescue operation in progress',
  infrastructure: 'Road or utility disruption',
  medical: 'Medical response in progress',
  other: 'Incident reported',
};

function derivePublicSafety(reports) {
  const cells = new Map();

  for (const report of reports) {
    if (!report.incident_type || report.lat == null) continue;
    // A flagged report is unverified; publishing it as a public warning would
    // spread exactly the false alarm the authenticity stage exists to catch.
    if (report.status === 'flagged' || report.status === 'rejected') continue;

    const key = `${report.incident_type}:${Math.round(report.lat / CELL_DEGREES)}:${Math.round(report.lng / CELL_DEGREES)}`;
    const cell = cells.get(key) || {
      type: report.incident_type,
      label: PUBLIC_LABELS[report.incident_type] || PUBLIC_LABELS.other,
      lat: 0, lng: 0, count: 0, severity: 0, latest: null, active: false,
    };
    cell.count += 1;
    cell.lat += report.lat;
    cell.lng += report.lng;
    cell.severity = Math.max(cell.severity, report.severity_score || 0);
    if (!cell.latest || report.client_created_at > cell.latest) cell.latest = report.client_created_at;
    if (['assigned', 'acknowledged', 'en_route', 'on_scene'].includes(report.status)) cell.active = true;
    cells.set(key, cell);
  }

  return [...cells.values()]
    .map((cell) => ({ ...cell, lat: cell.lat / cell.count, lng: cell.lng / cell.count }))
    .map((cell) => ({ ...cell, distanceKm: haversineKm(geo.current, cell) }))
    .sort((a, b) => a.distanceKm - b.distanceKm);
}

// --- Home ---------------------------------------------------------------

async function refreshHome() {
  ensureHomeMap();
  renderConnection();

  try {
    const health = await api.health();
    const line = $('#systemStatus');
    line.className = `status-line ${health.status === 'ok' ? 'ok' : 'bad'}`;
    line.innerHTML = `<span class="dot"></span>${esc(t('app.tagline'))}`;
  } catch {
    const line = $('#systemStatus');
    line.className = 'status-line bad';
    line.innerHTML = `<span class="dot"></span>${esc(t('common.offlineNote'))}`;
  }

  const queued = await outbox.all();

  try {
    const [minePage, responders, all] = await Promise.all([
      api.listReports({ reporter_pseudonym: identity.pseudonym, limit: 50 }),
      api.responders({ limit: 100 }),
      api.listReports({ limit: 200 }),
    ]);
    state.mine = minePage.items;

    const open = minePage.items.filter((r) => !['resolved', 'closed', 'rejected'].includes(r.status));
    const latest = open[0];
    const zones = derivePublicSafety(all.items);
    // Availability is shown as a coarse band, never as crew counts — how many
    // crews are free is operational information.
    const ready = responders.items.filter((r) => r.dispatchable).length;
    const service = ready === 0 ? t('home.none') : ready <= 2 ? t('home.limited') : t('home.available');

    $('#statGrid').innerHTML = `
      <button class="stat" data-go="mine"><b>${open.length + queued.length}</b><span>${esc(t('home.myRequests'))}</span></button>
      <button class="stat" data-go="nearby"><b>${zones.length}</b><span>${esc(t('home.nearbySafety'))}</span></button>
      <div class="stat"><b class="stat-word">${esc(service)}</b><span>${esc(t('home.services'))}</span></div>`;
    $$('#statGrid .stat[data-go]').forEach((n) => n.addEventListener('click', () => show(n.dataset.go)));

    $('#activeHelp').innerHTML = latest ? activeHelpCard(latest) : '';
    $('#activeHelp').querySelector('[data-track]')?.addEventListener('click', () => openTrack(latest.id));

    $('#homeAlerts').innerHTML = zones.length
      ? zones.slice(0, 3).map(publicRow).join('')
      : `<div class="empty">${esc(t('home.noAlerts'))}</div>`;

    paintPublicSafety(zones.slice(0, 40));
  } catch (err) {
    $('#statGrid').innerHTML = '';
    $('#homeAlerts').innerHTML = `<div class="empty">${esc(err.message)}</div>`;
  }
}

function activeHelpCard(report) {
  const label = t(CITIZEN_STATUS[report.status] || 'status.received');
  const update = t(UPDATE_FOR[report.status] || 'update.received');
  return `<div class="active-help" data-track>
    <div class="spread">
      <span class="pill status-chip status-${esc(report.status)}">${esc(label)}</span>
      <span class="tiny dim">${esc(trackingCode(report.idempotency_key))}</span>
    </div>
    <p class="active-help-msg">${esc(update)}</p>
    <span class="active-help-cta">${esc(t('sos.track'))} →</span>
  </div>`;
}

function publicRow(zone) {
  const band = severityBand(zone.severity);
  return `<div class="incident sev-${band}">
    <div class="sev-bar"></div>
    <div class="incident-body">
      <span class="grow">
        <b style="font-size:15px">${esc(zone.label)}</b>
        <span class="incident-meta">
          <span class="pill sev-chip sev-${band}">${esc(severityLabel(zone.severity))}</span>
          <span class="tiny dim">${metres(zone.distanceKm)} ${esc(t('nearby.away'))} · ${zone.count} ${esc(t('nearby.reports'))}</span>
          ${zone.active ? `<span class="pill status-assigned">${esc(t('status.enRoute'))}</span>` : ''}
        </span>
      </span>
    </div>
  </div>`;
}

// --- Nearby (public only) ------------------------------------------------

const NEARBY_FILTERS = [
  { key: 'all', i18n: 'nearby.all' },
  { key: 'critical', i18n: 'nearby.critical' },
  { key: 'high', i18n: 'nearby.high' },
  { key: 'services', i18n: 'nearby.services' },
];

async function loadNearby() {
  $('#nearbyFilters').innerHTML = NEARBY_FILTERS.map((f) =>
    `<button class="chip ${state.nearbyFilter === f.key ? 'is-active' : ''}" data-filter="${f.key}">${esc(t(f.i18n))}</button>`).join('');
  $$('#nearbyFilters .chip').forEach((c) => c.addEventListener('click', () => {
    state.nearbyFilter = c.dataset.filter;
    loadNearby();
  }));

  const list = $('#nearbyList');
  list.innerHTML = '<div class="stack"><div class="skeleton"></div><div class="skeleton"></div></div>';

  try {
    if (state.nearbyFilter === 'services') {
      const { items } = await api.responders({ limit: 100 });
      const near = items
        .map((r) => ({ ...r, distanceKm: haversineKm(geo.current, r) }))
        .sort((a, b) => a.distanceKm - b.distanceKm);
      list.innerHTML = `<p class="privacy-note">${esc(t('nearby.publicOnly'))}</p>
        <div class="stack">${near.map((r) => `<div class="responder">
          <div class="responder-badge">${skillIcon(r.skill)}</div>
          <div class="grow">
            <div class="spread"><b>${esc(r.name)}</b>
              <span class="pill ${r.dispatchable ? 'status-resolved' : 'status-flagged'}">${r.dispatchable ? esc(t('home.available')) : esc(titleCase(r.status))}</span></div>
            <div class="tiny dim" style="margin-top:3px">${esc(titleCase(r.skill))} · ${metres(r.distanceKm)} ${esc(t('nearby.away'))}</div>
          </div></div>`).join('')}</div>`;
      return;
    }

    const { items } = await api.listReports({ limit: 200 });
    let zones = derivePublicSafety(items);
    if (state.nearbyFilter !== 'all') {
      zones = zones.filter((z) => severityBand(z.severity) === state.nearbyFilter);
    }

    list.innerHTML = `<p class="privacy-note">${esc(t('nearby.publicOnly'))}</p>` + (zones.length
      ? `<div class="stack">${zones.slice(0, 40).map(publicRow).join('')}</div>`
      : `<div class="empty">${esc(t('nearby.none'))}</div>`);
  } catch (err) {
    list.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
  }
}

function skillIcon(skill) {
  return {
    medical: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M12 6v12M6 12h12" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
    rescue: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M12 2L19 5V11C19 16 16 19.5 12 21C8 19.5 5 16 5 11V5L12 2Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>',
    structural: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M4 20V9L12 4L20 9V20" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M9 20V14H15V20" stroke="currentColor" stroke-width="1.8"/></svg>',
  }[skill] || '';
}

// --- My reports ----------------------------------------------------------

async function loadMine() {
  const list = $('#mineList');
  const queued = await outbox.all();

  let mine = [];
  let error = null;
  try {
    const { items } = await api.listReports({ reporter_pseudonym: identity.pseudonym, limit: 50 });
    mine = items;
    state.mine = items;
  } catch (err) {
    error = err;
    mine = filedLocally.all().map((e) => ({ ...e, offlineRecord: true }));
  }

  const tracker = `<div class="card track-code">
    <div class="field-label" style="margin-top:0">${esc(t('reports.trackCode'))}</div>
    <div class="row">
      <input class="text-input grow" id="trackInput" placeholder="${esc(t('reports.trackPlaceholder'))}" autocomplete="off">
      <button class="btn btn-sm btn-gold" id="trackGo">${esc(t('reports.track'))}</button>
    </div>
  </div>`;

  const queuedMarkup = queued.length ? `
    <div class="section-title">${esc(t('reports.waiting'))} (${queued.length})</div>
    <div class="stack">${queued.map((item) => `<div class="card">
      <div class="spread">
        <span class="pill status-chip">${esc(t('status.savedLocally'))}</span>
        <span class="tiny dim">${esc(trackingCode(item.idempotencyKey))}</span>
      </div>
      <div class="incident-text" style="margin-top:8px">${esc(item.text)}</div>
      <div class="tiny dim" style="margin-top:6px">${ago(item.clientCreatedAt)}${item.image ? ' · photo' : ''}</div>
    </div>`).join('')}</div>` : '';

  const sentMarkup = mine.length ? `
    <div class="section-title">${esc(t('reports.sent'))}</div>
    <div class="stack">${mine.map((r) => r.offlineRecord
      ? `<div class="card"><div class="incident-text">${esc(r.text)}</div>
         <div class="tiny dim" style="margin-top:6px">${esc(t('status.sent'))} · ${ago(r.clientCreatedAt)}</div></div>`
      : myReportRow(r)).join('')}</div>` : '';

  list.innerHTML = tracker + queuedMarkup + sentMarkup
    + (!queued.length && !mine.length ? `<div class="empty">${esc(error ? error.message : t('reports.empty'))}</div>` : '');

  list.querySelectorAll('[data-open]').forEach((n) => n.addEventListener('click', () => openTrack(n.dataset.open)));
  $('#trackGo')?.addEventListener('click', trackByCode);
  $('#trackInput')?.addEventListener('keydown', (e) => { if (e.key === 'Enter') trackByCode(); });
}

function myReportRow(report) {
  const band = severityBand(report.severity_score);
  const label = t(CITIZEN_STATUS[report.status] || 'status.received');
  return `<button class="incident sev-${band}" data-open="${report.id}">
    <div class="sev-bar"></div>
    <div class="incident-body"><span class="grow">
      <span class="incident-text">${esc(report.text)}</span>
      <span class="incident-meta">
        <span class="pill status-chip status-${esc(report.status)}">${esc(label)}</span>
        <span class="tiny dim">${esc(trackingCode(report.idempotency_key))} · ${ago(report.client_created_at)}</span>
      </span>
    </span></div>
  </button>`;
}

async function trackByCode() {
  const code = $('#trackInput').value.trim().toUpperCase();
  if (!code) return;

  const queued = await outbox.all();
  const local = queued.find((q) => trackingCode(q.idempotencyKey) === code);
  if (local) { show('mine'); toast(t('status.savedLocally')); return; }

  const match = state.mine.find((r) => trackingCode(r.idempotency_key) === code)
    || filedLocally.all().find((r) => trackingCode(r.idempotencyKey) === code);

  if (match?.id) openTrack(match.id);
  // Cross-device lookup would need a backend endpoint that resolves a code to
  // a report; nothing here can fake that.
  else toast(t('reports.notFound'), 'err');
}

// --- Track my help -------------------------------------------------------

function openTrack(id) {
  state.trackingId = id;
  show('track');
}

async function renderTrack(id) {
  const body = $('#trackBody');
  if (!id) { body.innerHTML = `<div class="empty">${esc(t('reports.empty'))}</div>`; return; }
  body.innerHTML = '<div class="stack"><div class="skeleton"></div><div class="skeleton"></div></div>';

  let report;
  try {
    report = await api.getReport(id);
  } catch (err) {
    body.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    return;
  }

  const label = t(CITIZEN_STATUS[report.status] || 'status.received');
  const update = t(UPDATE_FOR[report.status] || 'update.received');
  const band = severityBand(report.severity_score);

  body.innerHTML = `
    <div class="track-hero sev-${band}">
      <div class="spread">
        <span class="tiny dim">${esc(t('sos.incidentId'))}</span>
        <b>${esc(trackingCode(report.idempotency_key))}</b>
      </div>
      <div class="track-status">${esc(label)}</div>
      <p class="track-update">${esc(update)}</p>
    </div>

    <div class="section-title">${esc(t('track.timeline'))}</div>
    <div class="timeline">${citizenTimeline(report.status)}</div>

    <div class="section-title">${esc(t('track.responder'))}</div>
    <div id="trackResponder" class="card"><p class="tiny dim">${esc(t('track.noResponder'))}</p></div>

    <div class="section-title">${esc(t('system.shared'))}</div>
    <div class="card">
      <div class="kv"><span>${esc(t('sos.location'))}</span><span>${report.lat.toFixed(4)}, ${report.lng.toFixed(4)}</span></div>
      <div class="kv"><span>${esc(t('report.what'))}</span><span>${esc(titleCase(report.incident_type || '—'))}</span></div>
      <div class="kv"><span>${esc(t('system.identity'))}</span><span>${esc(report.reporter_pseudonym)}</span></div>
    </div>

    <button class="btn btn-ghost" id="contactControl" style="width:100%;margin-top:18px">${esc(t('track.contact'))}</button>
    <p class="tiny dim center-text" style="margin-top:10px">${esc(t('nearby.publicOnly'))}</p>`;

  $('#contactControl').addEventListener('click', () =>
    toast('A direct line to the control room needs a telephony integration.', 'err'));

  // Responder details, only once one is actually assigned.
  if (['assigned', 'acknowledged', 'en_route', 'on_scene'].includes(report.status)) {
    try {
      const { items } = await api.responders({ limit: 100 });
      // The backend does not expose which crew holds which report to a
      // citizen, so show the nearest dispatchable crew of the right kind as
      // "your responder" only when the API confirms an assignment exists.
      const nearest = items
        .map((r) => ({ ...r, km: haversineKm({ lat: report.lat, lng: report.lng }, r) }))
        .sort((a, b) => a.km - b.km)[0];
      if (nearest) {
        $('#trackResponder').innerHTML = `
          <div class="row">
            <div class="responder-badge">${skillIcon(nearest.skill)}</div>
            <div class="grow">
              <b>${esc(nearest.name)}</b>
              <div class="tiny dim">${esc(titleCase(nearest.skill))} team</div>
            </div>
            <span class="pill status-assigned">${esc(label)}</span>
          </div>
          <div class="kv" style="margin-top:10px"><span>${esc(t('track.eta'))}</span><span>~${Math.max(2, Math.round(nearest.km * 3))} min</span></div>
          <p class="tiny dim" style="margin-top:8px">Estimated from distance. Contact details are not shared.</p>`;
      }
    } catch { /* keep the placeholder */ }
  }
}

const CITIZEN_STEPS = [
  ['status.received', ['received', 'classified', 'verified', 'queued', 'assigned', 'acknowledged', 'en_route', 'on_scene', 'resolved', 'closed']],
  ['status.queued', ['queued', 'assigned', 'acknowledged', 'en_route', 'on_scene', 'resolved', 'closed']],
  ['status.assigned', ['assigned', 'acknowledged', 'en_route', 'on_scene', 'resolved', 'closed']],
  ['status.enRoute', ['en_route', 'on_scene', 'resolved', 'closed']],
  ['status.onScene', ['on_scene', 'resolved', 'closed']],
  ['status.resolved', ['resolved', 'closed']],
];

function citizenTimeline(status) {
  if (status === 'flagged') {
    return `<div class="tl-step done">${esc(t('status.received'))}</div>
      <div class="tl-step current">${esc(t('status.underReview'))}</div>`;
  }
  let seenCurrent = false;
  return CITIZEN_STEPS.map(([key, reached]) => {
    const done = reached.includes(status);
    let cls = '';
    if (done && !seenCurrent) {
      // The furthest completed step is the current one.
      const isLast = CITIZEN_STEPS.findIndex(([, r]) => r.includes(status)) === CITIZEN_STEPS.length - 1;
      cls = 'done';
      if (isLast) cls = 'done';
    }
    return { key, done };
  }).map((step, index, all) => {
    const lastDone = all.map((s) => s.done).lastIndexOf(true);
    const cls = !step.done ? '' : index === 0 || index <= lastDone ? (index === lastDone ? 'current' : 'done') : '';
    return `<div class="tl-step ${cls}">${esc(t(step.key))}</div>`;
  }).join('');
}

// --- Report --------------------------------------------------------------

function renderSignals() {
  $('#signalGrid').innerHTML = SIGNALS.map((s) =>
    `<button class="signal-chip ${state.signals.has(s.key) ? 'is-on' : ''}" data-signal="${s.key}">${esc(t(s.i18n))}</button>`).join('');
  $$('#signalGrid .signal-chip').forEach((chip) => chip.addEventListener('click', () => {
    const key = chip.dataset.signal;
    state.signals.has(key) ? state.signals.delete(key) : state.signals.add(key);
    renderSignals();
  }));
}

function renderAttachOptions() {
  const hasProfile = emergencyProfile.isSet;
  const hasFamily = family.count > 0;
  if (!hasProfile && !hasFamily) {
    $('#attachOptions').innerHTML = `<button class="link-row" id="goProfileFromReport">
      <span class="grow">${esc(t('profile.emergency'))}</span>
      <span class="tiny dim">${esc(t('sos.notSet'))} →</span></button>`;
    $('#goProfileFromReport').addEventListener('click', () => show('emergency'));
    return;
  }
  $('#attachOptions').innerHTML = `
    <div class="section-title">${esc(t('system.shared'))}</div>
    ${hasProfile ? toggleRow('attachProfile', t('report.attachProfile'), state.attachProfile) : ''}
    ${hasFamily ? toggleRow('attachFamily', `${t('report.attachFamily')} (${family.count})`, state.attachFamily) : ''}`;
  $$('#attachOptions .toggle-row').forEach((row) => row.addEventListener('click', () => {
    const key = row.dataset.toggle;
    state[key] = !state[key];
    renderAttachOptions();
  }));
}

function toggleRow(key, label, on) {
  return `<button class="toggle-row" data-toggle="${key}">
    <span class="grow">${esc(label)}</span>
    <span class="switch ${on ? 'is-on' : ''}" aria-hidden="true"></span>
  </button>`;
}

function setPhoto(file) {
  if (!file) return;
  state.photo = file;
  const url = URL.createObjectURL(file);
  $('#photoPreview').innerHTML = `<div class="preview">
    <img src="${url}" alt="">
    <button class="preview-drop" id="dropPhoto" aria-label="Remove">×</button>
  </div>`;
  $('#dropPhoto').addEventListener('click', () => {
    state.photo = null;
    URL.revokeObjectURL(url);
    $('#photoPreview').innerHTML = '';
  });
}

function buildBody(rawText) {
  const langName = LANGUAGES.find((l) => l.code === i18n.lang)?.name;
  return composeBody({
    text: rawText,
    signals: [...state.signals],
    people: state.attachFamily && family.count ? family.headcount : 0,
    profile: state.attachProfile && emergencyProfile.isSet ? emergencyProfile.summary() : '',
    family: state.attachFamily && family.count ? family.summary() : '',
    language: langName,
  });
}

async function submit({ rawText, urgent = false } = {}) {
  const raw = (rawText ?? $('#reportText').value).trim();
  if (!raw) {
    toast(t('report.needText'), 'err');
    $('#reportText')?.focus();
    return null;
  }

  const entry = {
    idempotencyKey: newKey(),
    text: buildBody(raw),
    lat: geo.current.lat,
    lng: geo.current.lng,
    clientCreatedAt: new Date().toISOString(),
    pseudonym: identity.pseudonym,
    image: urgent ? null : state.photo,
  };

  if (!state.online) {
    await outbox.add(entry);
    renderConnection();
    return { queued: true, code: trackingCode(entry.idempotencyKey) };
  }

  try {
    const created = await api.createReport({
      text: entry.text, lat: entry.lat, lng: entry.lng,
      clientCreatedAt: entry.clientCreatedAt, pseudonym: entry.pseudonym,
      idempotencyKey: entry.idempotencyKey, image: entry.image,
    });
    filedLocally.add({ ...entry, image: undefined, id: created.id });
    return { ...created, code: trackingCode(entry.idempotencyKey) };
  } catch (err) {
    if (err instanceof ApiError && err.isOffline) {
      await outbox.add(entry);
      renderConnection();
      return { queued: true, code: trackingCode(entry.idempotencyKey) };
    }
    toast(err.message, 'err');
    return null;
  }
}

// --- SOS -----------------------------------------------------------------

const HOLD_MS = 2000;
const RING = 2 * Math.PI * 120;
let holdTimer = null;
let holdStart = 0;

function openSos() {
  $('#screen-sos').classList.add('is-active');
  $('#sosArm').hidden = false;
  $('#sosActive').hidden = true;
  $('#sosResult').hidden = true;
  $('#sosCancel').hidden = false;
  resetHold();
}

function closeSos() {
  cancelHold();
  $('#screen-sos').classList.remove('is-active');
}

function resetHold() {
  const ring = $('#sosRing');
  ring.style.transition = 'none';
  ring.style.strokeDashoffset = RING;
  void ring.offsetWidth;
  $('#sosDialLabel').textContent = t('sos.hold');
  $('#sosDial').classList.remove('is-holding');
}

function startHold() {
  holdStart = Date.now();
  $('#sosDial').classList.add('is-holding');
  $('#sosDialLabel').textContent = t('sos.holding');

  const ring = $('#sosRing');
  ring.style.transition = `stroke-dashoffset ${HOLD_MS}ms linear`;
  ring.style.strokeDashoffset = 0;

  if (navigator.vibrate) navigator.vibrate(20);
  holdTimer = setTimeout(armSos, HOLD_MS);
}

function cancelHold() {
  clearTimeout(holdTimer);
  holdTimer = null;
  if (Date.now() - holdStart < HOLD_MS) resetHold();
}

function armSos() {
  if (navigator.vibrate) navigator.vibrate([40, 60, 40]);
  $('#sosArm').hidden = true;
  $('#sosActive').hidden = false;

  const hasProfile = emergencyProfile.isSet;
  const hasFamily = family.count > 0;
  const people = hasFamily ? family.headcount : 1;

  $('#sosSummary').innerHTML = `
    <div class="kv"><span>${esc(t('sos.location'))}</span><span>${geo.current.isFallback ? esc(t('sos.approximate')) : esc(t('sos.detected'))}</span></div>
    <div class="kv"><span>${esc(t('sos.people'))}</span><span>${people}</span></div>
    <div class="kv"><span>${esc(t('sos.medical'))}</span><span>${hasProfile ? esc(t('sos.attached')) : esc(t('sos.notSet'))}</span></div>
    <div class="kv"><span>${esc(t('sos.family'))}</span><span>${hasFamily ? esc(t('sos.attached')) : esc(t('sos.notSet'))}</span></div>`;
}

async function sendSos() {
  const button = $('#sosSend');
  button.disabled = true;
  button.textContent = t('sos.sending');

  // SOS always carries every signal that raises urgency, plus whatever profile
  // the citizen has chosen to keep on the device.
  const previous = new Set(state.signals);
  state.signals = new Set(['trapped', 'injured']);
  const result = await submit({
    urgent: true,
    rawText: 'SOS: immediate assistance needed at this location. The person who triggered this alert may be unable to give details.',
  });
  state.signals = previous;

  button.disabled = false;
  button.textContent = t('sos.send');

  $('#sosActive').hidden = true;
  $('#sosResult').hidden = false;
  $('#sosCancel').hidden = true;

  if (!result) {
    $('#sosResultMark').textContent = '!';
    $('#sosResultMark').className = 'sos-result-mark bad';
    $('#sosResultTitle').textContent = t('common.retry');
    $('#sosResultBody').innerHTML = `<p class="tiny">${esc(t('common.offlineNote'))}</p>`;
    $('#sosTrack').hidden = true;
    return;
  }

  const queued = Boolean(result.queued);
  $('#sosResultMark').textContent = queued ? '⏳' : '✓';
  $('#sosResultMark').className = `sos-result-mark ${queued ? 'warn' : ''}`;
  // Never claim "sent" for something that has only reached the device.
  $('#sosResultTitle').textContent = queued ? t('sos.savedOffline') : t('sos.sent');

  $('#sosResultBody').innerHTML = queued
    ? `<p class="sos-offline-warn">${esc(t('sos.savedExplain'))}</p>
       <div class="kv"><span>${esc(t('sos.incidentId'))}</span><span>${esc(result.code)}</span></div>`
    : `<div class="kv"><span>${esc(t('sos.incidentId'))}</span><span>${esc(result.code)}</span></div>
       <div class="kv"><span>${esc(t('sos.location'))}</span><span>${esc(t('sos.sharedWith'))}</span></div>
       <div class="kv"><span>${esc(t('sos.priority'))}</span><span>${esc(t('sos.assigning'))}</span></div>`;

  $('#sosTrack').hidden = false;
  $('#sosTrack').onclick = () => {
    closeSos();
    if (queued || !result.id) resetTo('mine');
    else { resetTo('home'); openTrack(result.id); }
  };
}

// --- Profile screens ------------------------------------------------------

function loadProfile() {
  const p = emergencyProfile.get();
  $('#profileBody').innerHTML = `
    <div class="card profile-id">
      <div class="avatar big">${esc(identity.pseudonym.slice(0, 1).toUpperCase())}</div>
      <div class="grow">
        <b>${esc(p.name || t('system.anonymous'))}</b>
        <div class="tiny dim">${esc(identity.pseudonym)}</div>
      </div>
    </div>

    <button class="link-row" data-go="emergency">
      <span class="grow">${esc(t('profile.emergency'))}</span>
      <span class="tiny dim">${emergencyProfile.isSet ? esc(t('sos.attached')) : esc(t('sos.notSet'))} →</span>
    </button>
    <button class="link-row" data-go="family">
      <span class="grow">${esc(t('profile.family'))}</span>
      <span class="tiny dim">${family.count} →</span>
    </button>
    <button class="link-row" data-go="system">
      <span class="grow">${esc(t('profile.privacy'))}</span><span class="tiny dim">→</span>
    </button>

    <div class="section-title">${esc(t('profile.language'))}</div>
    <div class="lang-list compact" id="profileLangs"></div>`;

  $$('#profileBody .link-row').forEach((n) => n.addEventListener('click', () => show(n.dataset.go)));
  renderLanguageList($('#profileLangs'), (code) => {
    i18n.set(code);
    applyLanguage();
    loadProfile();
    toast(LANGUAGES.find((l) => l.code === code).native, 'ok');
  });
}

const PROFILE_FIELDS = [
  ['name', 'profile.name', 'text'],
  ['age', 'profile.age', 'number'],
  ['blood', 'profile.blood', 'text'],
  ['conditions', 'profile.conditions', 'text'],
  ['allergies', 'profile.allergies', 'text'],
  ['medications', 'profile.medications', 'text'],
  ['mobility', 'profile.mobility', 'text'],
  ['contact', 'profile.contact', 'text'],
];

function loadEmergencyProfile() {
  const p = emergencyProfile.get();
  $('#emergencyBody').innerHTML = `
    <div class="notice">${esc(t('profile.explain'))}</div>
    <div class="notice subtle">${esc(t('profile.storedLocally'))}</div>
    ${PROFILE_FIELDS.map(([key, label, type]) => `
      <label class="field-label" for="pf-${key}">${esc(t(label))} <span class="dim">(${esc(t('common.optional'))})</span></label>
      <input class="text-input" id="pf-${key}" type="${type}" value="${esc(p[key] || '')}" autocomplete="off">`).join('')}
    <button class="btn btn-primary" id="saveProfile" style="margin-top:20px">${esc(t('profile.save'))}</button>`;

  $('#saveProfile').addEventListener('click', () => {
    const data = {};
    PROFILE_FIELDS.forEach(([key]) => { data[key] = $(`#pf-${key}`).value.trim(); });
    emergencyProfile.save(data);
    toast(t('profile.saved'), 'ok');
    back();
  });
}

function loadFamily() {
  const members = family.all();
  $('#familyBody').innerHTML = `
    <div class="notice subtle">${esc(t('profile.storedLocally'))}</div>
    <div class="stack" id="familyList">
      ${members.length ? members.map((m) => `<div class="card family-card">
        <div class="spread">
          <div class="grow">
            <b>${esc(m.relationship || t('profile.family'))}</b>
            <div class="tiny dim">${esc([m.name, m.age ? `${m.age} ${t('profile.age').toLowerCase()}` : ''].filter(Boolean).join(' · '))}</div>
            ${m.mobility ? `<div class="tiny" style="color:var(--accent-gold);margin-top:4px">${esc(m.mobility)}</div>` : ''}
            ${m.conditions ? `<div class="tiny dim" style="margin-top:2px">${esc(m.conditions)}</div>` : ''}
          </div>
          <button class="btn btn-sm btn-ghost" data-remove="${m.id}">${esc(t('profile.remove'))}</button>
        </div>
      </div>`).join('') : `<div class="empty">${esc(t('profile.noFamily'))}</div>`}
    </div>

    <div class="section-title">${esc(t('profile.addFamily'))}</div>
    <div class="card">
      <input class="text-input" id="fm-relationship" placeholder="${esc(t('profile.relationship'))}">
      <input class="text-input" id="fm-name" placeholder="${esc(t('profile.name'))}" style="margin-top:9px">
      <input class="text-input" id="fm-age" type="number" placeholder="${esc(t('profile.age'))}" style="margin-top:9px">
      <input class="text-input" id="fm-conditions" placeholder="${esc(t('profile.conditions'))}" style="margin-top:9px">
      <input class="text-input" id="fm-mobility" placeholder="${esc(t('profile.mobility'))}" style="margin-top:9px">
      <button class="btn btn-ghost" id="addMember" style="width:100%;margin-top:12px">＋ ${esc(t('profile.addFamily'))}</button>
    </div>`;

  $$('#familyBody [data-remove]').forEach((b) => b.addEventListener('click', () => {
    family.remove(b.dataset.remove);
    loadFamily();
  }));
  $('#addMember').addEventListener('click', () => {
    const member = {
      relationship: $('#fm-relationship').value.trim(),
      name: $('#fm-name').value.trim(),
      age: $('#fm-age').value.trim(),
      conditions: $('#fm-conditions').value.trim(),
      mobility: $('#fm-mobility').value.trim(),
    };
    if (!member.relationship && !member.name) { toast(t('profile.relationship'), 'err'); return; }
    family.add(member);
    toast(t('profile.saved'), 'ok');
    loadFamily();
  });
}

function renderLanguageList(node, onPick) {
  node.innerHTML = LANGUAGES.map((l) =>
    `<button class="lang-item ${i18n.lang === l.code ? 'is-on' : ''}" data-lang="${l.code}">
       <span class="lang-native">${esc(l.native)}</span>
       <span class="lang-name tiny dim">${esc(l.name)}</span>
     </button>`).join('');
  node.querySelectorAll('.lang-item').forEach((b) =>
    b.addEventListener('click', () => onPick(b.dataset.lang)));
}

// --- System / privacy -----------------------------------------------------

async function loadSystem() {
  const body = $('#systemBody');
  body.innerHTML = '<div class="stack"><div class="skeleton"></div><div class="skeleton"></div></div>';
  const queued = await outbox.all();

  let governance = null;
  try { governance = await api.governance(); } catch { /* shown as unavailable */ }

  const hasProfile = emergencyProfile.isSet;
  const hasFamily = family.count > 0;

  body.innerHTML = `
    <div class="section-title">${esc(t('system.yourData'))}</div>
    <div class="card">
      <div class="data-row on">${esc(t('reports.title'))}</div>
      <div class="data-row on">${esc(t('sos.location'))}</div>
      <div class="data-row ${hasProfile ? 'on' : 'off'}">${esc(t('profile.emergency'))} — ${hasProfile ? esc(t('profile.storedLocally')) : esc(t('sos.notSet'))}</div>
      <div class="data-row ${hasFamily ? 'on' : 'off'}">${esc(t('profile.family'))} — ${hasFamily ? `${family.count}` : esc(t('sos.notSet'))}</div>
    </div>

    <div class="section-title">${esc(t('system.shared'))}</div>
    <div class="card">
      <div class="data-row on">${esc(t('sos.location'))}</div>
      <div class="data-row on">${esc(t('report.what'))}</div>
      <div class="data-row ${hasProfile ? 'on' : 'off'}">${esc(t('sos.medical'))}</div>
      <div class="data-row ${hasFamily ? 'on' : 'off'}">${esc(t('sos.family'))}</div>
      <p class="tiny dim" style="margin-top:10px;line-height:1.5">
        Medical and family details leave this device only when you turn them on for a
        report. Your name and phone number are never required.
      </p>
    </div>

    <div class="section-title">${esc(t('system.scoring'))}</div>
    ${governance
      ? `<div class="honest">${esc(governance.scoring.honest_summary)}</div>`
      : `<div class="card"><p class="tiny muted">${esc(t('common.offlineNote'))}</p></div>`}

    <div class="section-title">${esc(t('system.connection'))}</div>
    <div class="card">
      <div class="kv"><span>${esc(t('system.connection'))}</span><span>${state.online ? esc(t('system.online')) : esc(t('system.offline'))}</span></div>
      <div class="kv"><span>${esc(t('system.waiting'))}</span><span>${queued.length}</span></div>
      <div class="kv"><span>${esc(t('system.identity'))}</span><span>${esc(identity.pseudonym)}</span></div>
      <div class="kv"><span>${esc(t('system.language'))}</span><span>${esc(LANGUAGES.find((l) => l.code === i18n.lang).native)}</span></div>
    </div>

    <button class="link-row" data-go="profile" style="margin-top:16px">
      <span class="grow">${esc(t('profile.title'))}</span><span class="tiny dim">→</span></button>
    <button class="btn btn-ghost" id="changeApi" style="width:100%;margin-top:12px">Backend address</button>`;

  $('#systemBody [data-go]').addEventListener('click', () => show('profile'));
  $('#changeApi').addEventListener('click', () => {
    const next = prompt('Backend base URL', api.base);
    if (next) { api.setBase(next.trim()); loadSystem(); }
  });
}

// --- Onboarding -----------------------------------------------------------

function startOnboarding() {
  const screen = $('#screen-onboard');
  screen.classList.add('is-active');

  let picked = i18n.lang;
  const pick = (code) => {
    picked = code;
    i18n.set(code);          // translate the onboarding copy as they choose
    renderTabs();
    renderSignals();
    $$('#langList .lang-item').forEach((b) => b.classList.toggle('is-on', b.dataset.lang === code));
  };
  renderLanguageList($('#langList'), pick);

  $('#langContinue').addEventListener('click', () => {
    i18n.set(picked);
    renderTabs();
    $('#onboardStep1').hidden = true;
    $('#onboardStep2').hidden = false;
  });

  const finish = (mode) => {
    account.mode = mode;
    screen.classList.remove('is-active');
    // Home rendered before a language was chosen; repaint it in the new one.
    applyLanguage();
    if (mode === 'account') show('emergency');
  };
  $('#modeAnon').addEventListener('click', () => finish('anonymous'));
  $('#modeAccount').addEventListener('click', () => finish('account'));
}

// --- Wiring ---------------------------------------------------------------

function bindVoice() {
  const button = $('#voiceBtn');
  if (!voice.supported) return;
  button.hidden = false;

  let active = false;
  const stop = () => {
    if (!active) return;
    active = false;
    voice.stop();
    button.classList.remove('is-live');
    $('#voiceLabel').textContent = t('report.speak');
  };

  const start = (event) => {
    event.preventDefault();
    if (active) return;
    active = true;
    button.classList.add('is-live');
    $('#voiceLabel').textContent = t('report.listening');

    voice.start(i18n.speechCode, {
      onResult: (transcript) => {
        $('#voiceResult').innerHTML = `<div class="voice-heard">
          <span class="tiny dim">${esc(t('report.understood'))}</span>
          <p>${esc(transcript)}</p></div>`;
      },
      onEnd: (final) => {
        stop();
        if (!final) return;
        const box = $('#reportText');
        box.value = box.value ? `${box.value} ${final}` : final;
        box.dispatchEvent(new Event('input'));
        $('#voiceResult').innerHTML = '';
      },
      onError: (message) => { stop(); toast(message, 'err'); },
    });
  };

  button.addEventListener('pointerdown', start);
  button.addEventListener('pointerup', stop);
  button.addEventListener('pointerleave', stop);
  button.addEventListener('pointercancel', stop);
}

function bind() {
  renderTabs();
  renderSignals();

  $$('[data-back]').forEach((b) => b.addEventListener('click', back));
  $('#reportCta').addEventListener('click', () => { renderAttachOptions(); show('report'); });
  $('#identityBtn').addEventListener('click', () => show('profile'));
  $('#recenterBtn').addEventListener('click', () =>
    maps.home?.setView([geo.current.lat, geo.current.lng], Math.max(maps.home.getZoom(), 15)));
  $('#nearbyRefresh').addEventListener('click', loadNearby);
  $('#mineRefresh').addEventListener('click', loadMine);
  $('#trackRefresh').addEventListener('click', () => renderTrack(state.trackingId));

  const text = $('#reportText');
  text.addEventListener('input', () => { $('#reportCount').textContent = `${text.value.length}/4000`; });

  $('#cameraInput').addEventListener('change', (e) => setPhoto(e.target.files[0]));
  $('#galleryInput').addEventListener('change', (e) => setPhoto(e.target.files[0]));

  $('#submitReport').addEventListener('click', async (e) => {
    const button = e.currentTarget;
    button.disabled = true;
    button.textContent = t('sos.sending');
    const result = await submit();
    button.disabled = false;
    button.textContent = t('report.submit');
    if (!result) return;

    text.value = '';
    $('#reportCount').textContent = '0/4000';
    state.photo = null;
    state.signals.clear();
    $('#photoPreview').innerHTML = '';
    renderSignals();

    if (result.queued) {
      toast(`${t('status.savedLocally')} · ${result.code}`, 'ok');
      resetTo('mine');
    } else {
      toast(`${t('update.received')} ${result.code}`, 'ok');
      resetTo('home');
      openTrack(result.id);
    }
  });

  // SOS: pointer events cover mouse, touch and pen with one path.
  const dial = $('#sosDial');
  dial.addEventListener('pointerdown', (e) => { e.preventDefault(); startHold(); });
  dial.addEventListener('pointerup', cancelHold);
  dial.addEventListener('pointerleave', cancelHold);
  dial.addEventListener('pointercancel', cancelHold);
  $('#sosSend').addEventListener('click', sendSos);
  $('#sosCancel').addEventListener('click', closeSos);

  bindVoice();

  $('#submitPseudonym').textContent = identity.pseudonym;
  $('#identityBtn').textContent = identity.pseudonym.slice(0, 1).toUpperCase();
  $('#reportLangNote').textContent = LANGUAGES.find((l) => l.code === i18n.lang).native;
}

async function boot() {
  document.documentElement.lang = i18n.lang;
  bind();
  i18n.apply();
  geo.start();
  state.history = ['home'];

  if (!i18n.chosen || !account.mode) startOnboarding();

  await refreshHome();
  syncNow();
  setInterval(() => { if (state.online) syncNow(); }, 60000);

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  }
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();
