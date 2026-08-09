/* Emergency profile and family, held on the device.
 *
 * The backend has no profile or family model, and adding one would mean
 * storing medical records server-side — a decision with consequences well
 * beyond a hackathon. So this lives in localStorage on the citizen's own
 * device and is attached to a report only when they choose to send it.
 *
 * What gets attached is a plain-English summary appended to the report body.
 * That is not a workaround for the sake of it: the triage engine reads exactly
 * these signals (TRD §4.1 vulnerability terms — children, elderly, disabled,
 * pregnant, injured), so attaching a profile measurably raises the severity
 * score for a household that genuinely needs more help.
 */

const PROFILE_KEY = 'resq.profile';
const FAMILY_KEY = 'resq.family';
const MODE_KEY = 'resq.mode';

function read(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key) || 'null') ?? fallback;
  } catch {
    return fallback;
  }
}

export const account = {
  /** 'anonymous' | 'account' | null (not yet chosen) */
  get mode() {
    return localStorage.getItem(MODE_KEY);
  },
  set mode(value) {
    localStorage.setItem(MODE_KEY, value);
  },
  get isAnonymous() {
    return this.mode !== 'account';
  },
};

export const emergencyProfile = {
  get() {
    return read(PROFILE_KEY, {});
  },
  save(data) {
    localStorage.setItem(PROFILE_KEY, JSON.stringify(data));
  },
  clear() {
    localStorage.removeItem(PROFILE_KEY);
  },
  get isSet() {
    return Object.values(this.get()).some((v) => String(v || '').trim());
  },

  /** A sentence responders can act on. Empty when nothing is filled in. */
  summary() {
    const p = this.get();
    const bits = [];
    if (p.name) bits.push(p.name);
    if (p.age) bits.push(`${p.age} years old`);
    if (p.blood) bits.push(`blood group ${p.blood}`);
    if (p.conditions) bits.push(`medical conditions: ${p.conditions}`);
    if (p.allergies) bits.push(`allergies: ${p.allergies}`);
    if (p.medications) bits.push(`medications: ${p.medications}`);
    // Worded as "disabled ... cannot move" so the scorer's vulnerability and
    // life-risk patterns both catch it.
    if (p.mobility) bits.push(`disabled or limited mobility: ${p.mobility}`);
    if (p.contact) bits.push(`emergency contact ${p.contact}`);
    return bits.length ? `Reporter details: ${bits.join('; ')}.` : '';
  },
};

export const family = {
  all() {
    return read(FAMILY_KEY, []);
  },
  save(list) {
    localStorage.setItem(FAMILY_KEY, JSON.stringify(list));
  },
  add(member) {
    const list = this.all();
    list.push({ id: crypto.randomUUID?.() || String(Date.now()), ...member });
    this.save(list);
  },
  remove(id) {
    this.save(this.all().filter((m) => m.id !== id));
  },
  get count() {
    return this.all().length;
  },

  summary() {
    const list = this.all();
    if (!list.length) return '';
    const described = list.map((m) => {
      const bits = [m.relationship || 'family member'];
      if (m.age) bits.push(`${m.age} years old`);
      // Keep the age words the scorer recognises rather than raw numbers alone.
      if (Number(m.age) && Number(m.age) < 13) bits.push('a child');
      if (Number(m.age) >= 65) bits.push('elderly');
      if (m.conditions) bits.push(m.conditions);
      if (m.mobility) bits.push(`needs mobility assistance: ${m.mobility}`);
      if (m.needs) bits.push(m.needs);
      return bits.join(', ');
    });
    return `${list.length} family member${list.length > 1 ? 's' : ''} present — ${described.join('; ')}.`;
  },

  /** Head count used for the people-affected band. */
  get headcount() {
    return this.all().length + 1; // family plus the reporter
  },
};
