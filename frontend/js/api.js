/* Client for the RescueNet backend.
 *
 * One place that knows the wire format, so nothing else has to guess. Every
 * failure surfaces as an ApiError carrying the backend's typed error code
 * (TRD §6), because the UI needs to distinguish "you sent bad coordinates"
 * from "the server is unreachable" — the first is the user's to fix, the
 * second is ours to retry.
 */

const DEFAULT_BASE = (() => {
  const stored = localStorage.getItem('resq.apiBase');
  if (stored) return stored;
  // Served from the same origin as the API (a single deployment), or from a
  // static server during development while the API runs on 8000.
  const { protocol, hostname, port } = window.location;
  if (port && port !== '8000') return `${protocol}//${hostname}:8000`;
  return window.location.origin;
})();

export class ApiError extends Error {
  constructor(message, { code = 'NETWORK_ERROR', status = 0, detail = {} } = {}) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.detail = detail;
  }
  get isOffline() {
    return this.code === 'NETWORK_ERROR';
  }
}

export const api = {
  base: DEFAULT_BASE,

  setBase(url) {
    this.base = url.replace(/\/+$/, '');
    localStorage.setItem('resq.apiBase', this.base);
  },

  url(path) {
    return `${this.base}${path}`;
  },

  async request(path, { method = 'GET', body, headers = {}, timeout = 15000 } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);

    let response;
    try {
      response = await fetch(this.url(path), {
        method,
        body,
        headers,
        signal: controller.signal,
      });
    } catch (err) {
      // A refused connection and an aborted timeout are the same thing to the
      // caller: the backend could not be reached, so queue rather than fail.
      throw new ApiError(
        err.name === 'AbortError' ? 'The request timed out' : 'Cannot reach the server',
        { code: 'NETWORK_ERROR' },
      );
    } finally {
      clearTimeout(timer);
    }

    if (response.status === 204) return null;

    const isJson = (response.headers.get('content-type') || '').includes('json');
    const payload = isJson ? await response.json().catch(() => null) : await response.text();

    if (!response.ok) {
      const error = payload && payload.error ? payload.error : {};
      throw new ApiError(error.message || `Request failed (${response.status})`, {
        code: error.code || 'HTTP_ERROR',
        status: response.status,
        detail: error.detail || {},
      });
    }
    return payload;
  },

  json(path, method, data) {
    return this.request(path, {
      method,
      body: JSON.stringify(data),
      headers: { 'Content-Type': 'application/json' },
    });
  },

  query(params) {
    const search = new URLSearchParams();
    Object.entries(params || {}).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') search.set(key, value);
    });
    const qs = search.toString();
    return qs ? `?${qs}` : '';
  },

  // --- System ---------------------------------------------------------

  health() {
    return this.request('/health', { timeout: 6000 });
  },
  governance() {
    return this.request('/api/governance');
  },

  // --- Reports --------------------------------------------------------

  /** Submit one report. `image` is an optional File or Blob. */
  createReport({ text, lat, lng, clientCreatedAt, pseudonym, idempotencyKey, image }) {
    const form = new FormData();
    form.append('text', text);
    form.append('lat', lat);
    form.append('lng', lng);
    if (clientCreatedAt) form.append('client_created_at', clientCreatedAt);
    if (pseudonym) form.append('reporter_pseudonym', pseudonym);
    if (idempotencyKey) form.append('idempotency_key', idempotencyKey);
    if (image) form.append('image', image, image.name || 'photo.jpg');
    // No Content-Type header: the browser must set the multipart boundary.
    return this.request('/api/reports', { method: 'POST', body: form, timeout: 30000 });
  },

  listReports(params) {
    return this.request(`/api/reports${this.query(params)}`);
  },
  getReport(id) {
    return this.request(`/api/reports/${id}`);
  },
  reviewReport(id, { decision, reviewer, note }) {
    return this.json(`/api/reports/${id}/review`, 'POST', { decision, reviewer, note });
  },

  /** Batch sync of offline-queued reports. Idempotent on the client's keys. */
  syncReports(reports, deviceId) {
    return this.json('/api/sync/reports', 'POST', { reports, device_id: deviceId }, );
  },

  // --- Queue and dispatch ----------------------------------------------

  queue(params) {
    return this.request(`/api/queue${this.query(params)}`);
  },
  override(reportId, { action, operator, rank, reason }) {
    return this.json(`/api/queue/${reportId}/override`, 'POST', { action, operator, rank, reason });
  },
  assign({ reportId, operator } = {}) {
    return this.json('/api/dispatch/assign', 'POST', { report_id: reportId, operator });
  },
  responders(params) {
    return this.request(`/api/responders${this.query(params)}`);
  },

  // --- Assignment lifecycle ---------------------------------------------

  advanceAssignment(id, { status, actor, note }) {
    return this.json(`/api/assignments/${id}/status`, 'POST', { status, actor, note });
  },
  rejectAssignment(id, { reason, actor }) {
    return this.json(`/api/assignments/${id}/reject`, 'POST', { reason, actor });
  },

  // --- Process intelligence ---------------------------------------------

  events(params) {
    return this.request(`/api/events${this.query(params)}`);
  },
  eventsCsvUrl(params) {
    return this.url(`/api/events/export.csv${this.query(params)}`);
  },
  bottlenecks() {
    return this.request('/api/mining/bottlenecks');
  },
};
