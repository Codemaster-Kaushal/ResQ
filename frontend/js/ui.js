/* Small shared UI helpers. No framework — the app is a handful of screens and
 * a build step would cost more than it saves. */

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    else node.setAttribute(key, value);
  }
  (Array.isArray(children) ? children : [children])
    .filter(Boolean)
    .forEach((child) => node.append(child));
  return node;
}

/** Escape anything that came from a reporter before it touches innerHTML. */
export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

let toastTimer;
export function toast(message, kind = '') {
  let node = $('#toast');
  if (!node) {
    node = el('div', { id: 'toast', class: 'toast' });
    document.body.append(node);
  }
  node.textContent = message;
  node.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove('show'), 2600);
}

/** Severity 0-100 to the four-step scale used across the UI. */
export function severityBand(score) {
  if (score === null || score === undefined) return 'low';
  if (score >= 75) return 'critical';
  if (score >= 50) return 'high';
  if (score >= 25) return 'medium';
  return 'low';
}

export function severityLabel(score) {
  return { critical: 'Critical', high: 'High', medium: 'Moderate', low: 'Low' }[severityBand(score)];
}

export function titleCase(value) {
  return String(value || '')
    .toLowerCase() // SCREAMING_SNAKE would otherwise stay shouting
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/* Reason codes are the product's explanation of itself, so a citizen has to be
 * able to read them. `PEOPLE_AFFECTED_2_5` means nothing to the person who
 * filed the report; "2–5 people affected" does. */
const REASON_LABELS = {
  BASELINE: 'Starting trust score',
  CORROBORATED: 'Independent reports of the same event nearby',
  DUPLICATE_IMAGE: 'This photograph was already filed by someone else',
  EXIF_CONSISTENT: "The photo's own GPS matches the reported location",
  GEO_IMPLAUSIBLE: 'The coordinates do not look real',
  IMPOSSIBLE_MOVEMENT: 'The same reporter was far away moments earlier',
  LOW_INFORMATION: 'Too little detail to act on',
  STALE_REPORT: 'Filed long before it reached us',
  IMAGE_CORROBORATION: 'The photograph supports the description',
  IMAGE_CONTRADICTION: 'The photograph contradicts the description',
  SCORE_CLAMPED_AT_MAX: 'Capped at the maximum score',
  SCORE_CLAMPED_AT_MIN: 'Floored at the minimum score',
  HUMAN_REVIEW_VERIFIED: 'An operator confirmed this report',
  HUMAN_REVIEW_REJECTED: 'An operator closed this report',
};

const PEOPLE_BANDS = {
  PEOPLE_AFFECTED_1: '1 person affected',
  PEOPLE_AFFECTED_2_5: '2–5 people affected',
  PEOPLE_AFFECTED_6_20: '6–20 people affected',
  PEOPLE_AFFECTED_20_PLUS: 'More than 20 people affected',
};

export function reasonLabel(code) {
  if (REASON_LABELS[code]) return REASON_LABELS[code];
  if (PEOPLE_BANDS[code]) return PEOPLE_BANDS[code];
  if (code.startsWith('INCIDENT_')) return `Classified as ${titleCase(code.slice(9)).toLowerCase()}`;
  if (code.startsWith('LIFE_RISK_')) return `Life risk: ${titleCase(code.slice(10)).toLowerCase()}`;
  if (code.startsWith('VULNERABILITY_')) return `Vulnerable people: ${titleCase(code.slice(14)).toLowerCase()}`;
  return titleCase(code);
}

/** Timestamps from the API are naive UTC, so they need the Z to parse right. */
export function parseUtc(value) {
  if (!value) return null;
  const normalised = /[zZ]|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`;
  return new Date(normalised);
}

export function ago(value) {
  const date = parseUtc(value);
  if (!date) return '';
  const seconds = Math.max(0, (Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function minutesLabel(minutes) {
  if (minutes === null || minutes === undefined) return '—';
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const hours = minutes / 60;
  return hours < 24 ? `${hours.toFixed(1)} h` : `${(hours / 24).toFixed(1)} d`;
}

export function metres(km) {
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(1)} km`;
}

export function haversineKm(a, b) {
  const toRad = (deg) => (deg * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat);
  const dLng = toRad(b.lng - a.lng);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h));
}

export function debounce(fn, wait = 300) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}
