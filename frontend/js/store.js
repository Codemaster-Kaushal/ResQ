/* Local identity and the offline outbox.
 *
 * The backend can only honour "accept reports filed with no connectivity" (G5)
 * if the client actually holds onto them. This is that half: reports written
 * while offline are persisted here and replayed when the network returns.
 *
 * IndexedDB rather than localStorage because a report can carry a photograph,
 * and a single 2 MB image base64-encodes past localStorage's ~5 MB ceiling.
 */

const DB_NAME = 'resq';
const DB_VERSION = 1;
const OUTBOX = 'outbox';

let dbPromise = null;

function openDb() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(OUTBOX)) {
        db.createObjectStore(OUTBOX, { keyPath: 'idempotencyKey' });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return dbPromise;
}

async function tx(mode, run) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction(OUTBOX, mode);
    const store = transaction.objectStore(OUTBOX);
    let result;
    try {
      result = run(store);
    } catch (err) {
      reject(err);
      return;
    }
    transaction.oncomplete = () => resolve(result && result.result !== undefined ? result.result : result);
    transaction.onerror = () => reject(transaction.error);
  });
}

/** A stable pseudonymous handle. No name, phone or email — NFR-7. */
export const identity = {
  get pseudonym() {
    let value = localStorage.getItem('resq.pseudonym');
    if (!value) {
      value = `citizen-${Math.random().toString(36).slice(2, 8)}`;
      localStorage.setItem('resq.pseudonym', value);
    }
    return value;
  },
  set pseudonym(value) {
    localStorage.setItem('resq.pseudonym', value);
  },
  get deviceId() {
    let value = localStorage.getItem('resq.deviceId');
    if (!value) {
      value = `device-${Math.random().toString(36).slice(2, 10)}`;
      localStorage.setItem('resq.deviceId', value);
    }
    return value;
  },
};

export function newKey() {
  const uuid = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  return `resq-${uuid}`;
}

export const outbox = {
  async all() {
    const items = await tx('readonly', (store) => store.getAll());
    return (items || []).sort((a, b) => a.clientCreatedAt.localeCompare(b.clientCreatedAt));
  },

  async count() {
    return (await this.all()).length;
  },

  /** Queue a report for later. Returns the stored entry. */
  async add(entry) {
    const record = { attempts: 0, queuedAt: new Date().toISOString(), ...entry };
    await tx('readwrite', (store) => store.put(record));
    return record;
  },

  async remove(idempotencyKey) {
    await tx('readwrite', (store) => store.delete(idempotencyKey));
  },

  async markAttempt(idempotencyKey, error) {
    const items = await this.all();
    const found = items.find((item) => item.idempotencyKey === idempotencyKey);
    if (!found) return;
    found.attempts = (found.attempts || 0) + 1;
    found.lastError = error || null;
    await tx('readwrite', (store) => store.put(found));
  },
};

/**
 * Replay the outbox.
 *
 * Reports are split by whether they carry a photograph. Text-only reports go
 * out as one batch to /api/sync/reports; reports with an image go individually
 * through the multipart endpoint, because a JSON batch has nowhere to put the
 * file. Both paths use the same client-generated idempotency key, so a report
 * cannot be created twice no matter which route it takes or how often the sync
 * is retried.
 */
export async function flushOutbox(api) {
  const pending = await outbox.all();
  if (!pending.length) return { attempted: 0, synced: 0, failed: 0 };

  const withImage = pending.filter((item) => item.image);
  const textOnly = pending.filter((item) => !item.image);

  let synced = 0;
  let failed = 0;

  if (textOnly.length) {
    try {
      const result = await api.syncReports(
        textOnly.map((item) => ({
          idempotency_key: item.idempotencyKey,
          text: item.text,
          lat: item.lat,
          lng: item.lng,
          client_created_at: item.clientCreatedAt,
          reporter_pseudonym: item.pseudonym,
        })),
        identity.deviceId,
      );

      for (const outcome of result.results) {
        // `duplicate` means the server already has it — that is a success from
        // the queue's point of view, and exactly what a retried sync looks like.
        if (outcome.outcome === 'created' || outcome.outcome === 'duplicate') {
          await outbox.remove(outcome.idempotency_key);
          synced += 1;
        } else {
          await outbox.markAttempt(outcome.idempotency_key, outcome.error_message);
          failed += 1;
        }
      }
    } catch (err) {
      failed += textOnly.length;
      for (const item of textOnly) await outbox.markAttempt(item.idempotencyKey, err.message);
    }
  }

  for (const item of withImage) {
    try {
      await api.createReport({
        text: item.text,
        lat: item.lat,
        lng: item.lng,
        clientCreatedAt: item.clientCreatedAt,
        pseudonym: item.pseudonym,
        idempotencyKey: item.idempotencyKey,
        image: item.image,
      });
      await outbox.remove(item.idempotencyKey);
      synced += 1;
    } catch (err) {
      // A validation failure will never succeed on retry, so drop it out of the
      // queue rather than letting it block every future sync.
      if (err.status >= 400 && err.status < 500 && err.code !== 'NETWORK_ERROR') {
        await outbox.remove(item.idempotencyKey);
      } else {
        await outbox.markAttempt(item.idempotencyKey, err.message);
      }
      failed += 1;
    }
  }

  return { attempted: pending.length, synced, failed };
}

/** Reports this device has successfully filed, so "My reports" works offline. */
export const filedLocally = {
  key: 'resq.filed',
  all() {
    try {
      return JSON.parse(localStorage.getItem(this.key) || '[]');
    } catch {
      return [];
    }
  },
  add(entry) {
    const list = this.all().filter((item) => item.idempotencyKey !== entry.idempotencyKey);
    list.unshift(entry);
    localStorage.setItem(this.key, JSON.stringify(list.slice(0, 60)));
  },
};
