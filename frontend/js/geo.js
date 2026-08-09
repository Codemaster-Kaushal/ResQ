/* Location tracking and map rendering.
 *
 * Leaflet is vendored rather than loaded from a CDN: the whole point of the
 * offline story is that the venue's network can die and the app keeps working.
 * Map *tiles* still need the network, so a tile failure degrades to a readable
 * message instead of a blank grey square.
 */

import { severityBand } from './ui.js';

const FALLBACK = { lat: 12.9716, lng: 77.5946, accuracy: 20000, isFallback: true }; // Bengaluru

export const location = {
  current: { ...FALLBACK },
  watchId: null,
  listeners: new Set(),

  onChange(fn) {
    this.listeners.add(fn);
    fn(this.current);
    return () => this.listeners.delete(fn);
  },

  emit() {
    this.listeners.forEach((fn) => fn(this.current));
  },

  set(coords, isFallback = false) {
    this.current = {
      lat: coords.latitude ?? coords.lat,
      lng: coords.longitude ?? coords.lng,
      accuracy: Math.max(5, Math.round(coords.accuracy || 50)),
      isFallback,
      at: new Date().toISOString(),
    };
    this.emit();
  },

  start() {
    if (!navigator.geolocation) {
      this.current = { ...FALLBACK, error: 'This device cannot report a location' };
      this.emit();
      return;
    }
    const onError = (err) => {
      this.current = {
        ...FALLBACK,
        error: err && err.code === 1
          ? 'Location permission denied — using an approximate area'
          : 'Could not get a precise fix — using an approximate area',
      };
      this.emit();
    };

    navigator.geolocation.getCurrentPosition(
      (pos) => this.set(pos.coords),
      onError,
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 },
    );

    this.watchId = navigator.geolocation.watchPosition(
      (pos) => this.set(pos.coords),
      () => { /* keep the last good fix through transient watch errors */ },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 5000 },
    );
  },
};

export function userIcon() {
  return L.divIcon({
    className: '',
    html: '<div class="user-marker"><i class="pulse"></i><i class="dot"></i></div>',
    iconSize: [16, 16],
    iconAnchor: [8, 8],
  });
}

export function incidentIcon(severity) {
  return L.divIcon({
    className: '',
    html: `<div class="incident-marker sev-${severityBand(severity)}"></div>`,
    iconSize: [14, 14],
    iconAnchor: [7, 7],
  });
}

export function createMap(node, { zoom = 14, controls = true } = {}) {
  const map = L.map(node, {
    zoomControl: controls,
    attributionControl: true,
    // A pinch-zoomable map inside a scrolling page fights the page on touch.
    tap: true,
  }).setView([location.current.lat, location.current.lng], zoom);

  const tiles = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  });

  let warned = false;
  tiles.on('tileerror', () => {
    if (warned) return;
    warned = true;
    const note = document.createElement('div');
    note.className = 'map-note';
    note.textContent = 'Map tiles need a connection — incidents are still listed below.';
    node.append(note);
  });
  tiles.addTo(map);

  return map;
}
